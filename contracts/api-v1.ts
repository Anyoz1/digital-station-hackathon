// Generated from frozen FRONTEND_HANDOFF v1.0.
export type Role = "viewer" | "operator" | "dispatcher" | "admin";
export type Mode = "paused" | "running";
export type TrainType = "FREIGHT" | "PASSENGER" | "SERVICE" | "OTHER";
export type Direction = "W_E" | "E_W";
export type ServiceProfileId = "freight_transit_v1" | "freight_local_v1"
  | "freight_reclassify_v1" | "passenger_transit_v1" | "service_transit_v1";
export type ConsistKind = "wagon_groups" | "fixed";
export type TractionKind = "locomotive" | "self_propelled";
export type ProcessingKind = "transit" | "local" | "reclassify";
export type TrainStatus = "expected" | "waiting_entry" | "on_station"
  | "ready_departure" | "departed";
export type OperationKind = "arrival" | "inspection" | "shunt_transfer"
  | "cargo" | "departure_prep" | "departure";
export type OperationStatus = "pending" | "planned" | "running" | "completed" | "blocked";
export type ExecutionMode = "auto" | "manual";
export type ShuntPhase = "empty_to_source" | "couple" | "pull_to_lead"
  | "reverse" | "push_to_target" | "uncouple" | "return_to_depot";
export type TrackAvailability = "open" | "closed" | "closure_pending";
export type ResourceKind = "shunting_locomotive" | "train_locomotive" | "self_propelled_unit" | "shunting_crew"
  | "inspection_crew" | "cargo_crew" | "train_crew";
export type ResourceStatus = "available" | "busy" | "unavailable" | "unavailable_pending";
export type PlanStatus = "proposed" | "active" | "superseded" | "stale" | "rejected";
export type PlanValidity = "feasible" | "invalid";
export type ReplanStatus = "queued" | "running" | "succeeded" | "no_feasible_plan"
  | "stale" | "timeout" | "failed";
export type IncidentKind = "train_delay" | "track_closure" | "resource_loss" | "destination_block";
export type IncidentStatus = "active" | "pending" | "resolved";
export type ConflictKind = "route_overlap" | "track_occupied" | "resource_unavailable"
  | "precedence" | "capacity" | "destination_closed";
export type Severity = "warning" | "critical";
export type EfficiencyCategory = "normal" | "attention" | "critical";

export interface State {
  schema_version: "1.0";
  run_id: string; scenario_id: string; scenario_epoch: string;
  event_seq: number; state_version: number; input_revision: number; config_version: number;
  server_time: string; sim_time_s: number; speed: 1 | 5 | 10; mode: Mode;
  station: Station; active_plan_id: string | null;
  tracks: Track[]; zones: Zone[]; trains: Train[]; wagon_groups: WagonGroup[];
  resources: Resource[]; operations: Operation[];
  incidents: Incident[]; conflicts: Conflict[]; plans: PlanSummary[];
  last_replan: ReplanJob | null; efficiency: Efficiency;
}
export interface Station {
  id: string; name: string;
  parks: {id: string; name: string; kind: "receiving_departure" | "sorting" | "cargo" | "service"}[];
  layout: {
    view_box: [number, number, number, number];
    nodes: {id: string; kind: "boundary" | "switch_zone" | "track_end";
      x: number; y: number; switch_ids: string[]}[];
    edges: {id: string; from_node_id: string; to_node_id: string;
      track_id: string | null; zone_id: string | null; bidirectional: boolean}[];
    tracks: {track_id: string; points: [number, number][]}[];
    routes: {id: string; from_id: string; to_id: string;
      track_ids: string[]; zone_ids: string[]; points: [number, number][]}[];
  };
}
export interface Track {
  id: string; name: string; park_id: string;
  kind: "receiving_departure" | "lead" | "sorting" | "cargo" | "parking";
  length_m: number; availability: TrackAvailability;
  occupied_length_m: number; train_ids: string[]; group_ids: string[]; locomotive_ids: string[];
  assigned_train_id: string | null; active_operation_ids: string[];
}
export interface Zone {
  id: "W" | "E"; active_operation_id: string | null;
  availability: "open" | "closed";
}
export interface Train {
  id: string; number: string; type: TrainType; service_profile_id: ServiceProfileId;
  processing_kind: ProcessingKind; direction: Direction;
  consist_kind: ConsistKind; body_length_m: number; total_length_m: number;
  location: PhysicalLocation;
  priority: 1 | 2 | 3; status: TrainStatus; destination_id: string;
  scheduled_arrival_sim_s: number; expected_arrival_sim_s: number;
  due_departure_sim_s: number; actual_arrival_sim_s: number | null;
  actual_departure_sim_s: number | null; group_ids: string[];
  target_group_ids?: string[];
  traction_resource_id: string; traction_kind: TractionKind;
  planned_track_id: string | null; current_track_id: string | null;
}
export interface PhysicalLocation {
  kind: "boundary" | "track" | "route" | "departed";
  boundary_id: string | null; track_id: string | null; route_id: string | null;
  operation_id: string | null; route_progress: number | null;
}
export interface WagonGroup {
  id: string; origin_train_id: string; assigned_train_id: string;
  current_train_id: string | null; wagon_ids: string[];
  wagon_count: number; length_m: number; destination_id: string;
  cargo_state: "unloaded" | "loaded" | "not_applicable";
  location: PhysicalLocation;
}
export interface Resource {
  id: string; name: string; kind: ResourceKind; status: ResourceStatus;
  active_operation_id: string | null;
  location: PhysicalLocation | null;
  available_after_sim_s: number | null; assigned_user_id: string | null;
}
export interface Operation {
  id: string; train_id: string; group_ids: string[]; kind: OperationKind;
  status: OperationStatus; execution_mode: ExecutionMode;
  predecessor_ids: string[]; source_track_id: string | null; target_track_id: string | null;
  route_ids: string[]; resource_ids: string[];
  duration_sim_s: number; start_sim_s: number | null; end_sim_s: number | null;
  actual_start_sim_s: number | null; actual_end_sim_s: number | null;
  progress: number; phase: ShuntPhase | null; blocked_reason_codes: string[];
  assigned_user_id: string | null; can_complete: boolean;
}

export interface Incident {
  id: string; kind: IncidentKind; target_id: string; status: IncidentStatus;
  created_at: string; starts_sim_s: number; ends_sim_s: number;
  delay_sim_s: number | null; affected_operation_ids: string[]; description: string;
}
export interface Conflict {
  id: string; kind: ConflictKind; severity: Severity; reason_code: string;
  entity_ids: string[]; operation_ids: string[]; message: string;
  recommendation: string; detected_at: string;
}
export interface Efficiency {
  mode: "actual" | "forecast"; formula_version: string;
  window_start_sim_s: number; window_end_sim_s: number;
  score: number | null; category: EfficiencyCategory | null;
  factors: {
    key: "throughput" | "delay" | "occupancy" | "conflicts" | "resource_idle";
    raw: number | null; unit: string; norm_penalty: number | null;
    weight: number; contribution: number | null; reason: string;
  }[];
}
export interface PlanSummary {
  id: string; status: PlanStatus; validity: PlanValidity;
  optimization_run_id: string;
  strategy: string; base_input_revision: number; base_state_version: number;
  base_active_plan_id: string | null;
  config_version: number; created_at: string; cutover_sim_s: number;
  forecast: Efficiency; objective_value: number | null;
  changed_operation_ids: string[]; explanation: string[];
  can_apply: boolean;
}
export interface PlanDetail extends PlanSummary {
  operations: Operation[];
  validator: {passed: boolean; errors: {code: string; entity_ids: string[]; message: string}[]};
}
export interface ReplanJob {
  id: string; status: ReplanStatus; reason: string;
  created_at: string; started_at: string | null; finished_at: string | null;
  base_input_revision: number; candidate_plan_ids: string[]; applied_plan_id: string | null;
  elapsed_ms: number; compute_ms: number | null;
  validation_ms: number | null; deadline_ms: 5000;
  outcome_reason_codes: string[];
}

export interface CommandEnvelope {
  request_id: string;
  run_id: string;
  expected_input_revision: number;
}
export interface CommandReceipt {
  request_id: string; run_id: string;
  input_revision: number; state_version: number;
  result: Record<string, unknown>;
}

export interface Config {
  config_version: number;
  weights: {throughput: number; delay: number; occupancy: number; conflicts: number; resource_idle: number};
  category_thresholds: {normal_min: number; attention_min: number};
  planner: {time_limit_ms: number; max_rollouts: number};
  units: {simulation_time: "seconds"; real_time: "milliseconds"; length: "meters"};
}

export interface IncidentInput {
  kind: IncidentKind;
  target_id: string;
  duration_sim_s: number; // integer, 1..7200
  delay_sim_s?: number; // integer, 1..3600, только train_delay
}

export interface HistoryEvent {
  run_id: string; seq: number; server_time: string; sim_time_s: number;
  kind: string; entity_ids: string[]; message: string;
  actor_user_id: string | null;
}

export interface DurationStats {
  sample_count: number;
  p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}
export interface Metrics {
  run_id: string; window_started_at: string; measured_at: string;
  published_events: number; connected_clients: number;
  actor_queue_depth: number; planner_running_jobs: number; planner_pending_jobs: number;
  ui_render: {client_id: string; latency_upper: DurationStats; receive_to_render: DurationStats;
    invalid_samples: number; hidden_samples: number; unreported_events: number;
    latency_exceedances: number}[]; // upper latency >=500ms
  replans: {elapsed: DurationStats; compute: DurationStats; succeeded: number;
    no_feasible_plan: number; stale: number; timeout: number; failed: number;
    deadline_exceedances: number}; // elapsed >5000ms
}
