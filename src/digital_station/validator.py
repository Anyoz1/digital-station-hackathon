"""Independent temporal/physical plan verifier. Never imports simulator or scheduler.

Only public schemas and scenario data are shared. The verifier maintains its own
ledger, reservations and phase timeline, including the frozen execution prefix.
"""

import copy
import time
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby

from .contracts import Operation, State

PHASE_POINTS = (25, 35, 60, 120, 210, 240, 330, 360, 385, 395)
TRAVEL_LEGS = ((0, 25, 0), (35, 60, 1), (120, 210, 2), (240, 330, 3), (360, 385, 4), (395, 420, 5))
IMMUTABLE_OPERATION = (
    "id",
    "train_id",
    "group_ids",
    "kind",
    "predecessor_ids",
    "duration_sim_s",
    "execution_mode",
    "assigned_user_id",
)


@dataclass
class ValidationResult:
    passed: bool
    errors: list[dict]
    samples: list[dict] = field(default_factory=list)
    end_state: dict | None = None
    elapsed_ms: float = 0

    def public(self):
        return {"passed": self.passed, "errors": self.errors}


class InvalidPlan(Exception):
    def __init__(self, code, entities, message):
        self.error = dict(code=code, entity_ids=list(entities), message=message)
        super().__init__(code)


def verify(condition, code, entities, message):
    if not condition:
        raise InvalidPlan(code, entities, message)


def position(kind, identifier=None, operation=None, progress=None):
    value = dict(
        kind=kind, boundary_id=None, track_id=None, route_id=None, operation_id=None, route_progress=None
    )
    if kind in {"track", "boundary"}:
        value[f"{kind}_id"] = identifier
    if kind == "route":
        value.update(route_id=identifier, operation_id=operation, route_progress=progress)
    return value


def own_crew(train_id):
    # Explicit crew roster of demo_main_v1, NOT a rule derived from Train.type.
    return "TCP1" if train_id == "P1" else f"TC{train_id[1:]}"


def check_graph(state):
    layout = state["station"]["layout"]
    nodes = {node["id"] for node in layout["nodes"]}
    tracks = {track["id"] for track in state["tracks"]}
    verify(len(nodes) == len(layout["nodes"]), "TOPOLOGY_IDS", [], "Duplicate topology nodes")
    verify(
        {e["track_id"] for e in layout["edges"] if e["track_id"]} == tracks,
        "TOPOLOGY_TRACK_EDGE",
        list(tracks),
        "Every track needs a physical edge",
    )
    graph = defaultdict(set)
    for edge in layout["edges"]:
        a, b = edge["from_node_id"], edge["to_node_id"]
        verify(a in nodes and b in nodes, "TOPOLOGY_EDGE", [edge["id"]], "Unknown graph endpoint")
        graph[a].add(b)
        if edge["bidirectional"]:
            graph[b].add(a)
    seen, pending = set(), ["BW"]
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(graph[node] - seen)
    verify(nodes <= seen and "BE" in seen, "TOPOLOGY_DISCONNECTED", [], "Disconnected station")
    for route in layout["routes"]:
        verify(
            set(route["track_ids"]) <= tracks and set(route["zone_ids"]) <= {"W", "E"},
            "ROUTE_REFERENCE",
            [route["id"]],
            "Unknown route track/zone",
        )
        side = "E" if route["zone_ids"] == ["E"] else "W"

        def endpoint(identifier):
            return (
                identifier
                if identifier in {"BW", "BE"}
                else "H-end"
                if identifier == "H"
                else f"{identifier}-{side}"
            )

        graph = defaultdict(set)
        for edge in layout["edges"]:
            if (not edge["track_id"] or edge["track_id"] in route["track_ids"]) and (
                not edge["zone_id"] or edge["zone_id"] in route["zone_ids"]
            ):
                graph[edge["from_node_id"]].add(edge["to_node_id"])
                if edge["bidirectional"]:
                    graph[edge["to_node_id"]].add(edge["from_node_id"])
        seen, pending = set(), [endpoint(route["from_id"])]
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(graph[node] - seen)
        verify(
            endpoint(route["to_id"]) in seen,
            "ROUTE_DISCONNECTED",
            [route["id"]],
            "Route has no physical path",
        )


class IndependentReplay:
    def __init__(self, base, operations, initial, deadline=None):
        self.state = copy.deepcopy(base)
        self.initial, self.deadline = initial, deadline
        self.state["operations"] = copy.deepcopy(operations)
        self.trains = {obj["id"]: obj for obj in self.state["trains"]}
        self.tracks = {obj["id"]: obj for obj in self.state["tracks"]}
        self.wagon_groups = {obj["id"]: obj for obj in self.state["wagon_groups"]}
        self.resources = {obj["id"]: obj for obj in self.state["resources"]}
        self.operations = {obj["id"]: obj for obj in self.state["operations"]}
        self.routes = {r["id"]: r for r in base["station"]["layout"]["routes"]}
        self.base = base
        self.samples: list[dict] = []
        occupied = self.occupancy()
        for track in self.tracks.values():
            verify(
                track["occupied_length_m"] == occupied.get(track["id"], 0),
                "FACTUAL_OCCUPANCY",
                [track["id"]],
                "Snapshot occupancy differs from physical positions",
            )

    def budget(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("Independent validation deadline")

    def locks(self, op):
        tracks = {t for t in (op["source_track_id"], op["target_track_id"]) if t}
        zones = set()
        for rid in op["route_ids"]:
            verify(rid in self.routes, "ROUTE_REFERENCE", [op["id"], rid], "Unknown route")
            tracks.update(self.routes[rid]["track_ids"])
            zones.update(self.routes[rid]["zone_ids"])
        return tracks, zones

    def calendar_ok(self, op, start, end):
        tracks, _ = self.locks(op)
        for incident in self.base["incidents"]:
            if incident["status"] == "resolved":
                continue
            begin = self.base["sim_time_s"] if incident["status"] == "pending" else incident["starts_sim_s"]
            blocked = start < incident["ends_sim_s"] and end > begin
            target = incident["target_id"]
            if incident["kind"] == "track_closure" and target in tracks:
                verify(not blocked, "TRACK_CALENDAR", [op["id"], target], "Track outage overlaps operation")
            if incident["kind"] == "resource_loss" and target in op["resource_ids"]:
                verify(
                    not blocked, "RESOURCE_CALENDAR", [op["id"], target], "Resource outage overlaps operation"
                )
            if incident["kind"] == "destination_block" and op["kind"] == "departure":
                arrival = end + 600
                if target == self.trains[op["train_id"]]["destination_id"]:
                    verify(
                        not incident["starts_sim_s"] <= arrival < incident["ends_sim_s"],
                        "DESTINATION_CALENDAR",
                        [op["id"], target],
                        "Destination closed at projected arrival",
                    )

    def inventory(self):
        reference = {g["id"]: g for g in self.initial["wagon_groups"]}
        verify(set(reference) == set(self.wagon_groups), "GROUP_IDENTITY", [], "Group roster changed")
        for gid, group in self.wagon_groups.items():
            verify(
                all(
                    group[k] == reference[gid][k]
                    for k in ("wagon_ids", "wagon_count", "length_m", "origin_train_id", "assigned_train_id")
                ),
                "WAGON_IDENTITY",
                [gid],
                "Wagon identity/ownership/length changed",
            )
        initial_trains = {t["id"]: t for t in self.initial["trains"]}
        verify(set(initial_trains) == set(self.trains), "TRAIN_IDENTITY", [], "Train roster changed")
        for tid, train in self.trains.items():
            verify(
                train["traction_resource_id"] == initial_trains[tid]["traction_resource_id"]
                and train["target_group_ids"] == initial_trains[tid]["target_group_ids"],
                "TRAIN_IDENTITY",
                [tid],
                "Traction/target formation changed",
            )
            attached = [g["id"] for g in self.wagon_groups.values() if g["current_train_id"] == tid]
            verify(
                set(attached) == set(train["group_ids"]), "GROUP_MEMBERSHIP", [tid], "Attached group mismatch"
            )
            for gid in attached:
                verify(
                    self.wagon_groups[gid]["location"] == train["location"],
                    "GROUP_LOCATION",
                    [tid, gid],
                    "Attached group does not follow train",
                )
            if train["consist_kind"] == "fixed":
                verify(
                    not attached
                    and train["body_length_m"] == initial_trains[tid]["body_length_m"]
                    and train["total_length_m"] == train["body_length_m"],
                    "FIXED_IDENTITY",
                    [tid],
                    "Fixed consist identity/length changed",
                )
            else:
                body = sum(self.wagon_groups[g]["length_m"] for g in attached)
                verify(
                    train["body_length_m"] == body and train["total_length_m"] == body + 20,
                    "CONSIST_LENGTH",
                    [tid],
                    "Incorrect current consist length",
                )
            verify(
                self.resources[train["traction_resource_id"]]["location"] == train["location"],
                "TRACTION_LOCATION",
                [tid, train["traction_resource_id"]],
                "Own traction not with train",
            )
        for op in self.operations.values():
            if op["status"] == "running" and op["kind"] == "shunt_transfer":
                elapsed = self.state["sim_time_s"] - op["actual_start_sim_s"]
                expected, _ = self.shunt_position(op, elapsed)
                verify(
                    self.resources["L1"]["location"] == expected,
                    "SHUNTER_LOCATION",
                    [op["id"], "L1"],
                    "Shunter location inconsistent with running phase",
                )
                if 120 <= elapsed < 360:
                    verify(
                        self.wagon_groups[op["group_ids"][0]]["location"] == expected,
                        "GROUP_LOCATION",
                        op["group_ids"],
                        "Moving group not with shunter",
                    )
        for rid, resource in self.resources.items():
            users = [
                o["id"]
                for o in self.operations.values()
                if o["status"] == "running" and rid in o["resource_ids"]
            ]
            verify(
                len(users) <= 1 and resource["active_operation_id"] == (users[0] if users else None),
                "RESOURCE_FACT",
                [rid],
                "Resource execution ownership does not match running prefix",
            )

    def shunt_position(self, op, elapsed):
        for start, end, index in TRAVEL_LEGS:
            if start <= elapsed < end:
                return position(
                    "route", op["route_ids"][index], op["id"], (elapsed - start) / (end - start)
                ), (
                    "empty_to_source"
                    if elapsed < 60
                    else "pull_to_lead"
                    if elapsed < 210
                    else "push_to_target"
                    if elapsed < 330
                    else "return_to_depot"
                )
        track = (
            "H"
            if elapsed < 60 or 210 <= elapsed < 240 or elapsed >= 360
            else (op["source_track_id"] if elapsed < 120 else op["target_track_id"])
        )
        phase = (
            "empty_to_source"
            if elapsed < 60
            else "couple"
            if elapsed < 120
            else ("reverse" if elapsed < 240 else "uncouple" if elapsed < 360 else "return_to_depot")
        )
        return position("track", track), phase

    def train_position(self, train, loc):
        train["location"] = copy.deepcopy(loc)
        train["current_track_id"] = loc["track_id"] if loc["kind"] == "track" else None
        for gid in train["group_ids"]:
            self.wagon_groups[gid]["location"] = copy.deepcopy(loc)
        for rid in (train["traction_resource_id"], own_crew(train["id"])):
            self.resources[rid]["location"] = copy.deepcopy(loc)

    def update_positions(self, now):
        for op in self.operations.values():
            if op["status"] != "running":
                continue
            elapsed = now - op["actual_start_sim_s"]
            op["progress"] = elapsed / op["duration_sim_s"]
            if op["kind"] in {"arrival", "departure"}:
                self.train_position(
                    self.trains[op["train_id"]],
                    position("route", op["route_ids"][0], op["id"], op["progress"]),
                )
            elif op["kind"] == "shunt_transfer":
                loc, phase = self.shunt_position(op, elapsed)
                op["phase"] = phase
                self.resources["L1"]["location"] = copy.deepcopy(loc)
                if 120 <= elapsed < 360:
                    self.wagon_groups[op["group_ids"][0]]["location"] = copy.deepcopy(loc)

    def occupancy(self):
        occupied: dict[str, float] = defaultdict(float)
        for train in self.trains.values():
            if train["consist_kind"] == "fixed" and train["location"]["kind"] == "track":
                occupied[train["location"]["track_id"]] += train["total_length_m"]
        for group in self.wagon_groups.values():
            if group["location"]["kind"] == "track":
                occupied[group["location"]["track_id"]] += group["length_m"]
        for resource in self.resources.values():
            loc = resource["location"]
            if resource["kind"] in {"shunting_locomotive", "train_locomotive"} and loc["kind"] == "track":
                occupied[loc["track_id"]] += 20
        for tid, length in occupied.items():
            verify(
                length <= self.tracks[tid]["length_m"],
                "CAPACITY",
                [tid],
                "Physical length exceeds track capacity",
            )
        return occupied

    def resources_at(self, now):
        for r in self.resources.values():
            if r["active_operation_id"]:
                continue
            intervals = [
                i
                for i in self.base["incidents"]
                if i["kind"] == "resource_loss" and i["target_id"] == r["id"] and i["status"] != "resolved"
            ]
            if intervals:
                r["status"] = (
                    "unavailable"
                    if any(
                        (self.base["sim_time_s"] if i["status"] == "pending" else i["starts_sim_s"])
                        <= now
                        < i["ends_sim_s"]
                        for i in intervals
                    )
                    else "available"
                )

    def record(self):
        now = self.state["sim_time_s"]
        for train in self.trains.values():
            if train["status"] == "expected" and now >= train["expected_arrival_sim_s"]:
                train["status"] = "waiting_entry"
        for op in self.operations.values():
            if (
                op["kind"] == "departure"
                and op["status"] not in {"running", "completed"}
                and all(self.operations[p]["status"] == "completed" for p in op["predecessor_ids"])
                and self.trains[op["train_id"]]["status"] == "on_station"
            ):
                self.trains[op["train_id"]]["status"] = "ready_departure"
        occupied = self.occupancy()
        pools = {}
        for pool, ids, kinds in (
            ("L1", ["L1"], {"shunt_transfer"}),
            ("SH1", ["SH1"], {"shunt_transfer"}),
            ("inspection", ["I1", "I2"], {"inspection", "departure_prep"}),
            ("CG1", ["CG1"], {"cargo"}),
        ):
            available = sum(
                self.resources[r]["status"] in {"available", "busy", "unavailable_pending"} for r in ids
            )
            busy = sum(self.resources[r]["active_operation_id"] is not None for r in ids)
            demand = sum(
                op["kind"] in kinds
                and (
                    op["status"] == "running"
                    or (
                        op["status"] not in {"completed", "running"}
                        and self.trains[op["train_id"]]["expected_arrival_sim_s"] <= now
                        and all(self.operations[p]["status"] == "completed" for p in op["predecessor_ids"])
                    )
                )
                for op in self.operations.values()
            )
            pools[pool] = dict(available=available, busy=busy, demand=demand)
        sample = dict(
            sim_time_s=now,
            occupied_track_ids=sorted(k for k, v in occupied.items() if v > 0),
            pools=pools,
            activity=any(t["status"] != "departed" for t in self.trains.values()),
        )
        if self.samples and self.samples[-1]["sim_time_s"] == now:
            self.samples[-1] = sample
        else:
            self.samples.append(sample)

    def start(self, op, now):
        train = self.trains[op["train_id"]]
        verify(
            all(self.operations[p]["status"] == "completed" for p in op["predecessor_ids"]),
            "PRECEDENCE",
            [op["id"]],
            "Predecessor not complete",
        )
        self.calendar_ok(op, now, now + op["duration_sim_s"])
        tracks, zones = self.locks(op)
        for other in self.operations.values():
            if other["status"] != "running":
                continue
            ot, oz = self.locks(other)
            verify(
                not set(op["resource_ids"]) & set(other["resource_ids"]),
                "RESOURCE_OVERLAP",
                [op["id"], other["id"]],
                "Double use of resource",
            )
            verify(not tracks & ot, "TRACK_OVERLAP", [op["id"], other["id"]], "Double track operation lock")
            verify(not zones & oz, "ZONE_OVERLAP", [op["id"], other["id"]], "Conflicting routes in throat")
        verify(
            all(z["availability"] == "open" for z in self.base["zones"] if z["id"] in zones),
            "ZONE_CALENDAR",
            [op["id"]],
            "Closed throat",
        )
        for tid in tracks:
            track = self.tracks[tid]
            active_outages = [
                i
                for i in self.base["incidents"]
                if i["kind"] == "track_closure" and i["target_id"] == tid and i["status"] != "resolved"
            ]
            verify(
                track["availability"] == "open" or bool(active_outages),
                "TRACK_CALENDAR",
                [op["id"], tid],
                "Track unavailable without an opening",
            )
        for rid in op["resource_ids"]:
            resource = self.resources[rid]
            verify(
                resource["status"] == "available" and resource["active_operation_id"] is None,
                "RESOURCE_UNAVAILABLE",
                [op["id"], rid],
                "Resource not available",
            )
            verify(
                resource["available_after_sim_s"] is None or now >= resource["available_after_sim_s"],
                "RESOURCE_NOT_READY",
                [op["id"], rid],
                "Resource ready time not reached",
            )
        kinds = {self.resources[r]["kind"] for r in op["resource_ids"]}
        if op["kind"] in {"arrival", "departure", "departure_prep"}:
            own = {train["traction_resource_id"], own_crew(train["id"])}
            verify(
                own <= set(op["resource_ids"]),
                "TRACTION_ASSIGNMENT",
                [op["id"]],
                "Own traction/crew required",
            )
        if op["kind"] == "arrival":
            target = self.tracks[op["target_track_id"]]
            verify(
                target["kind"] == "receiving_departure"
                and (train["processing_kind"] == "transit" or target["id"] in {"R3", "R4"})
                and (train["service_profile_id"] != "passenger_transit_v1" or target["id"] in {"R1", "R2"}),
                "TRACK_COMPATIBILITY",
                [op["id"], target["id"]],
                "Incompatible home R",
            )
            verify(
                train["location"]["kind"] == "boundary" and now >= train["expected_arrival_sim_s"],
                "ARRIVAL_ETA",
                [op["id"]],
                "Arrival before ETA / from wrong location",
            )
            verify(
                sum(t["status"] in {"on_station", "ready_departure"} for t in self.trains.values()) < 6,
                "ACTIVE_TRAIN_LIMIT",
                [train["id"]],
                "Active train limit",
            )
            verify(
                target["assigned_train_id"] is None and self.occupancy().get(target["id"], 0) == 0,
                "TRACK_RESIDENT",
                [op["id"], target["id"]],
                "Home held by another consist",
            )
            maximum = train["total_length_m"] + (20 if train["processing_kind"] != "transit" else 0)
            verify(
                maximum <= target["length_m"],
                "CAPACITY",
                [target["id"]],
                "Target formation plus traction too long",
            )
            verify(len(op["route_ids"]) == 1, "ROUTE_TEMPLATE", [op["id"]], "Arrival has one route")
            route = self.routes[op["route_ids"][0]]
            boundary = "BW" if train["direction"] == "W_E" else "BE"
            verify(
                (route["from_id"], route["to_id"]) == (boundary, target["id"]),
                "ROUTE_TEMPLATE",
                [op["id"]],
                "Wrong arrival direction/route",
            )
            train["status"] = "on_station"
            train["planned_track_id"] = target["id"]
            target["assigned_train_id"] = train["id"]
        elif op["kind"] == "shunt_transfer":
            verify(
                len(op["group_ids"]) == 1 and len(op["route_ids"]) == 6 and op["duration_sim_s"] == 420,
                "SHUNT_TEMPLATE",
                [op["id"]],
                "Shunt requires one group, six legs,420s",
            )
            source, target = op["source_track_id"], op["target_track_id"]
            verify(
                [(self.routes[r]["from_id"], self.routes[r]["to_id"]) for r in op["route_ids"]]
                == [("D1", "H"), ("H", source), (source, "H"), ("H", target), (target, "H"), ("H", "D1")],
                "SHUNT_ROUTE_TEMPLATE",
                [op["id"]],
                "No direct D1→source / forbidden reversal",
            )
            verify(
                set(op["resource_ids"]) == {"L1", "SH1"}
                and self.resources["L1"]["location"] == position("track", "D1"),
                "SHUNTER_LOCATION",
                [op["id"]],
                "Shunter/crew must start in depot",
            )
            group = self.wagon_groups[op["group_ids"][0]]
            verify(
                group["location"] == position("track", source) and group["assigned_train_id"] == train["id"],
                "GROUP_SOURCE",
                [group["id"]],
                "Group not at source / wrong owner",
            )
            if group["current_train_id"]:
                verify(
                    train["group_ids"][0] == group["id"],
                    "GROUP_ORDER",
                    [group["id"]],
                    "Middle group extraction",
                )
                allowed = "cargo" if train["processing_kind"] == "local" else "sorting"
                verify(
                    self.tracks[target]["kind"] == allowed,
                    "TRACK_COMPATIBILITY",
                    [target],
                    "Wrong process park",
                )
            else:
                verify(
                    target == train["current_track_id"],
                    "HOME_TRACK",
                    [target],
                    "Return must be to resident home",
                )
            foreign = [
                g
                for g in self.wagon_groups.values()
                if g["location"] == position("track", target) and g["id"] not in train["group_ids"]
            ]
            verify(not foreign, "TRACK_RESIDENT", [target], "Foreign group at target")
            verify(
                group["length_m"] + 20 <= self.tracks["H"]["length_m"]
                and self.occupancy().get(target, 0) + group["length_m"] + 20
                <= self.tracks[target]["length_m"],
                "CAPACITY",
                [target, "H"],
                "Shunt consist exceeds length",
            )
        elif op["kind"] == "cargo":
            verify(
                kinds == {"cargo_crew"}
                and op["source_track_id"] == op["target_track_id"]
                and self.tracks[op["source_track_id"]]["kind"] == "cargo",
                "RESOURCE_KIND",
                [op["id"]],
                "Wrong cargo front/crew",
            )
            verify(
                all(
                    self.wagon_groups[g]["current_train_id"] is None
                    and self.wagon_groups[g]["location"] == position("track", op["source_track_id"])
                    for g in op["group_ids"]
                ),
                "GROUP_SOURCE",
                op["group_ids"],
                "Cargo group not at front",
            )
        else:
            verify(
                train["location"] == position("track", op["source_track_id"]),
                "TRAIN_LOCATION",
                [train["id"]],
                "Train not at service/departure source",
            )
            if op["kind"] in {"inspection", "departure_prep"}:
                verify(
                    sum(self.resources[r]["kind"] == "inspection_crew" for r in op["resource_ids"]) == 1,
                    "RESOURCE_KIND",
                    [op["id"]],
                    "One inspection crew required",
                )
            if op["kind"] in {"departure", "departure_prep"}:
                verify(
                    train["group_ids"] == train["target_group_ids"],
                    "FORMATION_ORDER",
                    [train["id"]],
                    "Wrong target order",
                )
            if op["kind"] == "departure":
                boundary = "BE" if train["direction"] == "W_E" else "BW"
                verify(
                    len(op["route_ids"]) == 1
                    and (self.routes[op["route_ids"][0]]["from_id"], self.routes[op["route_ids"][0]]["to_id"])
                    == (op["source_track_id"], boundary),
                    "ROUTE_TEMPLATE",
                    [op["id"]],
                    "Wrong departure route",
                )
        op.update(status="running", actual_start_sim_s=now, progress=0)
        for rid in op["resource_ids"]:
            self.resources[rid].update(status="busy", active_operation_id=op["id"])

    def transition(self, op, kind, now):
        train = self.trains[op["train_id"]]
        if kind == "phase":
            elapsed = now - op["actual_start_sim_s"]
            group = self.wagon_groups[op["group_ids"][0]]
            if elapsed == 120 and group["current_train_id"]:
                verify(
                    train["group_ids"][0] == group["id"], "GROUP_ORDER", [group["id"]], "Inaccessible group"
                )
                train["group_ids"].remove(group["id"])
                group["current_train_id"] = None
            if elapsed == 360:
                group["location"] = position("track", op["target_track_id"])
                if op["target_track_id"] == train["current_track_id"]:
                    train["group_ids"].insert(0, group["id"])
                    group["current_train_id"] = train["id"]
        elif kind == "end":
            if op["kind"] == "arrival":
                self.train_position(train, position("track", op["target_track_id"]))
                train["actual_arrival_sim_s"] = now
            elif op["kind"] == "departure":
                self.train_position(train, position("departed"))
                train.update(status="departed", actual_departure_sim_s=now)
                self.tracks[op["source_track_id"]]["assigned_train_id"] = None
            elif op["kind"] == "shunt_transfer":
                self.resources["L1"]["location"] = position("track", "D1")
            elif op["kind"] == "cargo":
                for gid in op["group_ids"]:
                    self.wagon_groups[gid]["cargo_state"] = "loaded"
            op.update(status="completed", actual_end_sim_s=now, progress=1, phase=None)
            for rid in op["resource_ids"]:
                self.resources[rid].update(status="available", active_operation_id=None)
        for t in self.trains.values():
            if t["consist_kind"] == "wagon_groups":
                t["body_length_m"] = sum(self.wagon_groups[g]["length_m"] for g in t["group_ids"])
                t["total_length_m"] = t["body_length_m"] + 20

    def execute(self, until, prefix_only=False):
        now = self.state["sim_time_s"]
        timeline = []
        for op in self.operations.values():
            if op["status"] == "completed":
                continue
            running = op["status"] == "running"
            if prefix_only and not running:
                continue
            start = op["actual_start_sim_s"] if running else op["start_sim_s"]
            end = start + op["duration_sim_s"]
            if not running:
                timeline.append((start, 3, op["id"], "start"))
            timeline.append((end, 0, op["id"], "end"))
            if op["kind"] == "shunt_transfer":
                timeline.extend(
                    (start + offset, 1, op["id"], "phase")
                    for offset in PHASE_POINTS
                    if not running or start + offset > now
                )
        for incident in self.base["incidents"]:
            if incident["status"] != "resolved":
                timeline.extend(
                    (t, 2, "", "calendar") for t in (incident["starts_sim_s"], incident["ends_sim_s"])
                )
        timeline += [(until, 4, "", "sample")]
        self.inventory()
        self.record()
        for tick, grouped in groupby(sorted(timeline), key=lambda event: event[0]):
            if tick < now or tick > until:
                continue
            self.budget()
            self.state["sim_time_s"] = tick
            events = list(grouped)
            for _, _, oid, kind in events:
                if kind in {"phase", "end"}:
                    self.transition(self.operations[oid], kind, tick)
            self.update_positions(tick)
            self.resources_at(tick)
            for _, _, oid, kind in events:
                if kind == "start":
                    self.start(self.operations[oid], tick)
                    self.update_positions(tick)
            self.inventory()
            self.record()
        if not prefix_only:
            verify(
                all(o["status"] == "completed" for o in self.operations.values()),
                "INCOMPLETE_PLAN",
                [],
                "Full mandatory plan does not finish within horizon",
            )
            verify(
                all(t["status"] == "departed" for t in self.trains.values()),
                "INCOMPLETE_PLAN",
                [],
                "Not every known train departs",
            )
        return self.state


def validate_plan(
    base: State,
    operations: list[Operation],
    initial: State,
    cutover: int,
    horizon: int | None = None,
    deadline: float | None = None,
) -> ValidationResult:
    started = time.monotonic()
    try:
        source = base.model_dump(mode="json")
        items = [op.model_dump(mode="json") for op in operations]
        check_graph(source)
        current = {o["id"]: o for o in source["operations"]}
        candidate = {o["id"]: o for o in items}
        verify(
            len(items) == len(current) and set(candidate) == set(current),
            "OPERATION_IDENTITY",
            [],
            "Missing/duplicate/new operation IDs",
        )
        visited, active = set(), set()

        def visit(oid):
            verify(oid in candidate, "DAG_REFERENCE", [oid], "Unknown predecessor")
            verify(oid not in active, "DAG_CYCLE", [oid], "Predecessor cycle")
            if oid in visited:
                return
            active.add(oid)
            for predecessor in candidate[oid]["predecessor_ids"]:
                visit(predecessor)
            active.remove(oid)
            visited.add(oid)

        for oid in candidate:
            visit(oid)
        for oid, op in candidate.items():
            previous = current[oid]
            verify(
                all(op[k] == previous[k] for k in IMMUTABLE_OPERATION),
                "OPERATION_IDENTITY",
                [oid],
                "Operation process/subject/DAG/duration changed",
            )
            if previous["status"] in {"running", "completed"}:
                verify(op == previous, "FROZEN_PREFIX", [oid], "Running/completed prefix changed")
                continue
            verify(
                op["status"] == "planned"
                and op["actual_start_sim_s"] is None
                and op["actual_end_sim_s"] is None
                and op["progress"] == 0,
                "FUTURE_OPERATION_STATE",
                [oid],
                "Future operation has fictitious actual execution",
            )
            verify(
                op["start_sim_s"] is not None
                and op["end_sim_s"] is not None
                and op["start_sim_s"] >= max(base.sim_time_s, cutover)
                and op["end_sim_s"] - op["start_sim_s"] == op["duration_sim_s"],
                "OPERATION_INTERVAL",
                [oid],
                "Invalid duration / start before cutover",
            )
            verify(
                len(set(op["resource_ids"])) == len(op["resource_ids"]),
                "RESOURCE_IDENTITY",
                [oid],
                "Duplicate resource in operation",
            )
        replay = IndependentReplay(source, items, initial.model_dump(mode="json"), deadline)
        end = replay.execute(horizon if horizon is not None else cutover + 7200)
        return ValidationResult(True, [], replay.samples, end, (time.monotonic() - started) * 1000)
    except InvalidPlan as exc:
        return ValidationResult(False, [exc.error], elapsed_ms=(time.monotonic() - started) * 1000)
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        return ValidationResult(
            False,
            [dict(code="INVALID_PLAN_DATA", entity_ids=[], message=type(exc).__name__)],
            elapsed_ms=(time.monotonic() - started) * 1000,
        )


def physical_signature(state):
    """Small apply certificate: actual prefix/positions only, never future assignments."""
    return {
        "sim_time_s": state["sim_time_s"],
        "trains": [
            {
                k: t[k]
                for k in (
                    "id",
                    "status",
                    "location",
                    "group_ids",
                    "body_length_m",
                    "total_length_m",
                    "actual_arrival_sim_s",
                    "actual_departure_sim_s",
                )
            }
            for t in state["trains"]
        ],
        "groups": state["wagon_groups"],
        "resources": [
            {k: r[k] for k in ("id", "location", "active_operation_id")} for r in state["resources"]
        ],
        "prefix": [
            {
                k: o[k]
                for k in (
                    "id",
                    "status",
                    "actual_start_sim_s",
                    "actual_end_sim_s",
                    "phase",
                    "progress",
                    "route_ids",
                    "resource_ids",
                    "source_track_id",
                    "target_track_id",
                )
            }
            for o in state["operations"]
            if o["status"] in {"running", "completed"}
        ],
    }


def expected_prefix(base: State, initial: State, at_sim_s: int):
    source = base.model_dump(mode="json")
    if at_sim_s == base.sim_time_s:
        return physical_signature(source)
    replay = IndependentReplay(source, source["operations"], initial.model_dump(mode="json"))
    return physical_signature(replay.execute(at_sim_s, prefix_only=True))
