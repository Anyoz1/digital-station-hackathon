"""API v1.0 DTOs; structural validation only, not the future plan validator."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["viewer", "operator", "dispatcher", "admin"]
TrainType = Literal["FREIGHT", "PASSENGER", "SERVICE", "OTHER"]
ServiceProfileId = Literal[
    "freight_transit_v1",
    "freight_local_v1",
    "freight_reclassify_v1",
    "passenger_transit_v1",
    "service_transit_v1",
]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RenderTelemetry(DTO):
    run_id: str = Field(min_length=1, max_length=100)
    event_seq: int = Field(ge=1, strict=True)
    client_id: UUID
    received_client_ms: float = Field(ge=0, allow_inf_nan=False)
    rendered_client_ms: float = Field(ge=0, allow_inf_nan=False)
    offset_ms: float = Field(allow_inf_nan=False)
    uncertainty_ms: float = Field(ge=0, allow_inf_nan=False)
    visible: bool = Field(strict=True)


class ErrorPayload(DTO):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str | None


class APIError(DTO):
    error: ErrorPayload


class PhysicalLocation(DTO):
    kind: Literal["boundary", "track", "route", "departed"]
    boundary_id: str | None = None
    track_id: str | None = None
    route_id: str | None = None
    operation_id: str | None = None
    route_progress: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_location(self):
        required = {
            "boundary": {"boundary_id"},
            "track": {"track_id"},
            "route": {"route_id", "operation_id", "route_progress"},
            "departed": set(),
        }[self.kind]
        present = {
            name
            for name in ("boundary_id", "track_id", "route_id", "operation_id", "route_progress")
            if getattr(self, name) is not None
        }
        if present != required:
            raise ValueError(f"Invalid fields for location kind {self.kind}")
        return self


class Park(DTO):
    id: str
    name: str
    kind: Literal["receiving_departure", "sorting", "cargo", "service"]


class Node(DTO):
    id: str
    kind: Literal["boundary", "switch_zone", "track_end"]
    x: float
    y: float
    switch_ids: list[str]


class Edge(DTO):
    id: str
    from_node_id: str
    to_node_id: str
    track_id: str | None
    zone_id: str | None
    bidirectional: bool


class TrackGeometry(DTO):
    track_id: str
    points: list[tuple[float, float]] = Field(min_length=2)


class RouteGeometry(DTO):
    id: str
    from_id: str
    to_id: str
    track_ids: list[str]
    zone_ids: list[str]
    points: list[tuple[float, float]] = Field(min_length=2)


class Layout(DTO):
    view_box: tuple[float, float, float, float]
    nodes: list[Node]
    edges: list[Edge]
    tracks: list[TrackGeometry]
    routes: list[RouteGeometry]


class Station(DTO):
    id: str
    name: str
    parks: list[Park]
    layout: Layout


class Track(DTO):
    id: str
    name: str
    park_id: str
    kind: Literal["receiving_departure", "lead", "sorting", "cargo", "parking"]
    length_m: float = Field(gt=0)
    availability: Literal["open", "closed", "closure_pending"]
    occupied_length_m: float = Field(ge=0)
    train_ids: list[str]
    group_ids: list[str]
    locomotive_ids: list[str]
    assigned_train_id: str | None
    active_operation_ids: list[str]


class Zone(DTO):
    id: Literal["W", "E"]
    active_operation_id: str | None
    availability: Literal["open", "closed"]


class Train(DTO):
    id: str
    number: str
    type: TrainType
    service_profile_id: ServiceProfileId
    processing_kind: Literal["transit", "local", "reclassify"]
    direction: Literal["W_E", "E_W"]
    consist_kind: Literal["wagon_groups", "fixed"]
    body_length_m: float = Field(gt=0)
    total_length_m: float = Field(gt=0)
    location: PhysicalLocation
    priority: Literal[1, 2, 3]
    status: Literal["expected", "waiting_entry", "on_station", "ready_departure", "departed"]
    destination_id: str
    scheduled_arrival_sim_s: int = Field(ge=0)
    expected_arrival_sim_s: int = Field(ge=0)
    due_departure_sim_s: int = Field(ge=0)
    actual_arrival_sim_s: int | None
    actual_departure_sim_s: int | None
    group_ids: list[str]
    target_group_ids: list[str] = Field(default_factory=list)
    traction_resource_id: str
    traction_kind: Literal["locomotive", "self_propelled"]
    planned_track_id: str | None
    current_track_id: str | None

    @model_validator(mode="after")
    def consistent_consist(self):
        if self.consist_kind == "fixed" and (self.group_ids or self.target_group_ids):
            raise ValueError("Fixed consist cannot require wagon groups")
        if self.total_length_m < self.body_length_m:
            raise ValueError("Total length cannot be shorter than body")
        return self


class WagonGroup(DTO):
    id: str
    origin_train_id: str
    assigned_train_id: str
    current_train_id: str | None
    wagon_ids: list[str] = Field(min_length=1)
    wagon_count: int = Field(gt=0)
    length_m: float = Field(gt=0)
    destination_id: str
    cargo_state: Literal["unloaded", "loaded", "not_applicable"]
    location: PhysicalLocation

    @model_validator(mode="after")
    def count_matches(self):
        if self.wagon_count != len(self.wagon_ids) or len(set(self.wagon_ids)) != self.wagon_count:
            raise ValueError("Wagon count/identity mismatch")
        return self


class Resource(DTO):
    id: str
    name: str
    kind: Literal[
        "shunting_locomotive",
        "train_locomotive",
        "self_propelled_unit",
        "shunting_crew",
        "inspection_crew",
        "cargo_crew",
        "train_crew",
    ]
    status: Literal["available", "busy", "unavailable", "unavailable_pending"]
    active_operation_id: str | None
    location: PhysicalLocation | None
    available_after_sim_s: int | None
    assigned_user_id: str | None


class Operation(DTO):
    id: str
    train_id: str
    group_ids: list[str]
    kind: Literal["arrival", "inspection", "shunt_transfer", "cargo", "departure_prep", "departure"]
    status: Literal["pending", "planned", "running", "completed", "blocked"]
    execution_mode: Literal["auto", "manual"]
    predecessor_ids: list[str]
    source_track_id: str | None
    target_track_id: str | None
    route_ids: list[str]
    resource_ids: list[str]
    duration_sim_s: int = Field(gt=0)
    start_sim_s: int | None
    end_sim_s: int | None
    actual_start_sim_s: int | None
    actual_end_sim_s: int | None
    progress: float = Field(ge=0, le=1)
    phase: (
        Literal[
            "empty_to_source",
            "couple",
            "pull_to_lead",
            "reverse",
            "push_to_target",
            "uncouple",
            "return_to_depot",
        ]
        | None
    )
    blocked_reason_codes: list[str]
    assigned_user_id: str | None
    can_complete: bool


class Incident(DTO):
    id: str
    kind: Literal["train_delay", "track_closure", "resource_loss", "destination_block"]
    target_id: str
    status: Literal["active", "pending", "resolved"]
    created_at: str
    starts_sim_s: int
    ends_sim_s: int
    delay_sim_s: int | None
    affected_operation_ids: list[str]
    description: str


class Conflict(DTO):
    id: str
    kind: Literal[
        "route_overlap",
        "track_occupied",
        "resource_unavailable",
        "precedence",
        "capacity",
        "destination_closed",
    ]
    severity: Literal["warning", "critical"]
    reason_code: str
    entity_ids: list[str]
    operation_ids: list[str]
    message: str
    recommendation: str
    detected_at: str


class Factor(DTO):
    key: Literal["throughput", "delay", "occupancy", "conflicts", "resource_idle"]
    raw: float | None
    unit: str
    norm_penalty: float | None = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float | None
    reason: str


class Efficiency(DTO):
    mode: Literal["actual", "forecast"]
    formula_version: str
    window_start_sim_s: int
    window_end_sim_s: int
    score: float | None = Field(ge=0, le=100)
    category: Literal["normal", "attention", "critical"] | None
    factors: list[Factor]


class PlanSummary(DTO):
    id: str
    status: Literal["proposed", "active", "superseded", "stale", "rejected"]
    validity: Literal["feasible", "invalid"]
    optimization_run_id: str
    strategy: str
    base_input_revision: int
    base_state_version: int
    base_active_plan_id: str | None
    config_version: int
    created_at: str
    cutover_sim_s: int
    forecast: Efficiency
    objective_value: float | None
    changed_operation_ids: list[str]
    explanation: list[str]
    can_apply: bool


class ReplanJob(DTO):
    id: str
    status: Literal["queued", "running", "succeeded", "no_feasible_plan", "stale", "timeout", "failed"]
    reason: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    base_input_revision: int
    candidate_plan_ids: list[str]
    applied_plan_id: str | None
    elapsed_ms: float
    compute_ms: float | None
    validation_ms: float | None
    deadline_ms: Literal[5000]
    outcome_reason_codes: list[str]


class State(DTO):
    schema_version: Literal["1.0"]
    run_id: str
    scenario_id: str
    scenario_epoch: str
    event_seq: int = Field(ge=0)
    state_version: int = Field(ge=0)
    input_revision: int = Field(ge=0)
    config_version: int = Field(ge=1)
    server_time: str
    sim_time_s: int = Field(ge=0)
    speed: Literal[1, 5, 10]
    mode: Literal["paused", "running"]
    station: Station
    active_plan_id: str | None
    tracks: list[Track]
    zones: list[Zone]
    trains: list[Train]
    wagon_groups: list[WagonGroup]
    resources: list[Resource]
    operations: list[Operation]
    incidents: list[Incident]
    conflicts: list[Conflict]
    plans: list[PlanSummary]
    last_replan: ReplanJob | None
    efficiency: Efficiency

    @model_validator(mode="after")
    def structural_references(self):
        maps: dict[str, dict[str, Any]] = {}
        for name in ("tracks", "trains", "wagon_groups", "resources", "operations", "zones"):
            items = getattr(self, name)
            maps[name] = {item.id: item for item in items}
            if len(maps[name]) != len(items):
                raise ValueError(f"Duplicate {name} IDs")
        routes = {route.id for route in self.station.layout.routes}
        parks = {park.id for park in self.station.parks}
        wagons = [wid for group in self.wagon_groups for wid in group.wagon_ids]
        if len(wagons) != len(set(wagons)):
            raise ValueError("A wagon occurs in multiple groups")
        for track in self.tracks:
            if track.park_id not in parks or track.occupied_length_m > track.length_m:
                raise ValueError("Invalid track park/capacity")
            for field, collection in (
                ("train_ids", "trains"),
                ("group_ids", "wagon_groups"),
                ("locomotive_ids", "resources"),
                ("active_operation_ids", "operations"),
            ):
                if not set(getattr(track, field)) <= maps[collection].keys():
                    raise ValueError(f"Unknown track {field}")
        for train in self.trains:
            if train.traction_resource_id not in maps["resources"]:
                raise ValueError("Unknown traction")
            if not set(train.group_ids + train.target_group_ids) <= maps["wagon_groups"].keys():
                raise ValueError("Unknown train group")
        for group in self.wagon_groups:
            if group.origin_train_id not in maps["trains"] or group.assigned_train_id not in maps["trains"]:
                raise ValueError("Unknown group train")
            if group.current_train_id is not None:
                attached_train = maps["trains"].get(group.current_train_id)
                if (
                    attached_train is None
                    or group.id not in attached_train.group_ids
                    or group.location != attached_train.location
                ):
                    raise ValueError("Attached group membership/location mismatch")
        for op in self.operations:
            if op.train_id not in maps["trains"]:
                raise ValueError("Unknown operation train")
            for ids, known in (
                (op.group_ids, maps["wagon_groups"]),
                (op.resource_ids, maps["resources"]),
                (op.predecessor_ids, maps["operations"]),
                (op.route_ids, routes),
            ):
                if not set(ids) <= set(known):
                    raise ValueError("Unknown operation reference")
        return self


class User(DTO):
    id: str
    display_name: str
    role: Role
    resource_ids: list[str]


class AuthResponse(DTO):
    user: User


class LoginInput(DTO):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(
        min_length=1, max_length=200, json_schema_extra={"format": "password", "writeOnly": True}
    )


class SimulationCommand(DTO):
    request_id: str
    run_id: str
    expected_input_revision: int = Field(ge=0)
    action: Literal["play", "pause", "step", "set_speed"]
    speed: Literal[1, 5, 10] | None = None

    @model_validator(mode="after")
    def command_fields(self):
        UUID(self.request_id)
        if (self.action == "set_speed") != (self.speed is not None):
            raise ValueError("speed is required only for set_speed")
        return self


class CommandReceiptResponse(DTO):
    request_id: str
    run_id: str
    input_revision: int
    state_version: int
    result: dict[str, Any]


class Config(DTO):
    config_version: int
    weights: dict[str, float]
    category_thresholds: dict[str, float]
    planner: dict[str, int]
    units: dict[str, str]


class CommandEnvelope(DTO):
    request_id: str
    run_id: str
    expected_input_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_uuid(self):
        UUID(self.request_id)
        return self


class NewRunInput(CommandEnvelope):
    scenario_id: str = Field(min_length=1, max_length=100)
    seed: int = Field(ge=0, le=2147483647, strict=True)


class ReplanInput(CommandEnvelope):
    reason: Literal["manual"]


class IncidentInput(DTO):
    kind: Literal["train_delay", "track_closure", "resource_loss", "destination_block"]
    target_id: str
    duration_sim_s: int = Field(ge=1, le=7200)
    delay_sim_s: int | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def delay_for_train_only(self):
        if (self.kind == "train_delay") != (self.delay_sim_s is not None):
            raise ValueError("delay_sim_s is required only for train_delay")
        return self


class IncidentBatchInput(CommandEnvelope):
    items: list[IncidentInput] = Field(min_length=1, max_length=10)


ConfigNumber = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)]


class ConfigWeights(DTO):
    throughput: ConfigNumber
    delay: ConfigNumber
    occupancy: ConfigNumber
    conflicts: ConfigNumber
    resource_idle: ConfigNumber

    @model_validator(mode="after")
    def total_one(self):
        values = self.model_dump().values()
        if any(v > 1 for v in values) or abs(sum(values) - 1) > 1e-9:
            raise ValueError("Веса должны быть неотрицательны и суммироваться к 1")
        return self


class ConfigThresholds(DTO):
    normal_min: ConfigNumber
    attention_min: ConfigNumber

    @model_validator(mode="after")
    def ordered(self):
        if not self.attention_min < self.normal_min:
            raise ValueError("Нужно 0 <= attention_min < normal_min <= 100")
        return self


class PlannerLimits(DTO):
    time_limit_ms: int = Field(ge=100, le=3000, strict=True)
    max_rollouts: int = Field(ge=1, le=12, strict=True)


class ConfigPatch(DTO):
    weights: ConfigWeights | None = None
    category_thresholds: ConfigThresholds | None = None
    planner: PlannerLimits | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Patch должен содержать целый ненулевой вложенный объект")
        return self


class ConfigPatchInput(CommandEnvelope):
    patch: ConfigPatch


class HistoryEvent(DTO):
    run_id: str
    seq: int
    server_time: str
    sim_time_s: int
    kind: str
    entity_ids: list[str]
    message: str
    actor_user_id: str | None


class HistoryPage(DTO):
    items: list[HistoryEvent]
    next_from_seq: int
    has_more: bool
    anchor_seq: int
    available_from_wall_time: str
    available_to_wall_time: str


class ValidatorError(DTO):
    code: str
    entity_ids: list[str]
    message: str


class ValidatorReport(DTO):
    passed: bool
    errors: list[ValidatorError]


class PlanDetail(PlanSummary):
    operations: list[Operation]
    validator: ValidatorReport


class ReplanDetail(DTO):
    job: ReplanJob
    plans: list[PlanDetail]
