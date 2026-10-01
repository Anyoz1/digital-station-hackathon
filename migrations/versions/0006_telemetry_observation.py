"""Non-authoritative mock-source observations; no change to domain State v1.0."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_telemetry_observation"
down_revision = "0005_history_indexes"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "telemetry_observation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("source_event_id", sa.String()),
        sa.Column("source_id", sa.String()),
        sa.Column("operation_id", sa.String()),
        sa.Column("source_seq", sa.BigInteger()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "source_event_id", name="uq_observation_run_event"),
    )
    op.create_index("ix_telemetry_observation_received_at", "telemetry_observation", ["received_at"])
    op.create_index(
        "ix_observation_source_order",
        "telemetry_observation",
        ["run_id", "source_id", "operation_id", "source_seq"],
    )


def downgrade():
    op.drop_table("telemetry_observation")
