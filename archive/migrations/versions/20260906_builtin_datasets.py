"""add builtin dataset identity, source metadata, and download errors"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260906_builtin_datasets"
down_revision: Union[str, None] = "20260905_version_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("datasets", sa.Column("builtin_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_datasets_builtin_key", "datasets", ["builtin_key"])

    op.add_column("dataset_versions", sa.Column("source_id", sa.String(length=256), nullable=True))
    op.add_column("dataset_versions", sa.Column("source_revision", sa.String(length=128), nullable=True))
    op.add_column("dataset_versions", sa.Column("source_split", sa.String(length=64), nullable=True))
    op.add_column("dataset_versions", sa.Column("source_subset", sa.String(length=128), nullable=True))
    op.add_column("dataset_versions", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dataset_versions", "error")
    op.drop_column("dataset_versions", "source_subset")
    op.drop_column("dataset_versions", "source_split")
    op.drop_column("dataset_versions", "source_revision")
    op.drop_column("dataset_versions", "source_id")
    op.drop_constraint("uq_datasets_builtin_key", "datasets", type_="unique")
    op.drop_column("datasets", "builtin_key")
    op.drop_column("datasets", "is_builtin")
