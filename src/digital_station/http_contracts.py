"""Schemas for already-existing transport responses; no domain/planning changes."""

from typing import Literal

from .contracts import DTO, ServiceProfileId


class DurationStats(DTO):
    sample_count: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class ClientRenderMetrics(DTO):
    client_id: str
    latency_upper: DurationStats
    receive_to_render: DurationStats
    invalid_samples: int
    hidden_samples: int
    unreported_events: int
    latency_exceedances: int


class ReplanMetrics(DTO):
    elapsed: DurationStats
    compute: DurationStats
    succeeded: int
    no_feasible_plan: int
    stale: int
    timeout: int
    failed: int
    deadline_exceedances: int


class MetricsResponse(DTO):
    run_id: str
    window_started_at: str
    measured_at: str
    published_events: int
    connected_clients: int
    actor_queue_depth: int
    planner_running_jobs: int
    planner_pending_jobs: int
    ui_render: list[ClientRenderMetrics]
    replans: ReplanMetrics


class ClockProbe(DTO):
    server_received_ms: float
    server_sent_ms: float


class ScenarioSummary(DTO):
    id: str
    name: str
    description: str
    train_count: int
    horizon_sim_s: int
    enabled_service_profiles: list[ServiceProfileId]


class ScenariosResponse(DTO):
    items: list[ScenarioSummary]


class HealthLive(DTO):
    status: Literal["alive"]
    stage: str


class HealthReady(DTO):
    status: Literal["ready"]
    stage: str
    checks: dict[str, str]
    capabilities: dict[str, bool]


class OperationDiff(DTO):
    operation_id: str
    train_id: str
    label: str
    before_start_sim_s: int | None
    after_start_sim_s: int | None
    shift_sim_s: int | None
    before_track_id: str | None
    after_track_id: str | None
    before_resource_ids: list[str]
    after_resource_ids: list[str]


class DepartureExplanation(DTO):
    train_id: str
    departure_sim_s: int | None
    delay_sim_s: int | None
    basis: Literal["actual", "forecast"]


class ExplainedPlan(DTO):
    plan_id: str
    changes: list[OperationDiff]
    departures: list[DepartureExplanation]
    reason: str


class ExplanationAvailable(DTO):
    available: Literal[True]
    run_id: str
    replan_id: str
    base_event_seq: int
    base_plan_id: str | None
    plans: list[ExplainedPlan]


class ExplanationUnavailable(DTO):
    available: Literal[False]
    reason: str
    plans: list[ExplainedPlan]
