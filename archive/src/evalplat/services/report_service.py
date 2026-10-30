"""静态报告与数据导出。"""

import csv
import html
import json
import logging
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from evalplat.config import get_settings
from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.core.paths import public_basename
from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.request import Request
from evalplat.models.report import Report
from evalplat.models.run import Run
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.supplier import Supplier
from evalplat.models.task import Task
from evalplat.models.note import Note
from evalplat.services.audit_service import record_event
from evalplat.services.security_service import redact_sensitive_text, redact_sensitive_value
from evalplat.services.metrics_service import aggregate_run_metrics
from evalplat.services.comparison_service import compare_runs
from evalplat.services.history_cleanup_service import task_cleanup_in_progress


logger = logging.getLogger(__name__)


def _report_root() -> Path:
    root = Path(get_settings().report_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


_PAGE_STYLE = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:2rem;color:#17202a}
nav{display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem} nav a{color:#1677ff}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border:1px solid #d9d9d9;padding:.5rem;text-align:left;vertical-align:top}th{background:#f5f5f5}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem}.metric{border:1px solid #d9d9d9;padding:1rem}.muted{color:#666}
figure.chart{margin:1.25rem 0;padding:1rem;border:1px solid #e5e7eb;overflow:auto}figure.chart figcaption{font-weight:600;margin-bottom:.5rem}svg.chart{display:block;width:100%;min-width:560px;height:auto}svg.chart text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#4b5563;font-size:11px}svg.chart .axis{stroke:#9ca3af;stroke-width:1}svg.chart .grid{stroke:#e5e7eb;stroke-width:1}svg.chart .bar{fill:#1677ff}svg.chart .line{fill:none;stroke-width:2.5}
"""


def _page(title: str, body: str, links: dict[str, str]) -> str:
    navigation = " ".join(
        f'<a href="{html.escape(path)}">{html.escape(label)}</a>'
        for label, path in links.items()
    )
    return f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>{_PAGE_STYLE}</style></head><body><nav>{navigation}</nav>{body}</body></html>"


def _metric_cell(value: object) -> str:
    return html.escape("-" if value is None else str(value))


def _safe_html_value(value: object) -> str:
    """Render user/provider text after the same credential redaction used by APIs."""

    redacted = redact_sensitive_value(value)
    return html.escape("" if redacted is None else str(redacted))


def _chart_number(value: object) -> float | None:
    """Return a finite chart value; malformed evidence is rendered as missing."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _chart_text(value: object) -> str:
    return html.escape(str(value)[:80])


def _svg_empty(title: str, message: str = "暂无足够数据") -> str:
    return (
        f'<figure class="chart"><figcaption>{_chart_text(title)}</figcaption>'
        f'<p class="muted">{_chart_text(message)}</p></figure>'
    )


def _svg_bar_chart(
    title: str,
    values: list[tuple[object, object]],
    *,
    suffix: str = "",
) -> str:
    """Render a dependency-free vertical bar chart for an offline report."""

    points = [(str(label), number) for label, raw in values if (number := _chart_number(raw)) is not None]
    if not points:
        return _svg_empty(title)
    width, height = 760, 300
    left, right, top, bottom = 54, 20, 34, 68
    chart_width, chart_height = width - left - right, height - top - bottom
    maximum = max(max(number, 0.0) for _, number in points) or 1.0
    slot = chart_width / len(points)
    bar_width = max(8.0, min(48.0, slot * 0.62))
    grid_lines: list[str] = []
    for index in range(5):
        ratio = index / 4
        y = top + chart_height * ratio
        value = maximum * (1 - ratio)
        grid_lines.append(
            f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}"/>'
            f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end">{value:.3g}</text>'
        )
    bars: list[str] = []
    for index, (label, number) in enumerate(points):
        x = left + slot * index + (slot - bar_width) / 2
        bar_height = max(0.0, number) / maximum * chart_height
        y = top + chart_height - bar_height
        display = f"{number:.4g}{suffix}"
        bars.append(
            f'<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" rx="2">'
            f'<title>{_chart_text(label)}: {_chart_text(display)}</title></rect>'
            f'<text x="{x + bar_width / 2:.2f}" y="{height - bottom + 18}" text-anchor="middle" '
            f'transform="rotate(-35 {x + bar_width / 2:.2f} {height - bottom + 18})">{_chart_text(label)}</text>'
        )
    svg = (
        f'<svg class="chart" role="img" aria-label="{_chart_text(title)}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg"><line class="axis" x1="{left}" y1="{top + chart_height}" '
        f'x2="{width - right}" y2="{top + chart_height}"/>{"".join(grid_lines)}{"".join(bars)}</svg>'
    )
    return f'<figure class="chart"><figcaption>{_chart_text(title)}</figcaption>{svg}</figure>'


def _svg_line_chart(title: str, series: list[tuple[object, list[object]]]) -> str:
    """Render request-order time series without JavaScript or external assets."""

    normalized: list[tuple[str, list[float | None]]] = []
    for label, raw_values in series:
        values = [_chart_number(value) for value in raw_values]
        if any(value is not None for value in values):
            normalized.append((str(label), values))
    if not normalized:
        return _svg_empty(title)
    width, height = 760, 320
    left, right, top, bottom = 54, 20, 34, 52
    chart_width, chart_height = width - left - right, height - top - bottom
    all_values = [value for _, values in normalized for value in values if value is not None]
    maximum = max(all_values) or 1.0
    maximum = max(maximum, 1.0)
    max_points = max(len(values) for _, values in normalized)
    x_step = chart_width / max(max_points - 1, 1)
    grid_lines: list[str] = []
    for index in range(5):
        ratio = index / 4
        y = top + chart_height * ratio
        value = maximum * (1 - ratio)
        grid_lines.append(
            f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}"/>'
            f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end">{value:.3g}</text>'
        )
    colors = ("#1677ff", "#13a8a8", "#fa8c16", "#722ed1", "#eb2f96", "#52c41a")
    lines: list[str] = []
    legends: list[str] = []
    for index, (label, values) in enumerate(normalized):
        color = colors[index % len(colors)]
        points = []
        for point_index, value in enumerate(values):
            if value is None:
                continue
            x = left + x_step * point_index
            y = top + chart_height - (value / maximum * chart_height)
            points.append(f"{x:.2f},{y:.2f}")
        if points:
            lines.append(f'<polyline class="line" stroke="{color}" points="{" ".join(points)}"/>')
        legend_x = left + (index % 3) * 220
        legend_y = height - 20 - (index // 3) * 16
        legends.append(
            f'<line x1="{legend_x}" y1="{legend_y - 4}" x2="{legend_x + 18}" y2="{legend_y - 4}" stroke="{color}" stroke-width="3"/>'
            f'<text x="{legend_x + 24}" y="{legend_y}">{_chart_text(label)}</text>'
        )
    svg = (
        f'<svg class="chart" role="img" aria-label="{_chart_text(title)}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg"><line class="axis" x1="{left}" y1="{top + chart_height}" '
        f'x2="{width - right}" y2="{top + chart_height}"/>{"".join(grid_lines)}{"".join(lines)}{"".join(legends)}</svg>'
    )
    return f'<figure class="chart"><figcaption>{_chart_text(title)}</figcaption>{svg}</figure>'


def _csv_safe(value: object) -> object:
    """阻止常见电子表格程序把用户可控字符串解释为公式。"""

    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _atomic_write_stream(path: Path, writer, *, newline: str | None = None) -> None:
    """Write an export beside its destination and publish it atomically.

    ``writer`` receives the open temporary stream, allowing large exports to
    be produced incrementally without building one giant string first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline=newline,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Atomically publish an already-rendered text payload."""

    _atomic_write_stream(path, lambda stream: stream.write(content), newline=newline)


def list_reports(
    db: Session,
    task_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 20,
    supplier_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> tuple[list[Report], int]:
    """按版本倒序查询报告，筛选和分页均在数据库端执行。"""

    if task_id is not None and not db.get(Task, task_id):
        raise NotFoundError("任务不存在")
    stmt = select(Report)
    count_stmt = select(func.count()).select_from(Report)
    if task_id is not None:
        stmt = stmt.where(Report.task_id == task_id)
        count_stmt = count_stmt.where(Report.task_id == task_id)
    if supplier_id is not None:
        supplier_match = exists(
            select(Run.id)
            .join(ModelDeployment, ModelDeployment.id == Run.model_deployment_id)
            .join(Connection, Connection.id == ModelDeployment.connection_id)
            .where(Run.task_id == Report.task_id, Connection.supplier_id == supplier_id)
        )
        stmt = stmt.where(supplier_match)
        count_stmt = count_stmt.where(supplier_match)
    if status_filter:
        stmt = stmt.where(Report.status == status_filter)
        count_stmt = count_stmt.where(Report.status == status_filter)
    if created_from is not None:
        stmt = stmt.where(Report.created_at >= created_from)
        count_stmt = count_stmt.where(Report.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Report.created_at <= created_to)
        count_stmt = count_stmt.where(Report.created_at <= created_to)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Report.version.desc()).offset(skip).limit(limit)))
    return items, total


def get_report(db: Session, report_id: uuid.UUID) -> Report:
    """获取单个报告版本。"""

    report = db.get(Report, report_id)
    if not report:
        raise NotFoundError("报告不存在")
    return report


def _report_result(report: Report) -> dict:
    """Serialize an existing report for idempotent callers."""

    raw_paths = ((report.snapshot or {}).get("page_paths") or {}) if report.status == "completed" else {}
    paths = {
        str(key): filename
        for key, raw_path in raw_paths.items()
        if (filename := _public_report_filename(raw_path)) is not None
    } if isinstance(raw_paths, dict) else {}
    summary = _public_report_filename(report.summary_path) if report.status == "completed" else None
    details = _public_report_filename(report.details_path) if report.status == "completed" else None
    return {
        "id": report.id,
        "task_id": report.task_id,
        "version": report.version,
        "status": report.status,
        "path": summary,
        "details_path": details,
        "page_paths": paths,
        "download_url": f"/api/v1/reports/files/{quote(summary, safe='')}" if summary else None,
        "details_download_url": f"/api/v1/reports/files/{quote(details, safe='')}" if details else None,
        "created_at": report.created_at,
    }


def _public_report_filename(value: object) -> str | None:
    """Expose only a basename, never a persisted host path, at API seams."""

    if value is None:
        return None
    return public_basename(value)


def _cleanup_report_files(report: Report, root: Path) -> None:
    """Remove only the exact files owned by one report version.

    A report can fail after one or more pages have been published.  Cleanup is
    deliberately constrained to the report's versioned names and the
    configured report root so a malformed legacy snapshot cannot delete an
    unrelated file.
    """

    snapshot = report.snapshot if isinstance(report.snapshot, dict) else {}
    page_paths = snapshot.get("page_paths")
    if not isinstance(page_paths, dict):
        page_paths = {}
    expected_prefix = f"task-{report.task_id}-v{report.version}-"
    candidates = [
        report.summary_path,
        report.details_path,
        *(value for value in page_paths.values() if isinstance(value, str)),
    ]
    seen: set[Path] = set()
    for raw_path in candidates:
        if not isinstance(raw_path, str) or not raw_path:
            continue
        filename = public_basename(raw_path)
        if filename is None:
            continue
        candidate = (root / filename).resolve()
        if (
            candidate.parent != root
            or not candidate.name.startswith(expected_prefix)
            or candidate in seen
        ):
            continue
        seen.add(candidate)
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            logger.exception("failed to clean up report file", extra={"path": str(candidate)})


def _fail_report_generation(
    db: Session,
    report_id: uuid.UUID | None,
    error: BaseException,
) -> None:
    """Converge a committed ``generating`` report after an unexpected error."""

    if report_id is None:
        return
    try:
        db.rollback()
        report = db.get(Report, report_id)
        if report is None or report.status != "generating":
            return
        _cleanup_report_files(report, _report_root())
        report.status = "failed"
        report.error = redact_sensitive_text(str(error)[:500]) or "报告生成失败"
        db.commit()
    except Exception:
        # The original exception is the useful signal to the caller.  Keep the
        # best-effort convergence failure visible in logs without masking it.
        db.rollback()
        logger.exception(
            "failed to converge report generation failure",
            extra={"report_id": str(report_id)},
        )


def generate_report(db: Session, task_id: uuid.UUID, *, idempotency_key: str | None = None) -> dict:
    """Generate a versioned offline report and never strand ``generating`` rows."""

    generation_context: dict[str, uuid.UUID] = {}
    try:
        return _generate_report_impl(
            db,
            task_id,
            idempotency_key=idempotency_key,
            _generation_context=generation_context,
        )
    except Exception as exc:
        _fail_report_generation(db, generation_context.get("report_id"), exc)
        raise


def _generate_report_impl(
    db: Session,
    task_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
    _generation_context: dict[str, uuid.UUID] | None = None,
) -> dict:
    """生成不可变版本化离线报告；摘要和分析页不依赖明细页。"""
    # Serialize version allocation per task on PostgreSQL.  Without a row lock,
    # concurrent report requests can both observe the same max(version) and
    # collide on the immutable unique constraint.
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if not task:
        raise NotFoundError("任务不存在")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key 不能为空且长度不能超过 128")
        existing = db.scalar(select(Report).where(Report.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.task_id != task_id:
                raise ConflictError("Idempotency-Key 已用于其他任务")
            if existing.status != "failed":
                return _report_result(existing)
            # A failed generation is retryable by contract.  Keep the failed
            # version as immutable history, but release its operation key so a
            # retry can allocate a fresh version while retaining the same key.
            existing.idempotency_key = None
            db.flush()
    if task.status not in {"completed", "partially_completed", "failed", "cancelled"}:
        raise ConflictError("任务尚未进入终态，暂不能生成报告")
    if task_cleanup_in_progress(db, task.id):
        raise ConflictError("任务正在清理，暂不能生成报告")
    runs = list(db.scalars(select(Run).where(Run.task_id == task_id).order_by(Run.seq, Run.id)))
    version = (db.scalar(select(func.max(Report.version)).where(Report.task_id == task_id)) or 0) + 1
    root = _report_root()
    stem = f"task-{task_id}-v{version}"
    paths = {
        "index": f"{stem}-index.html",
        "summary": f"{stem}-summary.html",
        "performance": f"{stem}-performance.html",
        "quality": f"{stem}-quality.html",
        "cost": f"{stem}-cost.html",
        "errors": f"{stem}-errors.html",
        "comparison": f"{stem}-comparison.html",
        "details": f"{stem}-request-details.html",
    }
    links = {
        "首页": paths["index"],
        "摘要": paths["summary"],
        "性能": paths["performance"],
        "质量": paths["quality"],
        "成本": paths["cost"],
        "错误": paths["errors"],
        "对比": paths["comparison"],
        "请求明细": paths["details"],
    }
    run_remarks = _load_run_remarks(db, [run.id for run in runs])
    # Resolve display-only supplier context before the immutable Report row is
    # committed.  Credentials are intentionally not selected or copied, and
    # captured Run values win over current names for historical stability.
    run_contexts: dict[uuid.UUID, dict[str, str | None]] = {}
    if runs:
        context_rows = db.execute(
            select(Run.id, Supplier.name, Connection.base_url)
            .join(ModelDeployment, ModelDeployment.id == Run.model_deployment_id)
            .join(Connection, Connection.id == ModelDeployment.connection_id)
            .join(Supplier, Supplier.id == Connection.supplier_id)
            .where(Run.id.in_([run.id for run in runs]))
        ).all()
        live_contexts = {
            run_id: {"supplier_name": supplier_name, "base_url": base_url}
            for run_id, supplier_name, base_url in context_rows
        }
        for run in runs:
            snapshot = run.run_snapshot or {}
            live_context = live_contexts.get(run.id, {})
            run_contexts[run.id] = {
                "supplier_name": snapshot.get("supplier_name") or live_context.get("supplier_name"),
                "base_url": snapshot.get("base_url") or live_context.get("base_url"),
            }
    report = Report(
        task_id=task_id,
        version=version,
        status="generating",
        idempotency_key=idempotency_key,
        snapshot={
            "task_id": str(task.id),
            "task_status": task.status,
            "run_ids": [str(run.id) for run in runs],
            "task_remark": redact_sensitive_value(task.remark),
            "run_remarks": {
                str(run.id): redact_sensitive_value(run_remarks.get(run.id, []))
                for run in runs
            },
            "tls_skip_verify": any(bool((run.run_snapshot or {}).get("tls_skip_verify")) for run in runs),
            "page_paths": paths,
            "run_contexts": {
                str(run_id): dict(context) for run_id, context in run_contexts.items()
            },
        },
    )
    db.add(report)
    db.flush()
    report_id = report.id
    if _generation_context is not None:
        _generation_context["report_id"] = report_id
    # Release the Task lock before loading all request/Attempt evidence and
    # rendering the potentially large HTML package.
    db.commit()
    title = str(redact_sensitive_value(task.name) or "EvalPlat report")
    metric_rows: list[tuple[Run, dict]] = []
    for run in runs:
        try:
            metric_rows.append((run, aggregate_run_metrics(db, run.id)))
        except Exception:  # metrics must not prevent evidence report generation
            logger.exception("failed to aggregate run metrics for report", extra={"run_id": str(run.id)})
            metric_rows.append((run, {"summary": {"error": "指标计算失败"}, "by_phase": {}}))

    requests_by_run: dict[uuid.UUID, list[Request]] = {}
    attempts_by_request: dict[int, list[Attempt]] = {}
    if runs:
        request_rows = list(
            db.scalars(select(Request).where(Request.run_id.in_([run.id for run in runs])).order_by(Request.run_id, Request.id))
        )
        for request in request_rows:
            requests_by_run.setdefault(request.run_id, []).append(request)
        if request_rows:
            attempt_rows = db.scalars(
                select(Attempt)
                .where(Attempt.request_id.in_([request.id for request in request_rows]))
                .order_by(Attempt.request_id, Attempt.attempt_index)
            )
            for attempt in attempt_rows:
                attempts_by_request.setdefault(attempt.request_id, []).append(attempt)

    def summary_metrics(run: Run) -> dict:
        return (next((metrics["summary"] for candidate, metrics in metric_rows if candidate.id == run.id), {}))

    rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(str(value) if value is not None else '')}</td>"
            for value in (
                run.seq,
                run.id,
                run_contexts.get(run.id, {}).get("supplier_name"),
                run_contexts.get(run.id, {}).get("base_url"),
                run.model_deployment_id,
                run.status,
                summary_metrics(run).get("request_count"),
                summary_metrics(run).get("success_rate"),
                redact_sensitive_value(run.error_summary),
                "不安全 TLS" if bool((run.run_snapshot or {}).get("tls_skip_verify")) else "",
                redact_sensitive_value(run_remarks.get(run.id, [])),
            )
        ) + "</tr>"
        for run in runs
    )
    summary_body = f"""
<h1>{html.escape(title)}</h1>
    <p>任务 ID: {html.escape(str(task.id))}</p>
<p>任务备注: {_safe_html_value(task.remark or "-")}</p>
<p>状态: {html.escape(task.status)}；报告版本: {version}；生成时间: {datetime.now(timezone.utc).isoformat()}</p>
<p class="muted">请求明细是独立文件，可在外发前删除；摘要不依赖该文件。</p>
    <table><thead><tr><th>序号</th><th>Run</th><th>供应商</th><th>Base URL</th><th>模型部署</th><th>状态</th><th>请求数</th><th>成功率</th><th>错误</th><th>安全</th><th>备注</th></tr></thead>
<tbody>{rows}</tbody></table>"""
    summary_document = _page(title, summary_body, links)

    performance_rows: list[str] = []
    quality_rows: list[str] = []
    cost_rows: list[str] = []
    error_counts: dict[str, int] = {}
    for run, metrics in metric_rows:
        summary = metrics.get("summary") or {}
        latency = summary.get("latency_ms") or {}
        tokens = summary.get("tokens_by_source") or {}
        cost = summary.get("cost") or {}
        performance_rows.append(
            "<tr>" + "".join(
                f"<td>{_metric_cell(value)}</td>"
            for value in (run.seq, run.id, summary.get("request_count"), summary.get("sent_request_count"), summary.get("scheduled_dropped"), latency.get("p50"), latency.get("p95"), (summary.get("ttft_ms") or {}).get("p95"), (summary.get("tpot_ms") or {}).get("p95"), summary.get("throughput_requests_per_second"), summary.get("throughput_tokens_per_second"))
            ) + "</tr>"
        )
        requests = requests_by_run.get(run.id, [])
        quality_count = sum(request.kind == "quality" for request in requests)
        quality_success = sum(request.kind == "quality" and request.status == "completed" for request in requests)
        quality_summary = summary.get("quality") or {}
        code_metrics = summary.get("code_execution") or {}
        quality_rows.append("<tr>" + "".join(f"<td>{_metric_cell(value)}</td>" for value in (
            run.seq,
            quality_summary.get("sample_count", quality_count),
            quality_success,
            quality_success / quality_count if quality_count else None,
            quality_summary.get("score_mean"),
            quality_summary.get("accuracy"),
            code_metrics.get("total", 0),
            code_metrics.get("passed", 0),
            code_metrics.get("pass_rate"),
            code_metrics.get("pending", 0),
        )) + "</tr>")
        cost_rows.append("<tr>" + "".join(f"<td>{_metric_cell(value)}</td>" for value in (run.seq, cost.get("amount"), cost.get("currency"), cost.get("estimated"), (tokens.get("vendor") or {}).get("total"), (tokens.get("local") or {}).get("total"), (tokens.get("difference") or {}).get("total"))) + "</tr>")
        for request in requests:
            attempts = attempts_by_request.get(request.id, [])
            for attempt in attempts:
                if attempt.error_category:
                    key = attempt.error_category
                elif attempt.http_status is not None and not 200 <= attempt.http_status < 300:
                    key = f"http:{attempt.http_status}"
                else:
                    # Successful attempts (including 2xx/3xx compatibility
                    # probes) are not errors and must not pollute this page.
                    continue
                error_counts[key] = error_counts.get(key, 0) + 1

    percentile_values: list[tuple[object, object]] = []
    request_series: list[tuple[object, list[object]]] = []
    quality_values: list[tuple[object, object]] = []
    cost_values: list[tuple[object, object]] = []
    for run, metrics in metric_rows:
        summary = metrics.get("summary") or {}
        run_label = f"Run #{run.seq or '-'}"
        latency = summary.get("latency_ms") or {}
        for percentile in ("p50", "p90", "p95", "p99"):
            percentile_values.append((f"{run_label} {percentile.upper()}", latency.get(percentile)))
        series = summary.get("request_series") or []
        request_series.append((run_label, [item.get("latency_ms") for item in series if isinstance(item, dict)]))
        quality = summary.get("quality") or {}
        quality_values.append((run_label, quality.get("score_mean")))
        cost = summary.get("cost") or {}
        cost_values.append((run_label, cost.get("amount")))

    performance_chart = _svg_bar_chart("Latency 分位数（ms）", percentile_values, suffix=" ms")
    time_series_chart = _svg_line_chart("请求 Latency 时间序列（按请求顺序）", request_series)
    quality_chart = _svg_bar_chart("质量得分均值", quality_values)
    cost_chart = _svg_bar_chart("Run 成本", cost_values)
    error_chart = _svg_bar_chart("错误分类", list(error_counts.items()))
    performance_body = f"<h1>{html.escape(title)} 性能</h1>{performance_chart}{time_series_chart}<table><thead><tr><th>Run</th><th>ID</th><th>计划请求数</th><th>实际发送数</th><th>调度丢弃</th><th>Latency P50</th><th>Latency P95</th><th>TTFT P95</th><th>TPOT P95</th><th>实际 RPS</th><th>Token/s</th></tr></thead><tbody>{''.join(performance_rows)}</tbody></table>"
    quality_body = f"<h1>{html.escape(title)} 质量</h1>{quality_chart}<table><thead><tr><th>Run</th><th>质量样本数</th><th>生成成功数</th><th>生成成功率</th><th>得分均值</th><th>准确率</th><th>代码执行数</th><th>代码通过数</th><th>代码通过率</th><th>待执行</th></tr></thead><tbody>{''.join(quality_rows)}</tbody></table>"
    cost_body = f"<h1>{html.escape(title)} 成本与 Token</h1>{cost_chart}<table><thead><tr><th>Run</th><th>成本</th><th>币种</th><th>是否估算</th><th>Vendor Token</th><th>Local Token</th><th>差异</th></tr></thead><tbody>{''.join(cost_rows)}</tbody></table>"
    errors_body = "<h1>错误分类</h1>" + error_chart + "<table><thead><tr><th>类别</th><th>次数</th></tr></thead><tbody>" + "".join(f"<tr><td>{html.escape(str(key))}</td><td>{count}</td></tr>" for key, count in sorted(error_counts.items(), key=lambda item: (-item[1], item[0]))) + "</tbody></table>"
    completed_ids = [run.id for run in runs if run.status in {"completed", "partially_completed", "failed", "cancelled", "interrupted_failed"}]
    comparison_lines = "<p>至少需要两个终态 Run 才能检查可比性。</p>"
    if len(completed_ids) >= 2:
        try:
            comparison = compare_runs(db, completed_ids)
            comparison_lines = f"<p>可比性：<strong>{html.escape(comparison['comparability'])}</strong></p><ul>" + "".join(f"<li>{html.escape(item['dimension'])}: {html.escape(item['details'])}</li>" for item in comparison["differences"]) + "</ul>"
        except Exception:
            # A malformed legacy snapshot should not strand the whole report
            # in ``generating`` or prevent the evidence pages from publishing.
            logger.exception("failed to compare runs for report", extra={"task_id": str(task_id)})
            comparison_lines = "<p>可比性检查失败，请检查 Run 快照和指标数据。</p>"
    comparison_body = f"<h1>{html.escape(title)} 对比</h1>{comparison_lines}"
    detail_rows: list[str] = []
    for run in runs:
        requests = requests_by_run.get(run.id, [])
        for request in requests:
            attempts = attempts_by_request.get(request.id, [])
            for attempt in attempts or [None]:
                values = (
                    request.id,
                    request.kind,
                    request.phase,
                    request.status,
                    (attempt.attempt_index if attempt else None),
                    (attempt.http_status if attempt else None),
                    (attempt.error_category if attempt else None),
                    json.dumps(redact_sensitive_value(request.input_messages), ensure_ascii=False, default=str),
                    redact_sensitive_value(attempt.extracted_fields if attempt else None),
                    redact_sensitive_value(attempt.error_body if attempt else None),
                    redact_sensitive_value(attempt.raw_response if attempt else None),
                )
                detail_rows.append(
                    "<tr>" + "".join(f"<td>{html.escape(str(value) if value is not None else '')}</td>" for value in values) + "</tr>"
                )
    details_document = _page(title + " request details", f"""
<h1>{html.escape(title)} 请求明细</h1><p>报告版本: {version}</p>
<table><thead><tr><th>Request</th><th>类型</th><th>阶段</th><th>状态</th><th>Attempt</th><th>HTTP</th><th>错误类别</th><th>输入</th><th>提取字段</th><th>错误正文</th><th>原始响应</th></tr></thead>
<tbody>{''.join(detail_rows)}</tbody></table>""", {"首页": paths["index"], "摘要": paths["summary"]})
    index_body = f"<h1>{html.escape(title)}</h1><p>报告版本 {version}，包含性能、质量、成本、错误、对比和请求证据页面。</p><ul>" + "".join(f"<li><a href=\"{html.escape(path)}\">{html.escape(label)}</a></li>" for label, path in links.items()) + "</ul>"
    documents = {
        paths["index"]: _page(title, index_body, links),
        paths["summary"]: summary_document,
        paths["performance"]: _page(title + " performance", performance_body, links),
        paths["quality"]: _page(title + " quality", quality_body, links),
        paths["cost"]: _page(title + " cost", cost_body, links),
        paths["errors"]: _page(title + " errors", errors_body, links),
        paths["comparison"]: _page(title + " comparison", comparison_body, links),
        paths["details"]: details_document,
    }

    published_paths: list[Path] = []
    try:
        # Render the complete package away from its published names. A failed
        # render therefore cannot leave a half-written report visible to
        # downloads or history cleanup; publication only starts after every
        # document has been written successfully.
        with tempfile.TemporaryDirectory(prefix=f".report-{report_id}-", dir=root) as temp_dir:
            staging = Path(temp_dir)
            for filename, document in documents.items():
                (staging / filename).write_text(document, encoding="utf-8")
            try:
                for filename in documents:
                    destination = root / filename
                    os.replace(staging / filename, destination)
                    published_paths.append(destination)
            except Exception:
                # Versioned names are unique, so removing the few already
                # published files restores the pre-generation directory state.
                for destination in published_paths:
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError:
                        logger.exception("failed to roll back report file", extra={"path": str(destination)})
                raise
        # Persist only controlled filenames.  Absolute host paths are an
        # implementation detail and must never cross the API/report boundary.
        report.summary_path = paths["summary"]
        report.details_path = paths["details"]
        report.status = "completed"
        record_event(db, "report_generated", target_type="task", target_id=task_id, diff={"report_id": str(report.id), "version": version, "pages": list(paths.values())})
        db.commit()
    except Exception as exc:
        # A provider/database failure after publication must not leave files
        # that history cleanup cannot discover. Best-effort status persistence
        # is secondary to removing the already-published package.
        for destination in published_paths:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to roll back report file", extra={"path": str(destination)})
        db.rollback()
        try:
            report.status = "failed"
            report.error = redact_sensitive_text(str(exc)[:500]) or "报告生成失败"
            db.add(report)
            db.commit()
        except Exception:
            db.rollback()
        raise
    return _report_result(report)


def _load_run_remarks(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """Load all Run notes in one query for a report package."""

    if not run_ids:
        return {}
    notes = db.scalars(
        select(Note)
        .where(Note.target_type == "run", Note.target_id.in_(run_ids))
        .order_by(Note.target_id, Note.created_at.asc(), Note.id.asc())
    )
    remarks: dict[uuid.UUID, list[str]] = {}
    for note in notes:
        remarks.setdefault(note.target_id, []).append(note.body)
    return remarks


def export_csv(db: Session, run_id: uuid.UUID) -> dict:
    """导出 Run 的请求/尝试数据为 CSV。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
    output = _report_root() / f"run-{run_id}.csv"
    # Count attempts in a grouped subquery and stream the request page directly
    # to the staging file. This avoids a request-ID list and an equally large
    # in-memory count map for high-volume Runs.
    attempt_counts = (
        select(Attempt.request_id, func.count(Attempt.id).label("attempt_count"))
        .join(Request, Request.id == Attempt.request_id)
        .where(Request.run_id == run_id)
        .group_by(Attempt.request_id)
        .subquery()
    )
    rows = db.execute(
        select(Request, func.coalesce(attempt_counts.c.attempt_count, 0))
        .outerjoin(attempt_counts, attempt_counts.c.request_id == Request.id)
        .where(Request.run_id == run_id)
        .order_by(Request.id)
    ).yield_per(1_000)

    def write_csv(stream) -> None:
        writer = csv.writer(stream)
        writer.writerow([
            "request_id", "kind", "status", "started_at", "finished_at",
            "vendor_prompt_tokens", "vendor_cached_tokens", "vendor_completion_tokens",
            "vendor_total_tokens", "attempt_count",
        ])
        for request, attempt_count in rows:
            writer.writerow([_csv_safe(value) for value in [
                request.id, request.kind, request.status, request.started_at, request.finished_at,
                request.vendor_prompt_tokens, request.vendor_cached_tokens,
                request.vendor_completion_tokens, request.vendor_total_tokens,
                int(attempt_count),
            ]])

    _atomic_write_stream(output, write_csv, newline="")
    return {"run_id": run_id, "format": "csv", "status": "completed", "path": output.name, "download_url": f"/api/v1/reports/files/{output.name}", "created_at": datetime.now(timezone.utc)}


def export_json(db: Session, run_id: uuid.UUID) -> dict:
    """导出 Run 的机器可读 JSON（不包含 API Key 或密文）。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
    task = db.get(Task, run.task_id) if run.task_id is not None else None
    requests = list(db.scalars(select(Request).where(Request.run_id == run_id).order_by(Request.id)))
    attempts_by_request: dict[int, list[Attempt]] = {}
    if requests:
        attempt_rows = db.scalars(
            select(Attempt)
            .where(Attempt.request_id.in_([request.id for request in requests]))
            .order_by(Attempt.request_id, Attempt.attempt_index, Attempt.id)
        )
        for attempt in attempt_rows:
            attempts_by_request.setdefault(attempt.request_id, []).append(attempt)
    payload = {
        "task": (
            {
                "id": str(task.id),
                "name": task.name,
                "remark": redact_sensitive_value(task.remark),
                "template_version_id": str(task.template_version_id) if task.template_version_id else None,
                "run_mode": task.run_mode,
                "status": task.status,
                "config_snapshot": redact_sensitive_value(task.config_snapshot),
                "precheck_result": redact_sensitive_value(task.precheck_result),
                "created_at": task.created_at,
                "queued_at": task.queued_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
            }
            if task is not None
            else None
        ),
        "run_id": str(run.id),
        "task_id": str(run.task_id) if run.task_id else None,
        "model_deployment_id": str(run.model_deployment_id) if run.model_deployment_id else None,
        "retry_of_run_id": str(run.retry_of_run_id) if run.retry_of_run_id else None,
        "seq": run.seq,
        "status": run.status,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error_summary": redact_sensitive_value(run.error_summary),
        # credential_version 只用于内部复现审计，不应进入可外发 JSON。
        "run_snapshot": redact_sensitive_value(
            {
                key: value
                for key, value in (run.run_snapshot or {}).items()
                if key not in {"credential_version", "api_key", "api_key_ciphertext"}
            }
        ),
        "requests": [
            {
                "id": request.id,
                "kind": request.kind,
                "phase": request.phase,
                "status": request.status,
                "sample_index": request.sample_index,
                "quality_result": redact_sensitive_value(request.quality_result),
                "input_messages": redact_sensitive_value(request.input_messages),
                "started_at": request.started_at,
                "finished_at": request.finished_at,
                "vendor_usage": {
                    "prompt_tokens": request.vendor_prompt_tokens,
                    "cached_tokens": request.vendor_cached_tokens,
                    "completion_tokens": request.vendor_completion_tokens,
                    "total_tokens": request.vendor_total_tokens,
                },
                "local_usage": {
                    "prompt_tokens": request.local_prompt_tokens,
                    "completion_tokens": request.local_completion_tokens,
                    "total_tokens": request.local_total_tokens,
                },
                "attempts": [
                    {
                        "id": attempt.id,
                        "attempt_index": attempt.attempt_index,
                        "latency_ms": str(attempt.latency_ms) if attempt.latency_ms is not None else None,
                        "ttft_ms": str(attempt.ttft_ms) if attempt.ttft_ms is not None else None,
                        "tpot_ms": str(attempt.tpot_ms) if attempt.tpot_ms is not None else None,
                        "itl_series": redact_sensitive_value(attempt.itl_series),
                        "avg_speed": str(attempt.avg_speed) if attempt.avg_speed is not None else None,
                        "peak_speed": str(attempt.peak_speed) if attempt.peak_speed is not None else None,
                        "peak_tps": str(attempt.peak_tps) if attempt.peak_tps is not None else None,
                        "vendor_usage": redact_sensitive_value(attempt.vendor_usage),
                        "raw_response": redact_sensitive_value(attempt.raw_response),
                        "extracted_fields": redact_sensitive_value(attempt.extracted_fields),
                        "http_status": attempt.http_status,
                        "error_category": attempt.error_category,
                        "error_body": redact_sensitive_value(attempt.error_body),
                        "vendor_request_id": attempt.vendor_request_id,
                        "response_headers": redact_sensitive_value(attempt.response_headers),
                    }
                    for attempt in attempts_by_request.get(request.id, [])
                ],
            }
            for request in requests
        ],
        "code_executions": [
            {
                "id": str(execution.id),
                "request_id": execution.request_id,
                "sample_index": execution.sample_index,
                "status": execution.status,
                "compile_status": execution.compile_status,
                "run_status": execution.run_status,
                "code": redact_sensitive_value(execution.code),
                "tests": redact_sensitive_value(execution.tests),
                "stdout": redact_sensitive_value(execution.stdout),
                "stderr": redact_sensitive_value(execution.stderr),
                "test_results": redact_sensitive_value(execution.test_results),
                "exit_code": execution.exit_code,
                "duration_ms": str(execution.duration_ms) if execution.duration_ms is not None else None,
                "error_category": execution.error_category,
                "error_summary": redact_sensitive_value(execution.error_summary),
                "started_at": execution.started_at,
                "finished_at": execution.finished_at,
            }
            for execution in db.scalars(
                select(CodeExecution)
                .where(CodeExecution.run_id == run_id)
                .order_by(CodeExecution.sample_index, CodeExecution.created_at)
            )
        ],
    }
    output = _report_root() / f"run-{run_id}.json"
    _atomic_write_text(output, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return {"run_id": run_id, "format": "json", "status": "completed", "path": output.name, "download_url": f"/api/v1/reports/files/{output.name}", "created_at": datetime.now(timezone.utc)}
