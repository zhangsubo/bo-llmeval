from evalplat.models.audit_event import AuditEvent
from evalplat.models.task import Task
from evalplat.services import task_service
from evalplat.services.audit_service import bind_request_context, reset_request_context, record_event


def test_task_creation_and_audit_are_committed_together(db):
    task = task_service.create_task(db, name="审计任务", run_mode="serial")

    event = db.query(AuditEvent).filter(AuditEvent.action == "task_created").one()
    assert event.target_id == str(task.id)
    assert event.diff == {"name": "审计任务", "run_mode": "serial"}


def test_audit_diff_does_not_include_api_key_or_ciphertext(db):
    task_service.create_task(db, name="安全任务", run_mode="serial")

    for event in db.query(AuditEvent).all():
        payload = str(event.diff or {}).lower()
        assert "api_key" not in payload
        assert "ciphertext" not in payload


def test_audit_records_anonymous_request_metadata(db):
    tokens = bind_request_context(source_ip="192.0.2.10")
    try:
        record_event(db, "test_action", target_type="task")
        db.commit()
    finally:
        reset_request_context(tokens)
    event = db.query(AuditEvent).filter(AuditEvent.action == "test_action").one()
    assert event.actor_type == "anonymous"
    assert event.source_ip == "192.0.2.10"


def test_http_mutation_audit_contains_source_ip(client, db):
    response = client.post(
        "/api/v1/suppliers",
        json={"name": "HTTP 审计", "short_name": "http-audit", "type": "other"},
    )
    assert response.status_code == 201
    event = db.query(AuditEvent).filter(AuditEvent.action == "supplier_created").one()
    assert event.actor_type == "anonymous"
    assert event.source_ip
