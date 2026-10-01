"""H10–H12: incident records in the same transaction as State/events/receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_incidents"
down_revision = "0003_planner"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "incident",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starts_sim_s", sa.BigInteger(), nullable=False),
        sa.Column("ends_sim_s", sa.BigInteger(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('train_delay','track_closure','resource_loss','destination_block')",
            name="valid_incident_kind",
        ),
        sa.CheckConstraint("status IN ('active','pending','resolved')", name="valid_incident_status"),
        sa.CheckConstraint("starts_sim_s >= 0 AND ends_sim_s > starts_sim_s", name="valid_incident_interval"),
    )
    op.create_index("ix_incident_run_id", "incident", ["run_id"])


def downgrade():
    op.drop_table("incident")
