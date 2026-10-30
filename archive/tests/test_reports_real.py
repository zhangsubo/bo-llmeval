"""静态报告和 CSV 导出测试。"""

import uuid
import csv
from datetime import datetime, timezone

import pytest

from evalplat.models.request import Request
from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.run import Run
from evalplat.models.report import Report
from evalplat.models.task import Task
from evalplat.models.note import Note
from evalplat.models.supplier import Supplier
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.services import report_service
from evalplat.core.errors import ConflictError


def test_generate_report_writes_escaped_html(db, tmp_path):
    task = Task(name="<任务>", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    db.add(Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="completed", seq=1))
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)

    assert result["status"] == "completed"
    assert not result["path"].startswith(str(tmp_path))
    assert not result["page_paths"]["summary"].startswith(str(tmp_path))
    content = tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "&lt;任务&gt;" in content
    details = tmp_path.joinpath(result["details_path"].split("/")[-1])
    assert details.exists()
    assert db.query(Report).one().version == 1


def test_report_snapshot_contains_remarks_and_tls_warning(db, tmp_path):
    task = Task(name="带备注", run_mode="serial", status="completed", remark="任务说明")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="completed",
        seq=1,
        run_snapshot={"tls_skip_verify": True},
    )
    db.add(run)
    db.flush()
    db.add(Note(target_type="run", target_id=run.id, body="运行说明"))
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)
    snapshot = db.query(Report).one().snapshot
    assert snapshot["task_remark"] == "任务说明"
    assert snapshot["run_remarks"][str(run.id)] == ["运行说明"]
    assert snapshot["tls_skip_verify"] is True
    summary = (tmp_path / result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "不安全 TLS" in summary


def test_report_html_redacts_credentials_in_user_remarks(db, tmp_path):
    secret = "Bearer report-secret-12345678"
    task = Task(name="任务", run_mode="serial", status="completed", remark=secret)
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="completed",
        seq=1,
        error_summary=secret,
    )
    db.add(run)
    db.flush()
    db.add(Note(target_type="run", target_id=run.id, body=secret))
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)
    html_text = (tmp_path / result["path"]).read_text(encoding="utf-8")

    assert secret not in html_text
    assert "Bearer [REDACTED]" in html_text


def test_report_summary_displays_supplier_and_base_url(db, tmp_path):
    supplier = Supplier(name="报告供应商", short_name="report-supplier", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="报告连接",
        base_url="https://api.example.test",
    )
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(
        connection_id=connection.id,
        model_id="report-model",
        source="manual",
    )
    db.add(deployment)
    db.flush()
    task = Task(name="供应商摘要", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=deployment.id,
        status="completed",
        seq=1,
        run_snapshot={"base_url": connection.base_url},
    )
    db.add(run)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)

    summary = (tmp_path / result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "报告供应商" in summary
    assert "https://api.example.test" in summary
    assert db.query(Report).one().snapshot["run_contexts"][str(run.id)] == {
        "supplier_name": "报告供应商",
        "base_url": "https://api.example.test",
    }


def test_report_commit_failure_removes_published_files(db, tmp_path, monkeypatch):
    task = Task(name="提交失败", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)
    original_commit = db.commit
    failed_once = False

    def fail_once():
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("database unavailable")
        return original_commit()

    monkeypatch.setattr(db, "commit", fail_once)
    with pytest.raises(RuntimeError, match="database unavailable"):
        report_service.generate_report(db, task.id)
    assert list(tmp_path.glob("*.html")) == []


def test_report_render_failure_converges_generating_row(db, tmp_path, monkeypatch):
    task = Task(name="渲染失败", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    def fail_page(*_args, **_kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(report_service, "_page", fail_page)

    with pytest.raises(RuntimeError, match="render failed"):
        report_service.generate_report(db, task.id)

    report = db.query(Report).one()
    assert report.status == "failed"
    assert report.error == "render failed"
    assert list(tmp_path.glob("*.html")) == []


def test_report_render_failure_redacts_provider_secret(db, tmp_path, monkeypatch):
    task = Task(name="渲染脱敏", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    monkeypatch.setattr(
        report_service,
        "_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("render failed with sk-provider-private-secret")
        ),
    )

    with pytest.raises(RuntimeError):
        report_service.generate_report(db, task.id)

    report = db.query(Report).one()
    assert report.status == "failed"
    assert "sk-provider-private-secret" not in (report.error or "")


def test_generate_report_rejects_non_terminal_task(db, tmp_path):
    task = Task(name="进行中", run_mode="serial", status="running")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    with pytest.raises(ConflictError, match="尚未进入终态"):
        report_service.generate_report(db, task.id)


def test_summary_remains_readable_when_details_are_removed(db, tmp_path):
    task = Task(name="任务", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)
    details = tmp_path.joinpath(result["details_path"].split("/")[-1])
    details.unlink()

    summary = tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "任务" in summary
    assert "摘要不依赖该文件" in summary


def test_report_error_page_excludes_successful_attempts(db, tmp_path):
    task = Task(name="错误统计", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    run = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="completed", seq=1)
    db.add(run)
    db.flush()
    request = Request(id=4, run_id=run.id, kind="perf", status="completed")
    db.add(request)
    db.flush()
    db.add_all([
        Attempt(id=4, request_id=request.id, attempt_index=1, http_status=200),
        Attempt(id=5, request_id=request.id, attempt_index=2, http_status=503),
    ])
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)

    error_page = tmp_path / result["page_paths"]["errors"].split("/")[-1]
    content = error_page.read_text(encoding="utf-8")
    assert "http:503" in content
    assert "http:200" not in content


def test_report_analysis_pages_include_offline_charts(db, tmp_path):
    task = Task(name="图表报告", run_mode="parallel", status="completed")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="completed",
        seq=1,
        run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}},
    )
    db.add(run)
    db.flush()
    requests = [
        Request(
            id=21,
            run_id=run.id,
            kind="perf",
            phase="steady",
            status="completed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            vendor_prompt_tokens=10,
            vendor_completion_tokens=5,
            vendor_total_tokens=15,
        ),
        Request(
            id=22,
            run_id=run.id,
            kind="quality",
            phase="steady",
            status="completed",
            quality_result={"score": 1.0, "sample_count": 1},
        ),
    ]
    db.add_all(requests)
    db.flush()
    db.add_all([
        Attempt(id=21, request_id=21, attempt_index=1, latency_ms=120),
        Attempt(id=22, request_id=22, attempt_index=1, latency_ms=80, error_category="http:503"),
    ])
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id)

    performance = (tmp_path / result["page_paths"]["performance"]).read_text(encoding="utf-8")
    quality = (tmp_path / result["page_paths"]["quality"]).read_text(encoding="utf-8")
    errors = (tmp_path / result["page_paths"]["errors"]).read_text(encoding="utf-8")
    assert performance.count("<svg") >= 2
    assert "Latency 分位数" in performance
    assert "请求 Latency 时间序列" in performance
    assert "质量得分均值" in quality
    assert "错误分类" in errors
    assert "https://" not in performance


def test_export_csv_writes_request_rows(db, tmp_path):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="perf", status="completed", vendor_total_tokens=3)
    request.id = 1
    db.add(request)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.export_csv(db, run.id)

    assert result["status"] == "completed"
    content = tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "vendor_total_tokens" in content
    assert ",3," in content


def test_export_csv_counts_attempts_in_one_batch(db, tmp_path):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    requests = [
        Request(id=11, run_id=run.id, kind="perf", status="completed"),
        Request(id=12, run_id=run.id, kind="perf", status="failed"),
    ]
    db.add_all(requests)
    db.flush()
    db.add_all([
        Attempt(id=11, request_id=11, attempt_index=1, http_status=200),
        Attempt(id=12, request_id=11, attempt_index=2, http_status=503),
        Attempt(id=13, request_id=12, attempt_index=1, http_status=500),
    ])
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.export_csv(db, run.id)

    with (tmp_path / result["path"]).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [(row["request_id"], row["attempt_count"]) for row in rows] == [("11", "2"), ("12", "1")]


def test_export_files_are_atomic_on_publish_failure(db, tmp_path, monkeypatch):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)
    csv_path = tmp_path / f"run-{run.id}.csv"
    json_path = tmp_path / f"run-{run.id}.json"
    csv_path.write_text("previous csv\n", encoding="utf-8")
    json_path.write_text("previous json\n", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("publish failed")

    monkeypatch.setattr(report_service.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publish failed"):
        report_service.export_csv(db, run.id)
    with pytest.raises(OSError, match="publish failed"):
        report_service.export_json(db, run.id)

    assert csv_path.read_text(encoding="utf-8") == "previous csv\n"
    assert json_path.read_text(encoding="utf-8") == "previous json\n"
    assert not list(tmp_path.glob(".*.tmp"))


def test_export_csv_escapes_formula_like_strings(db, tmp_path):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="perf", status="completed")
    request.id = 2
    db.add(request)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    report_service.export_csv(db, run.id)

    content = tmp_path.joinpath(f"run-{run.id}.csv").read_text(encoding="utf-8")
    assert "request_id" in content
    assert report_service._csv_safe("=perf") == "'=perf"


def test_export_json_excludes_credentials(db, tmp_path):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="completed",
        run_snapshot={
            "credential_version": 2,
            "dataset_snapshot": {"storage_path": "/srv/evalplat/data/private.jsonl"},
        },
    )
    db.add(run)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.export_json(db, run.id)

    assert result["status"] == "completed"
    content = tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "credential_version" not in content
    assert '"storage_path": "private.jsonl"' in content
    assert "/srv/evalplat/data" not in content
    assert '"run_id"' in content


def test_export_json_includes_code_execution_evidence(db, tmp_path):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    db.add(CodeExecution(
        run_id=run.id,
        sample_index=0,
        code="def add(a, b): return a + b",
        tests="assert add(1, 2) == 3",
        status="passed",
        compile_status="passed",
        run_status="passed",
    ))
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.export_json(db, run.id)

    content = tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")
    assert '"code_executions"' in content
    assert "assert add(1, 2) == 3" in content


def test_export_json_serializes_evidence_timestamps(db, tmp_path):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(
        id=3,
        run_id=run.id,
        kind="perf",
        status="completed",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(request)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.export_json(db, run.id)

    assert result["status"] == "completed"
    assert '"started_at"' in tmp_path.joinpath(result["path"].split("/")[-1]).read_text(encoding="utf-8")


def test_report_versions_can_be_listed_and_fetched(client, db, tmp_path):
    task = Task(name="任务", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    first = report_service.generate_report(db, task.id)
    report_service.generate_report(db, task.id)

    listed = client.get(f"/api/v1/tasks/{task.id}/reports")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert [item["version"] for item in listed.json()["items"]] == [2, 1]

    fetched = client.get(f"/api/v1/reports/{first['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["version"] == 1

    filename = first["page_paths"]["summary"].split("/")[-1]
    downloaded = client.get(f"/api/v1/reports/files/{filename}")
    assert downloaded.status_code == 200
    assert "<html" in downloaded.text


def test_report_generation_endpoint_returns_only_public_file_metadata(client, db, tmp_path):
    task = Task(name="报告接口", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    response = client.post(f"/api/v1/tasks/{task.id}/report")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["status"] == "completed"
    assert body["path"] == body["page_paths"]["summary"]
    assert "snapshot" not in body
    assert "updated_at" not in body
    assert str(tmp_path) not in response.text


def test_task_report_list_applies_status_filter(client, db):
    task = Task(name="筛选报告", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    db.add_all([
        Report(task_id=task.id, version=1, status="completed"),
        Report(task_id=task.id, version=2, status="failed", error="upstream"),
    ])
    db.commit()

    response = client.get(f"/api/v1/tasks/{task.id}/reports", params={"status": "failed"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["status"] == "failed"


def test_report_generation_idempotency_key_returns_same_version(db, tmp_path):
    task = Task(name="幂等报告", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    first = report_service.generate_report(db, task.id, idempotency_key="report-op-1")
    repeated = report_service.generate_report(db, task.id, idempotency_key="report-op-1")

    assert repeated["id"] == first["id"]
    assert repeated["version"] == 1
    assert db.query(Report).count() == 1


def test_failed_report_generation_can_retry_with_same_idempotency_key(db, tmp_path, monkeypatch):
    task = Task(name="可重试报告", run_mode="serial", status="completed")
    db.add(task)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    original_page = report_service._page

    def fail_once(*_args, **_kwargs):
        monkeypatch.setattr(report_service, "_page", original_page)
        raise RuntimeError("render failed")

    monkeypatch.setattr(report_service, "_page", fail_once)
    with pytest.raises(RuntimeError, match="render failed"):
        report_service.generate_report(db, task.id, idempotency_key="retryable-report")

    failed = db.query(Report).one()
    assert failed.status == "failed"

    retried = report_service.generate_report(db, task.id, idempotency_key="retryable-report")

    assert retried["status"] == "completed"
    assert retried["version"] == 2
    assert db.query(Report).count() == 2


def test_report_schema_hides_legacy_host_path(client, db, tmp_path):
    task = Task(name="旧路径", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    legacy = tmp_path / "legacy-summary.html"
    db.add(Report(task_id=task.id, version=1, status="completed", summary_path=str(legacy)))
    db.commit()

    response = client.get(f"/api/v1/tasks/{task.id}/reports")

    assert response.status_code == 200
    assert response.json()["items"][0]["summary_path"] == legacy.name


def test_idempotent_report_result_hides_legacy_host_paths(db, tmp_path):
    task = Task(name="旧路径幂等", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    legacy_summary = tmp_path / "legacy-summary.html"
    legacy_details = tmp_path / "legacy-details.html"
    report = Report(
        task_id=task.id,
        version=1,
        status="completed",
        idempotency_key="legacy-report-op",
        summary_path=str(legacy_summary),
        details_path=str(legacy_details),
        snapshot={
            "page_paths": {
                "summary": str(legacy_summary),
                "details": str(legacy_details),
            }
        },
    )
    db.add(report)
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    result = report_service.generate_report(db, task.id, idempotency_key="legacy-report-op")

    assert result["path"] == legacy_summary.name
    assert result["details_path"] == legacy_details.name
    assert str(tmp_path) not in str(result)
    assert result["download_url"].endswith(f"/{legacy_summary.name}")
    assert result["details_download_url"].endswith(f"/{legacy_details.name}")


def test_public_report_filename_normalizes_windows_paths():
    assert report_service._public_report_filename(r"C:\\reports\\summary.html") == "summary.html"


def test_report_schema_normalizes_windows_paths(client, db):
    task = Task(name="Windows 旧路径", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    db.add(
        Report(
            task_id=task.id,
            version=1,
            status="completed",
            summary_path=r"C:\\srv\\evalplat\\summary.html",
            details_path=r"C:\\srv\\evalplat\\details.html",
        )
    )
    db.commit()

    response = client.get(f"/api/v1/tasks/{task.id}/reports")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["summary_path"] == "summary.html"
    assert item["details_path"] == "details.html"


def test_report_api_redacts_snapshot_and_error(client, db, tmp_path):
    task = Task(name="脱敏报告", run_mode="serial", status="completed")
    db.add(task)
    db.flush()
    db.add(
        Report(
            task_id=task.id,
            version=1,
            status="failed",
            snapshot={"task_remark": "Bearer report-secret-12345678"},
            error="provider returned Bearer report-secret-12345678",
        )
    )
    db.commit()
    report_service.get_settings().report_dir = str(tmp_path)

    response = client.get(f"/api/v1/tasks/{task.id}/reports")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "report-secret-12345678" not in str(item)
    assert item["snapshot"]["task_remark"] == "Bearer [REDACTED]"
    assert item["error"] == "provider returned Bearer [REDACTED]"
