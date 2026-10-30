"""add cross-table integrity constraints and retest idempotency"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260913_integrity_and_retest"
down_revision: str | None = "20260912_input_length_preset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("retest_idempotency_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_tasks_retest_idempotency_key", "tasks", ["retest_idempotency_key"])
    op.create_foreign_key(
        "fk_tasks_template_version_id_test_template_versions",
        "tasks",
        "test_template_versions",
        ["template_version_id"],
        ["id"],
    )
    op.create_foreign_key("fk_runs_task_id_tasks", "runs", "tasks", ["task_id"], ["id"])
    op.create_foreign_key(
        "fk_runs_model_deployment_id_model_deployments",
        "runs",
        "model_deployments",
        ["model_deployment_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_test_template_versions_dataset_version_id_dataset_versions",
        "test_template_versions",
        "dataset_versions",
        ["dataset_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_test_template_versions_tokenizer_id_tokenizers",
        "test_template_versions",
        "tokenizers",
        ["tokenizer_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_model_deployments_comparison_group_id_comparison_groups",
        "model_deployments",
        "comparison_groups",
        ["comparison_group_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_model_deployments_tokenizer_id_tokenizers",
        "model_deployments",
        "tokenizers",
        ["tokenizer_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_comparison_groups_default_tokenizer_id_tokenizers",
        "comparison_groups",
        "tokenizers",
        ["default_tokenizer_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_comparison_groups_default_tokenizer_id_tokenizers", "comparison_groups", type_="foreignkey")
    op.drop_constraint("fk_model_deployments_tokenizer_id_tokenizers", "model_deployments", type_="foreignkey")
    op.drop_constraint("fk_model_deployments_comparison_group_id_comparison_groups", "model_deployments", type_="foreignkey")
    op.drop_constraint("fk_test_template_versions_tokenizer_id_tokenizers", "test_template_versions", type_="foreignkey")
    op.drop_constraint("fk_test_template_versions_dataset_version_id_dataset_versions", "test_template_versions", type_="foreignkey")
    op.drop_constraint("fk_runs_model_deployment_id_model_deployments", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_task_id_tasks", "runs", type_="foreignkey")
    op.drop_constraint("fk_tasks_template_version_id_test_template_versions", "tasks", type_="foreignkey")
    op.drop_constraint("uq_tasks_retest_idempotency_key", "tasks", type_="unique")
    op.drop_column("tasks", "retest_idempotency_key")
