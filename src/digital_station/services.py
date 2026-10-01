"""Actor commands for configuration and manual service confirmation."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from .db import AppUser, ConfigRevision
from .runtime import ActorError


async def patch_config(actor, user_id, body, ingress):
    previous, _ = await actor.coordinator.command_check(user_id, body, "config")
    if previous:
        return previous
    async with actor.sessions() as session:
        user = await session.get(AppUser, user_id)
        version = (await session.scalar(select(func.max(ConfigRevision.version)))) + 1
    if user is None or user.role != "admin":
        raise ActorError(403, "FORBIDDEN", "Конфигурацию изменяет только администратор")
    config = {
        **actor.config,
        **body.patch.model_dump(mode="json", exclude_none=True),
        "config_version": version,
    }
    state = actor.state.model_copy(deep=True)
    state.input_revision += 1
    state.config_version = version
    record = ConfigRevision(version=version, actor_id=user_id, created_at=datetime.now(UTC), payload=config)
    receipt = await actor.coordinator.request(
        "config_changed",
        ingress,
        user_id,
        body,
        state,
        [dict(kind="config_changed", entity_ids=[], config_version=version)],
        receipt_scope="config",
        receipt_status=200,
        receipt_result={"config_version": version},
        event_kind="config_changed",
        config_record=record,
    )
    actor.coordinator.sync_simulator()
    return receipt


async def complete_operation(actor, user_id, body, operation_id, ingress):
    scope = f"complete:{operation_id}"
    previous, _ = await actor.coordinator.command_check(user_id, body, scope)
    if previous:
        return previous
    async with actor.sessions() as session:
        user = await session.get(AppUser, user_id)
    op = next((o for o in actor.state.operations if o.id == operation_id), None)
    if op is None:
        raise ActorError(404, "NOT_FOUND", "Операция не найдена")
    if user is None or user.role not in {"operator", "admin"}:
        raise ActorError(403, "FORBIDDEN", "Требуется исполнитель или администратор")
    if user.role == "operator" and op.assigned_user_id != user_id:
        raise ActorError(403, "FORBIDDEN", "Операция назначена другому исполнителю")
    if op.kind not in {"inspection", "cargo", "departure_prep"} or op.execution_mode != "manual":
        raise ActorError(409, "INVALID_TRANSITION", "Вручную подтверждается только manual service operation")
    # Flush clock before this command; authorization is checked against immutable assignment.
    if not actor.simulator.manual_ready(op):
        raise ActorError(409, "INVALID_TRANSITION", "Предусловия или минимальная длительность не выполнены")
    from .simulator import Simulator

    simulator = Simulator(actor.state, actor.coordinator.initial)
    completed = simulator.operations[operation_id]
    simulator.complete(completed)
    simulator.refresh()
    transitions = [
        dict(
            kind="manual_operation_completed",
            entity_ids=[operation_id, op.train_id],
            sim_time_s=simulator.state.sim_time_s,
        )
    ]
    # The former departure may already be blocked while waiting for this human.
    # Publish its factual readiness now, before immutable replan input/prefix capture.
    train = simulator.trains[op.train_id]
    departure = next(
        o for o in simulator.state.operations if o.train_id == train.id and o.kind == "departure"
    )
    if train.status == "on_station" and all(
        simulator.operations[p].status == "completed" for p in departure.predecessor_ids
    ):
        train.status = "ready_departure"
        transitions.append(dict(kind="train_ready_departure", entity_ids=[train.id]))
    candidate = simulator.state.model_copy(deep=True)
    candidate.input_revision += 1
    receipt = await actor.coordinator.request(
        "manual_confirmation",
        ingress,
        user_id,
        body,
        candidate,
        transitions,
        receipt_status=200,
        receipt_result={"operation_id": operation_id},
        receipt_scope=scope,
        event_kind="operation_completed",
    )
    actor.coordinator.sync_simulator()
    return receipt
