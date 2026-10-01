"""H12–H15: indexes for durable wall-window history and checkpoint reduction."""

from alembic import op

revision = "0005_history_indexes"
down_revision = "0004_incidents"
branch_labels = depends_on = None


def upgrade():
    op.create_index("ix_domain_event_run_wall_seq", "domain_event", ["run_id", "received_at", "seq"])
    op.create_index("ix_domain_event_run_sim_seq", "domain_event", ["run_id", "sim_time_s", "seq"])
    op.create_index("ix_state_snapshot_run_wall_seq", "state_snapshot", ["run_id", "created_at", "seq"])


def downgrade():
    op.drop_index("ix_state_snapshot_run_wall_seq", table_name="state_snapshot")
    op.drop_index("ix_domain_event_run_sim_seq", table_name="domain_event")
    op.drop_index("ix_domain_event_run_wall_seq", table_name="domain_event")
