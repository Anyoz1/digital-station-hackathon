"""Simulator availability calendars. Validator has its own calendar checks."""

from .contracts import State


def interval_available(state: State, op, start, end, locks):
    tracks, _ = locks
    for incident in state.incidents:
        if incident.status == "resolved":
            continue
        begin = state.sim_time_s if incident.status == "pending" else incident.starts_sim_s
        overlap = start < incident.ends_sim_s and end > begin
        if incident.kind == "track_closure" and incident.target_id in tracks and overlap:
            return False
        if incident.kind == "resource_loss" and incident.target_id in op.resource_ids and overlap:
            return False
        if incident.kind == "destination_block" and op.kind == "departure":
            train = next(t for t in state.trains if t.id == op.train_id)
            if (
                train.destination_id == incident.target_id
                and incident.starts_sim_s <= end + 600 < incident.ends_sim_s
            ):
                return False
    return True


def refresh_calendars(state: State):
    transitions = []
    now = state.sim_time_s
    for incident in state.incidents:
        if incident.status != "resolved" and now >= incident.ends_sim_s:
            incident.status = "resolved"
            transitions.append(dict(kind="incident_resolved", entity_ids=[incident.id], sim_time_s=now))
        elif incident.status == "pending" and now >= incident.starts_sim_s:
            incident.status = "active"
            transitions.append(dict(kind="incident_effective", entity_ids=[incident.id], sim_time_s=now))
    for track in state.tracks:
        related = [i for i in state.incidents if i.kind == "track_closure" and i.target_id == track.id]
        active = [i for i in related if i.status != "resolved"]
        if related:
            track.availability = (
                "closed"
                if any(i.status == "active" and i.starts_sim_s <= now < i.ends_sim_s for i in active)
                else ("closure_pending" if any(i.status == "pending" for i in active) else "open")
            )
    for resource in state.resources:
        related = [i for i in state.incidents if i.kind == "resource_loss" and i.target_id == resource.id]
        active = [i for i in related if i.status != "resolved"]
        if related:
            resource.available_after_sim_s = max((i.ends_sim_s for i in active), default=None)
            if resource.active_operation_id:
                resource.status = "unavailable_pending" if active else "busy"
            else:
                resource.status = "unavailable" if active else "available"
        elif (
            resource.status == "unavailable"
            and resource.available_after_sim_s is not None
            and now >= resource.available_after_sim_s
        ):
            resource.status, resource.available_after_sim_s = "available", None
    return transitions


def next_calendar_times(state):
    now = state.sim_time_s
    return [
        t
        for i in state.incidents
        if i.status != "resolved"
        for t in (i.starts_sim_s, i.ends_sim_s)
        if t > now
    ] + [
        r.available_after_sim_s
        for r in state.resources
        if r.available_after_sim_s is not None and r.available_after_sim_s > now
    ]
