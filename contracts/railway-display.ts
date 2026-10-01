// Additive read-only railway UI extension. api-v1.ts / State remain unchanged.
export interface RailwayPlanChange {
  operation_id: string;
  train_id: string;
  label: string;
  before_start_sim_s: number | null;
  after_start_sim_s: number | null;
  shift_sim_s: number | null;
  before_track_id: string | null;
  after_track_id: string | null;
  before_resource_ids: string[];
  after_resource_ids: string[];
}
export type RailwayExplanation = {
  available: false;
  reason: string;
  plans: [];
} | {
  available: true;
  run_id: string;
  replan_id: string;
  base_event_seq: number;
  base_plan_id: string | null;
  plans: {
    plan_id: string;
    changes: RailwayPlanChange[];
    departures: {
      train_id: string;
      departure_sim_s: number | null;
      delay_sim_s: number | null;
      basis: 'actual' | 'forecast';
    }[];
    reason: string;
  }[];
};
