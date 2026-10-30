"""API Key 替换：凭证版本更新和在途 Run 防护。"""

import uuid

import pytest
from cryptography.fernet import Fernet

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import ConflictError
from evalplat.models.audit_event import AuditEvent
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.services.key_replace_service import replace_api_key


def _connection_graph(db, *, run_status: str | None = None):
    """创建一条连接及其部署；可选地挂载指定状态的 Run。"""

    supplier = Supplier(name="测试供应商", short_name=f"supplier-{uuid.uuid4().hex[:8]}", type="other")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="测试连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("old-secret"),
        credential_version=1,
    )
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(
        connection_id=connection.id,
        model_id="test-model",
        source="manual",
        status="active",
    )
    db.add(deployment)
    db.flush()
    if run_status is not None:
        db.add(
            Run(
                task_id=uuid.uuid4(),
                model_deployment_id=deployment.id,
                status=run_status,
            )
        )
    db.commit()
    return connection, crypto


def test_replace_api_key_updates_ciphertext_version_and_audit(db):
    connection, crypto = _connection_graph(db)
    old_ciphertext = connection.api_key_ciphertext

    result = replace_api_key(db, connection.id, "new-secret-123", crypto)

    db.refresh(connection)
    assert result == {
        "new_mask": "new***123",
        "credential_version": 2,
    }
    assert connection.credential_version == 2
    assert connection.api_key_ciphertext != old_ciphertext
    assert crypto.decrypt(connection.api_key_ciphertext) == "new-secret-123"
    event = db.query(AuditEvent).filter(AuditEvent.action == "api_key_replaced").one()
    assert event.target_id == str(connection.id)
    assert "new-secret-123" not in str(event.diff)


@pytest.mark.parametrize(
    "run_status",
    ["queued", "running", "cancelling", "awaiting_code"],
)
def test_replace_api_key_rejects_every_in_flight_run_status(db, run_status):
    connection, crypto = _connection_graph(db, run_status=run_status)
    old_ciphertext = connection.api_key_ciphertext

    with pytest.raises(ConflictError, match="无法替换密钥"):
        replace_api_key(db, connection.id, "new-secret", crypto)

    db.refresh(connection)
    assert connection.credential_version == 1
    assert connection.api_key_ciphertext == old_ciphertext


@pytest.mark.parametrize(
    "run_status",
    ["draft", "completed", "partially_completed", "failed", "cancelled", "interrupted_failed"],
)
def test_replace_api_key_allows_draft_and_terminal_runs(db, run_status):
    connection, crypto = _connection_graph(db, run_status=run_status)

    result = replace_api_key(db, connection.id, "new-secret", crypto)

    assert result["credential_version"] == 2
    assert crypto.decrypt(connection.api_key_ciphertext) == "new-secret"


def test_replace_api_key_endpoint_returns_conflict_for_cancelling_run(client, db):
    connection, _crypto = _connection_graph(db, run_status="cancelling")

    response = client.post(
        f"/api/v1/connections/{connection.id}/replace-key",
        json={"new_api_key": "new-secret"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
