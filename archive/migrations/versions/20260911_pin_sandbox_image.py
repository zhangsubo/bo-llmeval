"""pin the default code sandbox image by digest"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260911_pin_sandbox_image"
down_revision: Union[str, None] = "20260910_schema_drift_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PINNED_IMAGE = (
    "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
)


def upgrade() -> None:
    # Existing rows are intentionally left unchanged: their snapshot records
    # the image selected at enqueue time and must remain historically stable.
    op.alter_column(
        "code_executions",
        "container_image",
        existing_type=sa.String(length=256),
        server_default=sa.text(f"'{_PINNED_IMAGE}'"),
    )


def downgrade() -> None:
    op.alter_column(
        "code_executions",
        "container_image",
        existing_type=sa.String(length=256),
        server_default=sa.text("'python:3.11-slim'"),
    )
