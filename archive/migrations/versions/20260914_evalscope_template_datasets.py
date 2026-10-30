"""store EvalScope benchmark names on template versions"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260914_evalscope_datasets"
down_revision: str | None = "20260913_integrity_and_retest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_template_versions",
        sa.Column(
            "evalscope_datasets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("test_template_versions", "evalscope_datasets")
