"""H0 foundation: users/sessions, initial run/state, JSONB and initial audit checkpoint.

Revision ID: 0001_h0_foundation
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_h0_foundation"
down_revision = None
branch_labels = None
depends_on = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("resource_ids", JSONB, nullable=False),
        sa.CheckConstraint("role IN ('viewer','operator','dispatcher','admin')", name="valid_role"),
    )
    op.create_table(
        "auth_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])
    op.create_table(
        "scenario",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_table(
        "config_revision",
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("app_user.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_table(
        "run",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("scenario_version", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["scenario_id", "scenario_version"], ["scenario.id", "scenario.version"]),
        sa.CheckConstraint("status IN ('paused','running','closed')", name="valid_run_status"),
    )
    op.create_table(
        "run_state",
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("input_revision", sa.BigInteger(), nullable=False),
        sa.Column("config_version", sa.Integer(), sa.ForeignKey("config_revision.version"), nullable=False),
        sa.Column("sim_time_s", sa.BigInteger(), nullable=False),
        sa.Column("active_plan_id", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.CheckConstraint(
            "state_version >= 0 AND input_revision >= 0 AND sim_time_s >= 0", name="nonnegative_state"
        ),
    )
    op.create_table(
        "domain_event",
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("source_event_id", sa.String(), nullable=False, unique=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sim_time_s", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("app_user.id")),
        sa.Column("request_id", sa.String()),
        sa.Column("payload", JSONB, nullable=False),
        sa.CheckConstraint("seq >= 0", name="nonnegative_seq"),
    )
    op.create_index("ix_domain_event_received_at", "domain_event", ["received_at"])
    op.create_table(
        "state_snapshot",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sim_time_s", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["run_id", "seq"], ["domain_event.run_id", "domain_event.seq"]),
    )
    op.create_index("ix_state_snapshot_created_at", "state_snapshot", ["created_at"])


def downgrade():
    for table in (
        "state_snapshot",
        "domain_event",
        "run_state",
        "run",
        "config_revision",
        "scenario",
        "auth_session",
        "app_user",
    ):
        op.drop_table(table)
