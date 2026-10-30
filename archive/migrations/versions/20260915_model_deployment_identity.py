"""enforce one model deployment per connection and model id"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260915_model_deployment_uq"
down_revision: str | None = "20260914_evalscope_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_model_deployments_connection_model",
        "model_deployments",
        ["connection_id", "model_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_model_deployments_connection_model",
        "model_deployments",
        type_="unique",
    )
