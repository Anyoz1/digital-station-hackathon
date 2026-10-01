"""H2–H5: smoke schedule provenance and durable simulation command receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_simulation_runtime"
down_revision = "0001_h0_foundation"
branch_labels = depends_on = None


def upgrade():
    op.add_column("run", sa.Column("execution_schedule_id", sa.String(), nullable=True))
    op.create_table(
        "command_receipt",
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("request_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("command_receipt")
    op.drop_column("run", "execution_schedule_id")
