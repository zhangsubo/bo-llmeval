"""persist provider-reported cached input tokens"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_cached_tokens"
down_revision: Union[str, None] = "20260906_builtin_datasets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("vendor_cached_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "vendor_cached_tokens")
