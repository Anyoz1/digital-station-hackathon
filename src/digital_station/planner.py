"""Bounded deterministic multi-strategy scheduling. No database/live-state access."""

import hashlib
import json
import time
from itertools import product

from .calendars import next_calendar_times
from .contracts import State
from .forecast import forecast, objective
from .simulator import GuardViolation, Simulator
from .validator import own_crew, validate_plan

RULES = ("fcfs", "earliest_due", "urgency", "release_R")
TIES = ("ascending", "descending", "least_scarce")


def ranked(values, tie, scarcity=None):
    return (
        sorted(values, reverse=tie == "descending")
        if tie != "least_scarce"
        else sorted(values, key=lambda v: ((scarcity or {}).get(v, 0), v))
    )


def compatible_homes(train):
    # Infrastructure specialization belongs to the service profile, not Train.type.
    if train.service_profile_id == "passenger_transit_v1":
        return ["R1", "R2"]
    return ["R1", "R2", "R3", "R4"] if train.processing_kind == "transit" else ["R3", "R4"]


def compatible_identifiers(engine, op):
    """Domains for scarcity counting, not a substitute for interval/start guards."""
    if op.status == "running":
        tracks, _ = engine.locks(op)
        return tracks | set(op.resource_ids)
    train = engine.trains[op.train_id]
    retained_home = next((t.id for t in engine.tracks.values() if t.assigned_train_id == train.id), None)
    homes = {retained_home} if retained_home else set(compatible_homes(train))
    if op.kind in {"arrival", "departure", "departure_prep"}:
        return (
            homes
            | {train.traction_resource_id, own_crew(train.id)}
            | ({"I1", "I2"} if op.kind == "departure_prep" else set())
        )
    if op.kind == "inspection":
        return homes | {"I1", "I2"}
    group = engine.wagon_groups[op.group_ids[0]]
    known_front = group.location.track_id
    if known_front is None:
        movement = next(
            (
                o
                for o in engine.operations.values()
                if o.status == "running" and group.id in o.group_ids and o.kind == "shunt_transfer"
            ),
            None,
        )
        if movement and movement.target_track_id.startswith(("C", "S")):
            known_front = movement.target_track_id
    fronts = (
        {known_front}
        if known_front and known_front.startswith(("C", "S"))
        else ({"C1", "C2"} if train.processing_kind == "local" else {"S1", "S2", "S3", "S4"})
    )
    return fronts | {"CG1"} if op.kind == "cargo" else homes | fronts | {"H", "D1", "L1", "SH1"}


def scarcity_counts(engine, op, identifiers):
    domains = [
        compatible_identifiers(engine, other)
        for other in engine.operations.values()
        if other.id != op.id and other.status != "completed"
    ]
    return {identifier: sum(identifier in domain for domain in domains) for identifier in identifiers}


def options(engine, op, tie):
    train = engine.trains[op.train_id]
    homes = compatible_homes(train) if op.kind == "arrival" else [train.current_track_id]
    scarcity = scarcity_counts(engine, op, homes) if tie == "least_scarce" else None
    for home in ranked(homes, tie, scarcity):
        inspectors = ["I1", "I2"]
        resource_options = ranked(
            inspectors, tie, scarcity_counts(engine, op, inspectors) if tie == "least_scarce" else None
        )
        own = [train.traction_resource_id, own_crew(train.id)]
        if op.kind == "arrival":
            yield None, home, [f"arrival-{'W' if train.direction == 'W_E' else 'E'}-{home}"], own
        elif op.kind == "departure":
            yield home, None, [f"departure-{'E' if train.direction == 'W_E' else 'W'}-{home}"], own
        elif op.kind in {"inspection", "departure_prep"}:
            for inspector in resource_options:
                yield home, home, [], [inspector] + (own if op.kind == "departure_prep" else [])
        elif op.kind == "cargo":
            front = engine.wagon_groups[op.group_ids[0]].location.track_id
            yield front, front, [], ["CG1"]
        else:
            group = engine.wagon_groups[op.group_ids[0]]
            source = group.location.track_id
            fronts = ["C1", "C2"] if train.processing_kind == "local" else ["S1", "S2", "S3", "S4"]
            targets = (
                ranked(fronts, tie, scarcity_counts(engine, op, fronts) if tie == "least_scarce" else None)
                if group.current_train_id
                else [home]
            )
            for target in targets:
                yield (
                    source,
                    target,
                    [
                        "move-D1-H",
                        f"move-H-{source}",
                        f"move-{source}-H",
                        f"move-H-{target}",
                        f"move-{target}-H",
                        "move-H-D1",
                    ],
                    ["L1", "SH1"],
                )


def remaining(engine, train_id):
    now = engine.state.sim_time_s
    return sum(
        max(0, o.actual_start_sim_s + o.duration_sim_s - now) if o.status == "running" else o.duration_sim_s
        for o in engine.operations.values()
        if o.train_id == train_id and o.status != "completed"
    )


def key(engine, op, rule):
    t = engine.trains[op.train_id]
    if rule == "earliest_due":
        return t.due_departure_sim_s, t.expected_arrival_sim_s, op.id
    if rule == "urgency":
        return (
            -t.priority,
            t.due_departure_sim_s - engine.state.sim_time_s - remaining(engine, t.id),
            t.expected_arrival_sim_s,
            op.id,
        )
    if rule == "release_R":
        return (
            0 if op.kind == "departure" else 2 if op.kind == "arrival" else 1,
            remaining(engine, t.id),
            op.id,
        )
    return t.expected_arrival_sim_s, op.id


def wait_for_priority(engine, op):
    now = engine.state.sim_time_s
    _, zones = engine.locks(op)
    train = engine.trains[op.train_id]
    for t in engine.trains.values():
        arrival_zone = "W" if t.direction == "W_E" else "E"
        if (
            t.status in {"expected", "waiting_entry"}
            and t.priority > train.priority
            and arrival_zone in zones
            and now < t.expected_arrival_sim_s <= now + 420
            and t.expected_arrival_sim_s < now + op.duration_sim_s
        ):
            return t.expected_arrival_sim_s
    return None


def rollout(base, initial, cutover, rule, tie, deadline):
    state = base.model_copy(deep=True)
    frozen = {o.id for o in state.operations if o.status in {"running", "completed"}}
    for op in state.operations:
        if op.id not in frozen:
            op.status, op.start_sim_s, op.end_sim_s = "pending", None, None
            op.blocked_reason_codes = []
    engine = Simulator(state, initial)
    for _ in engine.advance(cutover, allow_starts=False, emit_copies=False):
        pass
    horizon = cutover + 7200
    steps = 0
    while not all(o.status == "completed" for o in engine.operations.values()):
        if time.monotonic() >= deadline:
            raise TimeoutError("Search deadline")
        steps += 1
        if steps > 1000:
            return None, "DEADLOCK"
        now = engine.state.sim_time_s
        waits = []
        ready = [
            o
            for o in engine.operations.values()
            if o.status == "pending"
            and all(engine.operations[p].status == "completed" for p in o.predecessor_ids)
        ]
        for op in sorted(ready, key=lambda o: key(engine, o, rule)):
            if time.monotonic() >= deadline:
                raise TimeoutError("Search deadline")
            for source, target, routes, resources in options(engine, op, tie):
                op.source_track_id, op.target_track_id, op.route_ids, op.resource_ids = (
                    source,
                    target,
                    routes,
                    resources,
                )
                op.start_sim_s, op.end_sim_s = now, now + op.duration_sim_s
                try:
                    engine.guard(op)
                except GuardViolation:
                    continue
                priority_wait = wait_for_priority(engine, op) if rule == "urgency" else None
                if priority_wait is not None:
                    waits.append(priority_wait)
                    break
                engine.start(op)
                engine.refresh()
                break
        future = [t for t in next_calendar_times(engine.state) if t > now] + waits
        due = engine.next_due(allow_starts=False)
        if due is not None and due > now:
            future.append(due)
        future += [
            t.expected_arrival_sim_s
            for t in engine.trains.values()
            if t.expected_arrival_sim_s > now and t.status in {"expected", "waiting_entry"}
        ]
        if not future:
            return None, "DEADLOCK"
        target = min(future)
        if target > horizon:
            return None, "HORIZON_EXHAUSTED"
        for _ in engine.advance(target, allow_starts=False, emit_copies=False):
            pass
    previous = {o.id: o for o in base.operations}
    operations = []
    for op in engine.state.operations:
        if op.id in frozen:
            operations.append(previous[op.id].model_copy(deep=True))
        else:
            candidate = op.model_copy(deep=True)
            candidate.status, candidate.actual_start_sim_s, candidate.actual_end_sim_s = "planned", None, None
            candidate.progress, candidate.phase, candidate.can_complete = 0, None, False
            candidate.blocked_reason_codes = []
            operations.append(candidate)
    return operations, None


def search(data):
    started = time.monotonic()
    base = State.model_validate(data["state"])
    initial = State.model_validate(data["initial"])
    cutover = data["cutover_sim_s"]
    config = data["config"]
    deadline = min(data["deadline"], started + config["planner"]["time_limit_ms"] / 1000)
    digest = hashlib.sha256(
        json.dumps({k: v for k, v in data.items() if k != "deadline"}, sort_keys=True).encode()
    ).hexdigest()
    candidates = []
    fingerprints = set()
    attempts = []
    validation_ms = 0.0
    timed_out = False
    strategies = list(product(RULES, TIES))[: config["planner"]["max_rollouts"]]
    for rule, tie in strategies:
        try:
            operations, reason = rollout(base, initial, cutover, rule, tie, deadline)
            if operations is None:
                attempts.append(dict(strategy=f"{rule}/{tie}", input_digest=digest, outcome=reason))
                continue
            report = validate_plan(base, operations, initial, cutover, deadline=deadline)
            validation_ms += report.elapsed_ms
            attempts.append(
                dict(
                    strategy=f"{rule}/{tie}",
                    input_digest=digest,
                    outcome="feasible" if report.passed else "invalid",
                    errors=report.errors,
                )
            )
            if not report.passed:
                continue
            assert report.end_state is not None
            signature = json.dumps(
                [
                    (
                        o.id,
                        o.start_sim_s,
                        o.source_track_id,
                        o.target_track_id,
                        o.route_ids,
                        sorted(o.resource_ids),
                    )
                    for o in operations
                    if o.status not in {"running", "completed"}
                ],
                sort_keys=True,
            )
            if signature in fingerprints:
                continue
            fingerprints.add(signature)
            J, components, changed, explanations = objective(base, operations, report)
            candidates.append(
                dict(
                    strategy=f"{rule}/{tie}",
                    operations=[o.model_dump(mode="json") for o in operations],
                    operations_sha256=hashlib.sha256(
                        json.dumps([o.model_dump(mode="json") for o in operations], sort_keys=True).encode()
                    ).hexdigest(),
                    validator=report.public(),
                    objective_value=J,
                    objective_components=components,
                    forecast=forecast(base, report, cutover, config).model_dump(mode="json"),
                    changed_operation_ids=changed,
                    explanation=explanations,
                    departures={t["id"]: t["actual_departure_sim_s"] for t in report.end_state["trains"]},
                )
            )
        except TimeoutError:
            timed_out = True
            break
    candidates.sort(key=lambda c: (c["objective_value"], c["objective_components"]["L"], c["strategy"]))
    baseline = next((c for c in candidates if c["strategy"] == "fcfs/ascending"), None)
    elapsed = (time.monotonic() - started) * 1000
    return dict(
        status="succeeded" if candidates else "timeout" if timed_out else "no_feasible_plan",
        candidates=candidates[:2],
        baseline=baseline,
        attempts=attempts,
        input_digest=digest,
        compute_ms=max(0, elapsed - validation_ms),
        validation_ms=validation_ms,
        outcome_reason_codes=[]
        if candidates
        else ["SEARCH_DEADLINE" if timed_out else "NO_COMPLETE_FEASIBLE_PLAN_FOUND"],
    )
