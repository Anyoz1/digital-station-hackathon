"""Deterministic physical executor + runtime guards. Not a planner/independent validator."""

from collections import defaultdict, deque
from dataclasses import dataclass

from .calendars import interval_available, next_calendar_times, refresh_calendars
from .contracts import Conflict, PhysicalLocation, State
from .scenario import location, utc_now

SHUNT_BOUNDARIES = (25, 35, 60, 120, 210, 240, 330, 360, 385, 395, 420)
MOVEMENTS = ((0, 25, 0), (35, 60, 1), (120, 210, 2), (240, 330, 3), (360, 385, 4), (395, 420, 5))
PROFILE_KINDS = {
    "freight_transit_v1": {"arrival", "inspection", "departure"},
    "freight_local_v1": {"arrival", "inspection", "shunt_transfer", "cargo", "departure_prep", "departure"},
    "freight_reclassify_v1": {"arrival", "inspection", "shunt_transfer", "departure_prep", "departure"},
    "passenger_transit_v1": {"arrival", "departure"},
    "service_transit_v1": {"arrival", "departure"},
}


class GuardViolation(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise GuardViolation(code)


def at(kind, identifier=None, **kwargs):
    return PhysicalLocation.model_validate(location(kind, identifier, **kwargs))


def check_topology(state: State):
    """Validate IDs and physical graph connectivity, not interval/plan feasibility."""
    layout = state.station.layout
    nodes = {n.id for n in layout.nodes}
    tracks = {t.id for t in state.tracks}
    require(len(nodes) == len(layout.nodes), "DUPLICATE_NODE")
    require({t.track_id for t in layout.tracks} == tracks, "MISSING_TRACK_GEOMETRY")
    graph = defaultdict(set)
    for edge in layout.edges:
        require(edge.from_node_id in nodes and edge.to_node_id in nodes, "UNKNOWN_EDGE_NODE")
        require(edge.track_id is None or edge.track_id in tracks, "UNKNOWN_EDGE_TRACK")
        graph[edge.from_node_id].add(edge.to_node_id)
        if edge.bidirectional:
            graph[edge.to_node_id].add(edge.from_node_id)
    visited, pending = set(), deque(["BW"])
    while pending:
        node = pending.popleft()
        if node not in visited:
            visited.add(node)
            pending.extend(graph[node] - visited)
    require(nodes <= visited and "BE" in visited, "DISCONNECTED_TOPOLOGY")
    for route in layout.routes:
        require(
            route.from_id in tracks | {"BW", "BE"} and route.to_id in tracks | {"BW", "BE"},
            "UNKNOWN_ROUTE_END",
        )
        require(set(route.track_ids) <= tracks and set(route.zone_ids) <= {"W", "E"}, "UNKNOWN_ROUTE_PART")
        start = (
            route.from_id
            if route.from_id in {"BW", "BE"}
            else "H-end"
            if route.from_id == "H"
            else f"{route.from_id}-W"
        )
        finish = (
            route.to_id
            if route.to_id in {"BW", "BE"}
            else "H-end"
            if route.to_id == "H"
            else f"{route.to_id}-W"
        )
        allowed = defaultdict(set)
        for edge in layout.edges:
            if (edge.track_id is None or edge.track_id in route.track_ids) and (
                edge.zone_id is None or edge.zone_id in route.zone_ids
            ):
                allowed[edge.from_node_id].add(edge.to_node_id)
                if edge.bidirectional:
                    allowed[edge.to_node_id].add(edge.from_node_id)
        reached, queue = set(), deque([start])
        while queue:
            node = queue.popleft()
            if node not in reached:
                reached.add(node)
                queue.extend(allowed[node] - reached)
        require(finish in reached, "DISCONNECTED_ROUTE")


@dataclass
class Batch:
    state: State
    transitions: list[dict]


class Simulator:
    def __init__(self, state: State, initial: State):
        check_topology(state)
        self.state = state.model_copy(deep=True)
        self.ledger = {
            g.id: (tuple(g.wagon_ids), g.origin_train_id, g.assigned_train_id, g.length_m)
            for g in initial.wagon_groups
        }
        self.fixed = {t.id: t.body_length_m for t in initial.trains if t.consist_kind == "fixed"}
        self.index()
        self.launch_barrier = False
        self.guard_replanning = False
        self.refresh()

    def index(self):
        self.tracks = {obj.id: obj for obj in self.state.tracks}
        self.trains = {obj.id: obj for obj in self.state.trains}
        self.wagon_groups = {obj.id: obj for obj in self.state.wagon_groups}
        self.resources = {obj.id: obj for obj in self.state.resources}
        self.operations = {obj.id: obj for obj in self.state.operations}
        self.routes = {route.id: route for route in self.state.station.layout.routes}

    def check_inventory(self):
        require(set(self.wagon_groups) == set(self.ledger), "GROUP_LEDGER_CHANGED")
        for gid, group in self.wagon_groups.items():
            require(
                (tuple(group.wagon_ids), group.origin_train_id, group.assigned_train_id, group.length_m)
                == self.ledger[gid],
                "WAGON_LEDGER_CHANGED",
            )
            require(group.wagon_count == len(group.wagon_ids), "WAGON_COUNT_CHANGED")
            if group.current_train_id is not None:
                train = self.trains[group.current_train_id]
                require(
                    gid in train.group_ids and group.location == train.location, "ATTACHED_GROUP_MISMATCH"
                )
        for train in self.trains.values():
            require(
                {g.id for g in self.wagon_groups.values() if g.current_train_id == train.id}
                == set(train.group_ids),
                "MEMBERSHIP_MISMATCH",
            )
            if train.id in self.fixed:
                require(
                    train.body_length_m == self.fixed[train.id] and not train.group_ids,
                    "FIXED_CONSIST_CHANGED",
                )
            else:
                require(
                    train.body_length_m == sum(self.wagon_groups[g].length_m for g in train.group_ids)
                    and train.total_length_m == train.body_length_m + 20,
                    "CURRENT_CONSIST_LENGTH_MISMATCH",
                )

    def locks(self, op):
        tracks = {x for x in (op.source_track_id, op.target_track_id) if x}
        zones = set()
        for rid in op.route_ids:
            route = self.routes[rid]
            tracks.update(route.track_ids)
            zones.update(route.zone_ids)
        return tracks, zones

    def refresh(self):
        """Physical occupancy is derived from positions, not assigned timetable."""
        for train in self.trains.values():
            if train.consist_kind == "wagon_groups":
                train.body_length_m = sum(self.wagon_groups[g].length_m for g in train.group_ids)
                train.total_length_m = train.body_length_m + 20
        for track in self.tracks.values():
            track.occupied_length_m = 0
            track.train_ids, track.group_ids, track.locomotive_ids, track.active_operation_ids = (
                [],
                [],
                [],
                [],
            )
        for zone in self.state.zones:
            zone.active_operation_id = None
        for train in self.trains.values():
            if train.location.kind == "track":
                assert train.location.track_id is not None
                track = self.tracks[train.location.track_id]
                track.train_ids.append(train.id)
                if train.consist_kind == "fixed":
                    track.occupied_length_m += train.body_length_m
        for group in self.wagon_groups.values():
            if group.location.kind == "track":
                assert group.location.track_id is not None
                track = self.tracks[group.location.track_id]
                track.group_ids.append(group.id)
                track.occupied_length_m += group.length_m
        for resource in self.resources.values():
            if (
                resource.location
                and resource.location.kind == "track"
                and resource.kind in {"shunting_locomotive", "train_locomotive", "self_propelled_unit"}
            ):
                assert resource.location.track_id is not None
                track = self.tracks[resource.location.track_id]
                if resource.kind != "self_propelled_unit":
                    track.locomotive_ids.append(resource.id)
                    track.occupied_length_m += 20
        for op in self.operations.values():
            op.can_complete = self.manual_ready(op)
            if op.status == "running":
                track_ids, zone_ids = self.locks(op)
                for tid in track_ids:
                    self.tracks[tid].active_operation_ids.append(op.id)
                for zone in self.state.zones:
                    if zone.id in zone_ids:
                        require(zone.active_operation_id is None, "DOUBLE_ZONE_LOCK")
                        zone.active_operation_id = op.id
        for track in self.tracks.values():
            require(len(track.active_operation_ids) <= 1, "DOUBLE_TRACK_LOCK")
            require(track.occupied_length_m <= track.length_m, "CAPACITY")
        self.check_inventory()

    def guard(self, op):
        train = self.trains[op.train_id]
        require(op.kind in PROFILE_KINDS[train.service_profile_id], "PROFILE_OPERATION_MISMATCH")
        require(
            op.execution_mode == "auto"
            or (op.kind in {"inspection", "cargo", "departure_prep"} and op.assigned_user_id is not None),
            "MANUAL_MOVEMENT_FORBIDDEN",
        )
        require(op.end_sim_s - op.start_sim_s == op.duration_sim_s, "DURATION_MISMATCH")
        require(
            all(self.operations[p].status == "completed" for p in op.predecessor_ids),
            "PREDECESSOR_NOT_COMPLETED",
        )
        track_ids, zone_ids = self.locks(op)
        require(
            interval_available(
                self.state,
                op,
                self.state.sim_time_s,
                self.state.sim_time_s + op.duration_sim_s,
                (track_ids, zone_ids),
            ),
            "CALENDAR_CONFLICT",
        )
        for tid in track_ids:
            track = self.tracks[tid]
            require(track.availability == "open", "TRACK_CLOSED")
            require(not track.active_operation_ids, "TRACK_LOCKED")
        for zone in self.state.zones:
            if zone.id in zone_ids:
                require(zone.availability == "open" and zone.active_operation_id is None, "ZONE_LOCKED")
        require(
            len(op.resource_ids) == len(set(op.resource_ids)) and bool(op.resource_ids), "INVALID_RESOURCES"
        )
        for rid in op.resource_ids:
            resource = self.resources[rid]
            require(
                resource.status == "available" and resource.active_operation_id is None,
                "RESOURCE_UNAVAILABLE",
            )
            require(
                resource.available_after_sim_s is None
                or resource.available_after_sim_s <= self.state.sim_time_s,
                "RESOURCE_NOT_READY",
            )
        kinds = {self.resources[r].kind for r in op.resource_ids}
        if op.kind == "arrival":
            require(
                sum(t.status in {"on_station", "ready_departure"} for t in self.trains.values()) < 6,
                "ACTIVE_TRAIN_LIMIT",
            )
            require(
                self.resources[train.traction_resource_id].location == train.location,
                "TRACTION_NOT_AT_SOURCE",
            )
            require(
                train.location.kind == "boundary" and self.state.sim_time_s >= train.expected_arrival_sim_s,
                "TRAIN_NOT_READY",
            )
            target = self.tracks[op.target_track_id]
            require(train.processing_kind == "transit" or target.id in {"R3", "R4"}, "TRACK_INCOMPATIBLE")
            require(
                train.service_profile_id != "passenger_transit_v1" or target.id in {"R1", "R2"},
                "TRACK_INCOMPATIBLE",
            )
            require(
                target.kind == "receiving_departure"
                and target.occupied_length_m == 0
                and target.assigned_train_id is None,
                "TARGET_OCCUPIED",
            )
            require(
                train.total_length_m + (20 if train.processing_kind != "transit" else 0) <= target.length_m,
                "CAPACITY",
            )
            require(len(op.route_ids) == 1, "ROUTE_MISMATCH")
            route = self.routes[op.route_ids[0]]
            require(
                route.from_id == train.location.boundary_id and route.to_id == target.id, "ROUTE_MISMATCH"
            )
        elif op.kind == "shunt_transfer":
            require(op.duration_sim_s == 420, "SHUNT_DURATION_MISMATCH")
            require(len(op.group_ids) == 1 and len(op.route_ids) == 6, "SHUNT_TEMPLATE_MISMATCH")
            group = self.wagon_groups[op.group_ids[0]]
            require(group.location == at("track", op.source_track_id), "GROUP_NOT_AT_SOURCE")
            require(
                group.assigned_train_id == train.id and self.resources["L1"].location == at("track", "D1"),
                "TRACTION_NOT_AT_SOURCE",
            )
            require(
                kinds == {"shunting_locomotive", "shunting_crew"} and set(op.resource_ids) == {"L1", "SH1"},
                "RESOURCE_KIND_MISMATCH",
            )
            require(group.length_m + 20 <= self.tracks["H"].length_m, "CAPACITY")
            source, target = self.tracks[op.source_track_id], self.tracks[op.target_track_id]
            require(not set(source.group_ids) - set(train.group_ids) - {group.id}, "FOREIGN_GROUP_AT_SOURCE")
            if group.current_train_id:
                require(
                    train.group_ids[0] == group.id and group.current_train_id == train.id,
                    "MIDDLE_GROUP_EXTRACTION",
                )
            else:
                require(op.target_track_id == train.current_track_id, "WRONG_HOME_TRACK")
            require(
                not set(target.train_ids) - {train.id} and not set(target.group_ids) - set(train.group_ids),
                "TARGET_OCCUPIED",
            )
            require(target.occupied_length_m + group.length_m + 20 <= target.length_m, "CAPACITY")
            expected = [
                ("D1", "H"),
                ("H", source.id),
                (source.id, "H"),
                ("H", target.id),
                (target.id, "H"),
                ("H", "D1"),
            ]
            require(
                [(self.routes[r].from_id, self.routes[r].to_id) for r in op.route_ids] == expected,
                "DISCONNECTED_ROUTE",
            )
        else:
            require(op.source_track_id is not None, "NO_SOURCE_TRACK")
            if op.kind == "cargo":
                require(
                    self.tracks[op.source_track_id].kind == "cargo" and kinds == {"cargo_crew"},
                    "RESOURCE_KIND_MISMATCH",
                )
                require(
                    all(
                        self.wagon_groups[g].location == at("track", op.source_track_id)
                        and self.wagon_groups[g].current_train_id is None
                        for g in op.group_ids
                    ),
                    "GROUP_NOT_AT_SOURCE",
                )
            else:
                require(train.location == at("track", op.source_track_id), "TRAIN_NOT_AT_SOURCE")
                require(
                    self.resources[train.traction_resource_id].location == train.location,
                    "TRACTION_NOT_AT_SOURCE",
                )
                if op.kind in {"inspection", "departure_prep"}:
                    require("inspection_crew" in kinds, "RESOURCE_KIND_MISMATCH")
                if op.kind in {"departure_prep", "departure"}:
                    require(train.group_ids == train.target_group_ids, "FORMATION_MISMATCH")
        if op.kind in {"arrival", "departure", "departure_prep"}:
            own_crew = "TCP1" if train.id == "P1" else f"TC{train.id[1:]}"
            require(
                train.traction_resource_id in op.resource_ids and own_crew in op.resource_ids,
                "TRACTION_ASSIGNMENT_MISMATCH",
            )
        if op.kind == "departure":
            require(len(op.route_ids) == 1, "ROUTE_MISMATCH")
            route = self.routes[op.route_ids[0]]
            require(
                route.from_id == op.source_track_id
                and route.to_id == ("BE" if train.direction == "W_E" else "BW"),
                "ROUTE_MISMATCH",
            )

    def train_position(self, train, loc):
        train.location = loc.model_copy(deep=True)
        train.current_track_id = loc.track_id if loc.kind == "track" else None
        for gid in train.group_ids:
            self.wagon_groups[gid].location = loc.model_copy(deep=True)
        self.resources[train.traction_resource_id].location = loc.model_copy(deep=True)
        for op in self.operations.values():
            if op.train_id == train.id and op.kind == "arrival":
                for rid in op.resource_ids:
                    if self.resources[rid].kind == "train_crew":
                        self.resources[rid].location = loc.model_copy(deep=True)

    def positions(self, op, elapsed):
        if op.kind in {"arrival", "departure"}:
            loc = at(
                "route",
                route_id=op.route_ids[0],
                operation_id=op.id,
                route_progress=elapsed / op.duration_sim_s,
            )
            self.train_position(self.trains[op.train_id], loc)
        elif op.kind == "shunt_transfer":
            moving_group = 120 <= elapsed < 360
            phase = (
                "empty_to_source"
                if elapsed < 60
                else "couple"
                if elapsed < 120
                else "pull_to_lead"
                if elapsed < 210
                else "reverse"
                if elapsed < 240
                else "push_to_target"
                if elapsed < 330
                else "uncouple"
                if elapsed < 360
                else "return_to_depot"
            )
            op.phase = phase
            loc = None
            for start, end, leg in MOVEMENTS:
                if start <= elapsed < end:
                    loc = at(
                        "route",
                        route_id=op.route_ids[leg],
                        operation_id=op.id,
                        route_progress=(elapsed - start) / (end - start),
                    )
                    break
            if loc is None:
                tid = (
                    "H"
                    if elapsed < 60 or 210 <= elapsed < 240 or elapsed >= 360
                    else op.source_track_id
                    if elapsed < 120
                    else op.target_track_id
                )
                loc = at("track", tid)
            self.resources["L1"].location = loc.model_copy(deep=True)
            if moving_group:
                self.wagon_groups[op.group_ids[0]].location = loc.model_copy(deep=True)

    def start(self, op):
        self.guard(op)
        op.status, op.actual_start_sim_s, op.progress = "running", self.state.sim_time_s, 0
        for rid in op.resource_ids:
            resource = self.resources[rid]
            resource.status, resource.active_operation_id = "busy", op.id
        train = self.trains[op.train_id]
        if op.kind == "arrival":
            train.status = "on_station"
            self.tracks[op.target_track_id].assigned_train_id = train.id
        self.positions(op, 0)

    def complete(self, op):
        train = self.trains[op.train_id]
        if op.kind == "arrival":
            self.train_position(train, at("track", op.target_track_id))
            train.actual_arrival_sim_s = self.state.sim_time_s
        elif op.kind == "departure":
            self.train_position(train, at("departed"))
            train.status, train.actual_departure_sim_s = "departed", self.state.sim_time_s
            self.tracks[op.source_track_id].assigned_train_id = None
        elif op.kind == "shunt_transfer":
            self.resources["L1"].location = at("track", "D1")
        elif op.kind == "cargo":
            for gid in op.group_ids:
                self.wagon_groups[gid].cargo_state = "loaded"
        op.status, op.progress, op.actual_end_sim_s = "completed", 1, self.state.sim_time_s
        op.phase = None
        op.can_complete = False
        for rid in op.resource_ids:
            resource = self.resources[rid]
            resource.status, resource.active_operation_id = "available", None

    def manual_ready(self, op):
        return bool(
            op.execution_mode == "manual"
            and op.kind in {"inspection", "cargo", "departure_prep"}
            and op.status == "running"
            and op.actual_start_sim_s is not None
            and self.state.sim_time_s >= op.actual_start_sim_s + op.duration_sim_s
            and all(self.operations[p].status == "completed" for p in op.predecessor_ids)
            and all(self.resources[r].active_operation_id == op.id for r in op.resource_ids)
            and bool(op.resource_ids)
        )

    def next_due(self, allow_starts=True):
        now = self.state.sim_time_s
        times = []
        for op in self.operations.values():
            if op.status == "planned" and allow_starts and not self.launch_barrier:
                assert op.start_sim_s is not None
                times.append(max(now, op.start_sim_s))
            if op.status == "running":
                assert op.actual_start_sim_s is not None
                if op.execution_mode == "auto" or op.actual_start_sim_s + op.duration_sim_s > now:
                    times.append(op.actual_start_sim_s + op.duration_sim_s)
                if op.kind == "shunt_transfer":
                    times += [
                        op.actual_start_sim_s + t for t in SHUNT_BOUNDARIES if op.actual_start_sim_s + t > now
                    ]
        times += [
            t.expected_arrival_sim_s
            for t in self.trains.values()
            if t.status == "expected" and t.expected_arrival_sim_s >= now
        ]
        times += next_calendar_times(self.state)
        return min(times) if times else None

    def process(self, time, allow_starts=True):
        self.state.sim_time_s = time
        events = []
        # Endings precede phases/new starts at the same time: half-open reservations.
        for op in self.operations.values():
            if op.status != "running":
                continue
            assert op.actual_start_sim_s is not None
            if op.execution_mode == "auto" and op.actual_start_sim_s + op.duration_sim_s == time:
                self.complete(op)
                events.append(dict(kind="operation_completed", entity_ids=[op.id, op.train_id]))
        events.extend(refresh_calendars(self.state))
        for op in self.operations.values():
            if op.status == "running":
                elapsed = time - op.actual_start_sim_s
                if op.kind == "shunt_transfer" and elapsed in SHUNT_BOUNDARIES:
                    group = self.wagon_groups[op.group_ids[0]]
                    train = self.trains[op.train_id]
                    if elapsed == 120:
                        require(
                            train.group_ids and train.group_ids[0] == group.id
                            if group.current_train_id
                            else True,
                            "MIDDLE_GROUP_EXTRACTION",
                        )
                        if group.current_train_id:
                            train.group_ids.remove(group.id)
                            group.current_train_id = None
                    if elapsed == 360:
                        group.location = at("track", op.target_track_id)
                        if op.target_track_id == train.current_track_id:
                            train.group_ids.insert(0, group.id)
                            group.current_train_id = train.id
                    events.append(dict(kind="operation_phase_changed", entity_ids=[op.id, "L1", group.id]))
                op.progress = min(1, elapsed / op.duration_sim_s)
                self.positions(op, elapsed)
        self.refresh()
        for train in self.trains.values():
            if train.status == "expected" and time >= train.expected_arrival_sim_s:
                train.status = "waiting_entry"
                events.append(dict(kind="train_waiting_entry", entity_ids=[train.id]))
        for op in self.operations.values():
            if (
                op.kind == "departure"
                and op.status == "planned"
                and all(self.operations[p].status == "completed" for p in op.predecessor_ids)
            ):
                train = self.trains[op.train_id]
                if train.status == "on_station":
                    train.status = "ready_departure"
                    events.append(dict(kind="train_ready_departure", entity_ids=[train.id]))
        for op in sorted(self.operations.values(), key=lambda op: op.id):
            if allow_starts and not self.launch_barrier and op.status == "planned" and op.start_sim_s <= time:
                try:
                    require(op.start_sim_s == time, "START_TIME_MISSED")
                    self.start(op)
                    events.append(dict(kind="operation_started", entity_ids=[op.id, op.train_id]))
                except GuardViolation as exc:
                    if self.guard_replanning:
                        self.launch_barrier = True
                    op.status = "blocked"
                    op.blocked_reason_codes = [exc.code]
                    conflict_id = f"guard-{op.id}"
                    self.state.conflicts = [c for c in self.state.conflicts if c.id != conflict_id]
                    self.state.conflicts.append(
                        Conflict(
                            id=conflict_id,
                            kind="capacity"
                            if exc.code == "CAPACITY"
                            else "resource_unavailable"
                            if "RESOURCE" in exc.code or "TRACTION" in exc.code
                            else "precedence"
                            if "PREDECESSOR" in exc.code
                            else "track_occupied",
                            severity="critical",
                            reason_code=exc.code,
                            entity_ids=[op.train_id, *op.resource_ids],
                            operation_ids=[op.id],
                            message=f"Запуск {op.id} запрещён: {exc.code}",
                            recommendation="Нужен новый проверенный план; фактическое состояние сохранено.",
                            detected_at=utc_now(),
                        )
                    )
                    events.append(
                        dict(kind="operation_blocked", entity_ids=[op.id, op.train_id], reason_code=exc.code)
                    )
                self.refresh()
        self.refresh()
        return events

    def advance(self, target, allow_starts=True, emit_copies=True):
        """Process every intermediate due event, even after a long wall delay / at10×."""
        require(target >= self.state.sim_time_s, "CLOCK_REWIND")
        while True:
            due = self.next_due(allow_starts)
            if due is None or due > target:
                break
            events = self.process(due, allow_starts)
            yield Batch(self.state.model_copy(deep=True) if emit_copies else self.state, events)
        if target > self.state.sim_time_s:
            events = self.process(target, allow_starts)
            yield Batch(self.state.model_copy(deep=True) if emit_copies else self.state, events)
