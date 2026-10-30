"""add built-in input length presets to template versions"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260912_input_length_preset"
down_revision: Union[str, None] = "20260911_pin_sandbox_image"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_template_versions",
        sa.Column("input_length_preset", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_test_template_versions_input_length_preset"),
        "test_template_versions",
        "input_length_preset IS NULL OR input_length_preset IN ('short', 'medium', 'long', 'extra_long')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_test_template_versions_input_length_preset"),
        "test_template_versions",
        type_="check",
    )
    op.drop_column("test_template_versions", "input_length_preset")
