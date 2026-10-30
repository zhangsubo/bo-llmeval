"""serialize and constrain immutable dataset/template versions"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_version_constraints"
down_revision: Union[str, None] = "20260905_run_retry_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_dataset_versions_dataset_version",
        "dataset_versions",
        ["dataset_id", "version"],
    )
    op.create_unique_constraint(
        "uq_test_template_versions_template_version",
        "test_template_versions",
        ["template_id", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_test_template_versions_template_version",
        "test_template_versions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_dataset_versions_dataset_version",
        "dataset_versions",
        type_="unique",
    )
