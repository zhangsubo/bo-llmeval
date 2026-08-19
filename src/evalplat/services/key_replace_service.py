"""API Key 替换服务（PRD §8.2.3）。"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.audit_event import AuditEvent
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.task import Task


def replace_api_key(db: Session, connection_id: uuid.UUID, new_key_plain: str, crypto: CryptoService) -> dict:
    """替换连接的 API Key。

    流程（PRD §8.2.3）：
    1. SELECT ... FOR UPDATE 锁定连接行
    2. 检查运行中任务
    3. 加密新 Key → 更新密文 + 递增 credential_version
    4. 写审计日志（不含 Key 内容）
    """
    # 锁定连接行
    conn = db.execute(
        select(Connection).where(Connection.id == connection_id).with_for_update()
    ).scalar_one_or_none()

    if not conn:
        raise NotFoundError("连接不存在")

    # 检查关联连接是否有运行中任务
    # 路径：connection → model_deployment → run
    active_runs = db.scalar(
        select(Run).join(ModelDeployment, Run.model_deployment_id == ModelDeployment.id)
        .where(ModelDeployment.connection_id == connection_id)
        .where(Run.status.in_(["queued", "running"]))
        .limit(1)
    )

    if active_runs is not None:
        raise ConflictError("关联连接存在运行中任务，无法替换密钥")

    # 加密新 Key
    new_ciphertext = crypto.encrypt(new_key_plain)
    new_version = conn.credential_version + 1

    conn.api_key_ciphertext = new_ciphertext
    conn.credential_version = new_version

    # 审计日志（不含 Key 内容）
    audit = AuditEvent(
        action="api_key_replaced",
        target_type="connection",
        target_id=str(connection_id),
        result="success",
    )
    db.add(audit)
    db.commit()

    return {"new_mask": CryptoService.mask(new_key_plain), "credential_version": new_version}
