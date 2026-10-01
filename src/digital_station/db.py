from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("role IN ('viewer','operator','dispatcher','admin')", name="valid_role"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean)
    resource_ids: Mapped[list] = mapped_column(JSONB)


class AuthSession(Base):
    __tablename__ = "auth_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Scenario(Base):
    __tablename__ = "scenario"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


class ConfigRevision(Base):
    __tablename__ = "config_revision"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (
        ForeignKeyConstraint(["scenario_id", "scenario_version"], ["scenario.id", "scenario.version"]),
        CheckConstraint("status IN ('paused','running','closed')", name="valid_run_status"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String)
    scenario_version: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_schedule_id: Mapped[str | None] = mapped_column(String)


class RunState(Base):
    __tablename__ = "run_state"
    __table_args__ = (
        CheckConstraint(
            "state_version >= 0 AND input_revision >= 0 AND sim_time_s >= 0", name="nonnegative_state"
        ),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), primary_key=True)
    state_version: Mapped[int] = mapped_column(BigInteger)
    input_revision: Mapped[int] = mapped_column(BigInteger)
    config_version: Mapped[int] = mapped_column(ForeignKey("config_revision.version"))
    sim_time_s: Mapped[int] = mapped_column(BigInteger)
    active_plan_id: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


class DomainEvent(Base):
    __tablename__ = "domain_event"
    __table_args__ = (
        UniqueConstraint("source_event_id"),
        CheckConstraint("seq >= 0", name="nonnegative_seq"),
        Index("ix_domain_event_run_wall_seq", "run_id", "received_at", "seq"),
        Index("ix_domain_event_run_sim_seq", "run_id", "sim_time_s", "seq"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sim_time_s: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    request_id: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)


class StateSnapshot(Base):
    __tablename__ = "state_snapshot"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "seq"], ["domain_event.run_id", "domain_event.seq"]),
        Index("ix_state_snapshot_run_wall_seq", "run_id", "created_at", "seq"),
    )
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sim_time_s: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)


class CommandReceipt(Base):
    __tablename__ = "command_receipt"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"))
    body_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


class OptimizationRun(Base):
    __tablename__ = "optimization_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','no_feasible_plan','stale','timeout','failed')",
            name="valid_optimization_status",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    status: Mapped[str] = mapped_column(String)
    base_revision: Mapped[int] = mapped_column(BigInteger)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONB)


class PlanRecord(Base):
    __tablename__ = "plan"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','active','superseded','stale','rejected')", name="valid_plan_status"
        ),
        CheckConstraint("validity IN ('feasible','invalid')", name="valid_plan_validity"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    optimization_run_id: Mapped[str] = mapped_column(ForeignKey("optimization_run.id"), index=True)
    status: Mapped[str] = mapped_column(String)
    validity: Mapped[str] = mapped_column(String)
    base_revision: Mapped[int] = mapped_column(BigInteger)
    config_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    objective: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONB)


class IncidentRecord(Base):
    __tablename__ = "incident"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('train_delay','track_closure','resource_loss','destination_block')",
            name="valid_incident_kind",
        ),
        CheckConstraint("status IN ('active','pending','resolved')", name="valid_incident_status"),
        CheckConstraint("starts_sim_s >= 0 AND ends_sim_s > starts_sim_s", name="valid_incident_interval"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    target_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_sim_s: Mapped[int] = mapped_column(BigInteger)
    ends_sim_s: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)


class TelemetryObservation(Base):
    __tablename__ = "telemetry_observation"
    __table_args__ = (
        UniqueConstraint("run_id", "source_event_id", name="uq_observation_run_event"),
        Index("ix_observation_source_order", "run_id", "source_id", "operation_id", "source_seq"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id"))
    source_event_id: Mapped[str | None] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(String)
    operation_id: Mapped[str | None] = mapped_column(String)
    source_seq: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)


def database(url: str):
    engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
