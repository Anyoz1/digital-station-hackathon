"""H7–H10: durable optimization runs and independently validated alternatives."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_planner"
down_revision = "0002_simulation_runtime"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "optimization_run",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("base_revision", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("elapsed_ms", sa.Float(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','no_feasible_plan','stale','timeout','failed')",
            name="valid_optimization_status",
        ),
    )
    op.create_index("ix_optimization_run_run_id", "optimization_run", ["run_id"])
    op.create_table(
        "plan",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("optimization_run_id", sa.String(), sa.ForeignKey("optimization_run.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("validity", sa.String(), nullable=False),
        sa.Column("base_revision", sa.BigInteger(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("objective", sa.Float()),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed','active','superseded','stale','rejected')", name="valid_plan_status"
        ),
        sa.CheckConstraint("validity IN ('feasible','invalid')", name="valid_plan_validity"),
    )
    op.create_index("ix_plan_run_id", "plan", ["run_id"])
    op.create_index("ix_plan_optimization_run_id", "plan", ["optimization_run_id"])


def downgrade():
    op.drop_table("plan")
    op.drop_table("optimization_run")
