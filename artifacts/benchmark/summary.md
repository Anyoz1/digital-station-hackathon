# FCFS vs heuristic — backend v1.0

Same immutable input per pair; seed42/default config; 12 tracks/7 trains/33 operations.

Normal starts sim0. Disruptions use the same FCFS-executed sim480 checkpoint, identical H18 incident batches, 50sim-s launch barrier, window [0,7200]. Nothing was tuned to prefer heuristic.

Offline event-jump physical engine, not recorded live history or realtime SLA. All candidates independently validated. Raw/null values preserved.

| Case | Algorithm | Departed | Mean delay,sim-s | Wagon-hours | Idle ratio | Actual index | J | Offline compute,ms | Feasible |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
|normal|fcfs|7|171.429|25.133|0.000000|95.2|0.246438|31.124|1|
|normal|heuristic|7|171.429|25.067|0.000000|95.2|0.240284|527.490|2|
|train_delay|fcfs|7|128.571|24.467|0.000000|96.4|0.252473|30.300|1|
|train_delay|heuristic|7|154.286|23.000|0.023121|95.5|0.248512|489.000|2|
|track_closure|fcfs|7|171.429|25.133|0.000000|95.2|0.246438|30.483|1|
|track_closure|heuristic|7|171.429|25.133|0.000000|95.2|0.246438|475.623|2|
|resource_loss|fcfs|7|171.429|25.133|0.000000|95.2|0.246438|30.053|1|
|resource_loss|heuristic|7|171.429|25.133|0.000000|95.2|0.246438|478.156|2|
|burst5|fcfs|7|128.571|24.467|0.000000|96.4|0.252473|31.137|1|
|burst5|heuristic|7|154.286|23.000|0.023121|95.5|0.248512|537.036|2|
|burst10|fcfs|7|137.143|23.300|0.000000|96.2|0.266566|33.683|1|
|burst10|heuristic|7|137.143|27.100|0.023121|96.0|0.222005|529.501|2|

Heuristic minimizes J, not actual efficiency index alone. Equal or worse individual factors/index are retained, not hidden. Conflicts here are unresolved at window end; both strategies must be safe, 0 is not a fabricated optimization gain.

Quality/compute_ms use the offline checkpoint above. total_replan_elapsed_ms is populated only by scripts/benchmark_live_timings.py: paired real REST/DB/worker/publication timings at a separate identical paused sim0 checkpoint, recorded in live-timings.json and live_* columns. Before that companion run it is null. No virtual 50sim-s barrier is advertised as wall time.

Source: scripts/benchmark_backend.py; raw.json contains exact inputs, candidate/validator, engine samples and final State. Run: uv run python scripts/benchmark_backend.py.

## Actual paired live replan timings

Separate timing control: paused sim0, common validated FCFS starting plan for both. Not the sim480/530 offline quality trajectory. PostgreSQL/actor/process/validator/commit/SSE publish are real; no virtual barrier in these wall-ms. Production core files unchanged.

| Case | Algorithm | Compute,wall-ms | End-to-end,wall-ms | Valid alternatives |
|---|---|---:|---:|---:|
|normal|fcfs|34.952|316.412|1|
|normal|heuristic|537.492|1036.861|2|
|train_delay|fcfs|36.332|328.514|1|
|train_delay|heuristic|570.713|1087.873|2|
|track_closure|fcfs|38.374|336.827|1|
|track_closure|heuristic|542.395|1047.295|2|
|resource_loss|fcfs|37.374|431.654|1|
|resource_loss|heuristic|538.235|1151.841|2|
|burst5|fcfs|38.463|351.273|1|
|burst5|heuristic|565.060|1080.521|2|
|burst10|fcfs|39.379|504.575|1|
|burst10|heuristic|575.284|1122.008|2|
