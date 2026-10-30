from datetime import datetime, timedelta, timezone
from pathlib import Path

from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.connection import Connection
from evalplat.models.connection_rate_event import ConnectionRateEvent
from evalplat.models.note import Note
from evalplat.models.report import Report
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.models.supplier import Supplier
from evalplat.services import history_cleanup_service


def _terminal_task(db, *, status="completed", finished_at=None):
    task = Task(name="历史任务", run_mode="serial", status=status, finished_at=finished_at)
    db.add(task)
    db.flush()
    return task


def test_preview_excludes_active_runs_and_generating_reports(db, tmp_path):
    cutoff = datetime.now(timezone.utc)
    eligible = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    active = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    generating = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    db.add(Run(task_id=active.id, status="awaiting_code"))
    db.add(Report(task_id=generating.id, version=1, status="generating"))
    db.commit()

    result = history_cleanup_service.preview_cleanup(db, cutoff)

    assert result["task_ids"] == [eligible.id]
    assert result["planned_counts"]["tasks"] == 1
    assert result["estimated_bytes"] == 0


def test_create_and_execute_cleanup_batch_removes_evidence(db, tmp_path, monkeypatch):
    monkeypatch.setattr("evalplat.services.history_cleanup_service.get_settings", lambda: type("S", (), {"report_dir": str(tmp_path)})())
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    run = Run(task_id=task.id, status="completed")
    db.add(run)
    db.flush()
    request = Request(id=100, run_id=run.id, kind="perf", status="completed")
    db.add(request)
    db.flush()
    supplier = Supplier(name="清理测试供应商", short_name="cleanup", type="other")
    db.add(supplier)
    db.flush()
    connection = Connection(supplier_id=supplier.id, name="清理连接", base_url="https://example.com")
    db.add(connection)
    db.flush()
    db.add_all([
        Attempt(id=1000, request_id=request.id, attempt_index=1),
        CodeExecution(run_id=run.id, request_id=request.id, code="x", tests="", status="passed"),
        ConnectionRateEvent(connection_id=connection.id, request_id=request.id, started_at=cutoff, expires_at=cutoff),
        Note(target_type="task", target_id=task.id, body="task note"),
        Note(target_type="run", target_id=run.id, body="run note"),
    ])
    report_path = tmp_path / "summary.html"
    report_path.write_text("report", encoding="utf-8")
    db.add(Report(task_id=task.id, version=1, status="completed", summary_path=str(report_path)))
    db.commit()

    batch = history_cleanup_service.create_cleanup_batch(db, cutoff)
    assert batch.manifest["task_ids"] == [str(task.id)]
    assert batch.planned_counts["attempts"] == 1
    assert batch.planned_counts["file_bytes"] == len("report")
    assert report_path.exists()

    executed = history_cleanup_service.execute_cleanup_batch(db, batch.id, confirmation="CONFIRM")

    assert executed.status == "completed"
    assert not report_path.exists()
    assert db.get(Task, task.id) is None
    assert db.get(Run, run.id) is None
    assert db.get(Request, request.id) is None
    assert executed.deleted_counts["code_executions"] == 1
    # Repeating execute is an idempotent no-op.
    assert history_cleanup_service.execute_cleanup_batch(db, batch.id, confirmation="CONFIRM").status == "completed"


def test_preview_counts_code_jobs_without_request_id(db):
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    run = Run(task_id=task.id, status="completed")
    db.add(run)
    db.flush()
    db.add(CodeExecution(run_id=run.id, request_id=None, code="pass", tests="", status="failed"))
    db.commit()

    result = history_cleanup_service.preview_cleanup(db, cutoff)

    assert result["planned_counts"]["code_executions"] == 1
    assert result["tasks"][0]["counts"]["code_executions"] == 1


def test_cleanup_handles_windows_report_path_and_exports(db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evalplat.services.history_cleanup_service.get_settings",
        lambda: type("S", (), {"report_dir": str(tmp_path)})(),
    )
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    run = Run(task_id=task.id, status="completed")
    db.add(run)
    db.flush()
    summary_name = f"task-{task.id}-v1-summary.html"
    summary_path = tmp_path / summary_name
    summary_path.write_text("summary", encoding="utf-8")
    csv_path = tmp_path / f"run-{run.id}.csv"
    json_path = tmp_path / f"run-{run.id}.json"
    csv_path.write_text("csv", encoding="utf-8")
    json_path.write_text("json", encoding="utf-8")
    db.add(
        Report(
            task_id=task.id,
            version=1,
            status="completed",
            summary_path=rf"C:\\reports\\{summary_name}",
        )
    )
    db.commit()

    batch = history_cleanup_service.create_cleanup_batch(db, cutoff)
    assert batch.planned_counts["files"] == 3
    assert batch.planned_counts["file_bytes"] == len("summary") + len("csv") + len("json")

    executed = history_cleanup_service.execute_cleanup_batch(db, batch.id, confirmation="CONFIRM")

    assert executed.status == "completed"
    assert not summary_path.exists()
    assert not csv_path.exists()
    assert not json_path.exists()


def test_cleanup_api_redacts_report_paths(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evalplat.services.history_cleanup_service.get_settings",
        lambda: type("S", (), {"report_dir": str(tmp_path)})(),
    )
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    db.add(Run(task_id=task.id, status="completed"))
    db.flush()
    filename = f"task-{task.id}-v1-summary.html"
    (tmp_path / filename).write_text("summary", encoding="utf-8")
    db.add(
        Report(
            task_id=task.id,
            version=1,
            status="completed",
            summary_path=rf"C:\\private\\{filename}",
        )
    )
    db.commit()

    preview = client.post(
        "/api/v1/admin/history-cleanup/preview",
        json={"cutoff_at": cutoff.isoformat(), "statuses": ["completed"]},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["report_files"] == [filename, f"run-{body['tasks'][0]['run_ids'][0]}.csv", f"run-{body['tasks'][0]['run_ids'][0]}.json"]
    assert all("private" not in str(value) for value in body.values())

    batch = client.post(
        "/api/v1/admin/history-cleanup/batches",
        json={"cutoff_at": cutoff.isoformat(), "statuses": ["completed"]},
    )
    assert batch.status_code == 201, batch.text
    assert "private" not in batch.text
    assert filename in batch.json()["manifest"]["report_files"]


def test_export_download_is_blocked_while_cleanup_runs(db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evalplat.services.history_cleanup_service.get_settings",
        lambda: type("S", (), {"report_dir": str(tmp_path)})(),
    )
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    run = Run(task_id=task.id, status="completed")
    db.add(run)
    db.flush()
    export_path = tmp_path / f"run-{run.id}.csv"
    export_path.write_text("sensitive", encoding="utf-8")
    batch = history_cleanup_service.create_cleanup_batch(db, cutoff)

    # Simulate the committed running marker before file/row deletion starts.
    batch.status = "running"
    db.commit()

    assert history_cleanup_service.report_file_cleanup_in_progress(db, export_path)


def test_cleanup_rejects_outside_report_file_and_retries(db, tmp_path, monkeypatch):
    monkeypatch.setattr("evalplat.services.history_cleanup_service.get_settings", lambda: type("S", (), {"report_dir": str(tmp_path)})())
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    outside = Path(tmp_path).parent / "outside-report.html"
    outside.write_text("keep", encoding="utf-8")
    db.add(Report(task_id=task.id, version=1, status="completed", summary_path=str(outside)))
    db.commit()

    batch = history_cleanup_service.create_cleanup_batch(db, cutoff)
    failed = history_cleanup_service.execute_cleanup_batch(db, batch.id, confirmation="CONFIRM")
    assert failed.status == "failed"
    assert db.get(Task, task.id) is not None
    assert any(item["kind"] == "file" for item in failed.failed_items)

    # The manifest remains immutable and retry keeps the task when the file is unsafe.
    retried = history_cleanup_service.execute_cleanup_batch(db, batch.id, confirmation="CONFIRM", retry=True)
    assert retried.status == "failed"
    assert retried.manifest["task_ids"] == [str(task.id)]


def test_stale_cleanup_batch_is_unblocked_and_retryable(db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evalplat.services.history_cleanup_service.get_settings",
        lambda: type("S", (), {"report_dir": str(tmp_path), "history_cleanup_stale_seconds": 60})(),
    )
    cutoff = datetime.now(timezone.utc)
    task = _terminal_task(db, finished_at=cutoff - timedelta(minutes=1))
    run = Run(task_id=task.id, status="completed")
    db.add(run)
    db.commit()
    batch = history_cleanup_service.create_cleanup_batch(db, cutoff)
    batch.status = "running"
    batch.started_at = cutoff - timedelta(minutes=2)
    batch.updated_at = cutoff - timedelta(minutes=2)
    db.commit()

    export_path = tmp_path / f"run-{run.id}.csv"
    export_path.write_text("export", encoding="utf-8")
    assert not history_cleanup_service.report_file_cleanup_in_progress(db, export_path)

    recovered = history_cleanup_service.execute_cleanup_batch(
        db, batch.id, confirmation="CONFIRM", retry=True
    )
    assert recovered.status == "completed"
    assert db.get(Task, task.id) is None
