"""Actor-owned replan lifecycle, isolated CPU work, frozen-prefix/CAS apply checks."""

import asyncio
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from .contracts import Operation, PlanDetail, PlanSummary, ReplanJob
from .db import CommandReceipt, ConfigRevision, OptimizationRun, PlanRecord
from .scenario import utc_now
from .validator import expected_prefix, physical_signature
from .worker import PlannerWorker


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def body_hash(body, scope=None):
    payload = body.model_dump(mode="json")
    if scope is not None:
        payload = {"scope": scope, "body": payload}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class PlanningCoordinator:
    def __init__(self, actor, initial, config):
        self.actor, self.initial, self.config = actor, initial, config
        self.worker = PlannerWorker()
        self.owner = None
        self.origin_wall = None
        self.deadline = None
        self.cutover = None
        self.task = None
        self.jobs = {}
        self.details = {}
        self.certificates = {}
        self.dirty_jobs = set()
        self.apply_check_ms = []

    async def start(self):
        await self.worker.start()
        async with self.actor.sessions() as session:
            for record in await session.scalars(
                select(PlanRecord).where(PlanRecord.run_id == self.actor.run_id)
            ):
                self.details[record.id] = record.payload["detail"]
            for record in await session.scalars(
                select(OptimizationRun).where(OptimizationRun.run_id == self.actor.run_id)
            ):
                self.jobs[record.id] = record.payload
                if record.status in {"queued", "running"}:
                    job = self.jobs[record.id]["job"]
                    job.update(status="failed", finished_at=utc_now(), outcome_reason_codes=["RESTART"])
                    self.dirty_jobs.add(record.id)

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.worker.stop()

    async def command_check(self, user_id, body, scope=None):
        from .runtime import ActorError

        digest = body_hash(body, scope)
        async with self.actor.sessions() as session:
            previous = await session.get(CommandReceipt, (user_id, body.request_id))
            if previous:
                if previous.body_hash != digest:
                    raise ActorError(409, "IDEMPOTENCY_MISMATCH", "request_id уже использован с другим телом")
                return previous.payload, digest
        if body.run_id != self.actor.run_id:
            raise ActorError(409, "RUN_MISMATCH", "Запрошен другой run")
        if body.expected_input_revision != self.actor.state.input_revision:
            raise ActorError(
                409,
                "REVISION_MISMATCH",
                "Условия станции изменились",
                {"current_input_revision": self.actor.state.input_revision},
            )
        return None, digest

    def update_can_apply(self, state):
        for plan in state.plans:
            plan.can_apply = bool(
                state.mode == "paused"
                and state.sim_time_s == plan.cutover_sim_s
                and plan.base_input_revision == state.input_revision
                and plan.config_version == state.config_version
                and state.last_replan
                and state.last_replan.status == "succeeded"
                and plan.optimization_run_id == state.last_replan.id
                and plan.status == "proposed"
            )

    async def write_records(self, session, candidate):
        if candidate.last_replan:
            job = candidate.last_replan.model_dump(mode="json")
            cached = self.jobs.setdefault(job["id"], {})
            if cached.get("job") != job:
                cached["job"] = job
                self.dirty_jobs.add(job["id"])
        for job_id in self.dirty_jobs:
            payload = self.jobs[job_id]
            job = payload["job"]
            record = await session.get(OptimizationRun, job_id)
            if record is None:
                record = OptimizationRun(id=job_id, run_id=self.actor.run_id)
                session.add(record)
            record.status, record.base_revision = job["status"], job["base_input_revision"]
            record.requested_at, record.started_at, record.finished_at = (
                utc(job["created_at"]),
                utc(job["started_at"]),
                utc(job["finished_at"]),
            )
            record.elapsed_ms, record.payload = (
                job["elapsed_ms"],
                {k: v for k, v in payload.items() if k != "base"},
            )
        await session.flush()
        previous = {p.id: p for p in self.actor.state.plans}
        for plan in candidate.plans:
            if plan.id in previous and plan == previous[plan.id]:
                continue
            detail = self.details[plan.id]
            detail.update(plan.model_dump(mode="json"))
            record = await session.get(PlanRecord, plan.id)
            if record is None:
                record = PlanRecord(
                    id=plan.id, run_id=self.actor.run_id, optimization_run_id=plan.optimization_run_id
                )
                session.add(record)
            record.status, record.validity = plan.status, plan.validity
            record.base_revision, record.config_version = plan.base_input_revision, plan.config_version
            record.created_at, record.objective = utc(plan.created_at), plan.objective_value
            record.payload = {
                "detail": detail.copy(),
                "operations_sha256": self.certificates.get(plan.id, {}).get("operations_sha256"),
            }
        retained = {p.id for p in candidate.plans}
        for old in self.actor.state.plans:
            if old.id not in retained:
                record = await session.get(PlanRecord, old.id)
                if record:
                    record.status = "superseded"
                    payload = record.payload.copy()
                    payload["detail"] = {**payload["detail"], "status": "superseded", "can_apply": False}
                    record.payload = payload

    async def request(
        self,
        reason,
        ingress,
        user_id=None,
        body=None,
        candidate=None,
        transitions=None,
        spawn=True,
        receipt_status=202,
        receipt_result=None,
        receipt_scope=None,
        event_kind="replan_updated",
    ):
        if body:
            previous, digest = await self.command_check(user_id, body, receipt_scope)
            if previous:
                return previous
        actor = self.actor
        state = candidate or actor.state.model_copy(deep=True)
        now = time.monotonic()
        if self.owner and state.last_replan and state.last_replan.status == "queued":
            assert self.deadline is not None
            job = state.last_replan
            job.base_input_revision = state.input_revision
            self.cutover = (
                state.sim_time_s + math.ceil(max(0, self.deadline - now) * state.speed)
                if state.mode == "running"
                else state.sim_time_s
            )
        else:
            if self.owner and state.last_replan:
                old = state.last_replan.model_dump(mode="json")
                old.update(status="stale", finished_at=utc_now(), outcome_reason_codes=["INPUT_CHANGED"])
                self.jobs[old["id"]]["job"] = old
                self.dirty_jobs.add(old["id"])
                if self.task:
                    self.task.cancel()
            if self.deadline is None:
                age = max(0, (datetime.now(UTC) - utc(ingress)).total_seconds())
                self.origin_wall = now - age
                self.deadline = self.origin_wall + 5
            job = ReplanJob(
                id=f"rp-{uuid4()}",
                status="queued",
                reason=reason,
                created_at=ingress,
                started_at=None,
                finished_at=None,
                base_input_revision=state.input_revision,
                candidate_plan_ids=[],
                applied_plan_id=None,
                elapsed_ms=0,
                compute_ms=None,
                validation_ms=None,
                deadline_ms=5000,
                outcome_reason_codes=[],
            )
            state.last_replan = job
            self.jobs[job.id] = {"job": job.model_dump(mode="json")}
            self.owner = job.id
            self.cutover = (
                state.sim_time_s + math.ceil(max(0, self.deadline - now) * state.speed)
                if state.mode == "running"
                else state.sim_time_s
            )
        self.update_can_apply(state)
        self.dirty_jobs.add(job.id)
        actor.simulator.launch_barrier = True
        result = {"replan_id": job.id, **(receipt_result or {})}
        receipt = await actor.persist(
            state,
            (transitions or []) + [dict(kind="replan_queued", entity_ids=[job.id])],
            event_kind,
            actor_id=user_id,
            command=(body, digest) if body else None,
            ingested=ingress,
            receipt_status=receipt_status,
            receipt_result=result,
        )
        if spawn and (self.task is None or self.task.done() or self.task.cancelling()):
            self.task = asyncio.create_task(self.perform(job.id), name=f"planner-{job.id}")
        return receipt

    async def incidents(self, user_id, body, ingress):
        from .incidents import apply_batch

        previous, _ = await self.command_check(user_id, body)
        if previous:
            return previous
        candidate, ids = apply_batch(self.actor.state, body.items)
        receipt = await self.request(
            "incident_batch",
            ingress,
            user_id,
            body,
            candidate,
            [
                dict(kind="incident_created", entity_ids=[i.id, i.target_id, *i.affected_operation_ids])
                for i in candidate.incidents
                if i.id in ids
            ],
            receipt_status=201,
            receipt_result={"incident_ids": ids},
            event_kind="incident_batch",
        )
        self.sync_simulator()
        return receipt

    async def resolve_incident(self, user_id, body, incident_id, ingress):
        from .incidents import resolve

        scope = f"resolve:{incident_id}"
        previous, _ = await self.command_check(user_id, body, scope)
        if previous:
            return previous
        candidate = resolve(self.actor.state, incident_id)
        receipt = await self.request(
            "incident_resolved",
            ingress,
            user_id,
            body,
            candidate,
            [dict(kind="incident_resolved", entity_ids=[incident_id])],
            receipt_status=200,
            receipt_result={"incident_id": incident_id},
            receipt_scope=scope,
            event_kind="incident_resolved",
        )
        self.sync_simulator()
        return receipt

    def sync_simulator(self):
        simulator = self.actor.simulator
        simulator.state = self.actor.state.model_copy(deep=True)
        simulator.index()
        simulator.refresh()
        simulator.launch_barrier = bool(self.owner)

    async def initialize(self):
        await self.request("init", utc_now(), spawn=False)
        data = await self.start_job(self.owner)
        result = await self.worker.solve(data)
        await self.result(self.owner, result)

    async def start_job(self, job_id):
        if job_id != self.owner:
            return None
        assert self.deadline is not None
        state = self.actor.state.model_copy(deep=True)
        state.last_replan.status = "running"
        state.last_replan.started_at = utc_now()
        await self.actor.persist(state, [dict(kind="replan_started", entity_ids=[job_id])], "replan_updated")
        base = self.actor.state.model_copy(deep=True)
        async with self.actor.sessions() as session:
            self.config = (await session.get(ConfigRevision, base.config_version)).payload
        if base.mode == "paused":
            self.cutover = base.sim_time_s
        else:
            self.cutover = max(self.cutover, base.sim_time_s)
        data = dict(
            state=base.model_dump(mode="json"),
            initial=self.initial.model_dump(mode="json"),
            config=self.config,
            cutover_sim_s=self.cutover,
            deadline=min(self.deadline - 0.15, time.monotonic() + 3),
        )
        self.jobs[job_id]["inputs"] = data
        self.jobs[job_id]["base"] = base
        self.jobs[job_id]["cutover"] = self.cutover
        self.dirty_jobs.add(job_id)
        # Persist the exact immutable input before handing it to the CPU worker.
        # No new domain State/seq: this is job metadata, not a simulation event.
        async with self.actor.sessions() as session, session.begin():
            await self.write_records(session, self.actor.state)
        self.dirty_jobs.clear()
        return data

    async def perform(self, job_id):
        try:
            await asyncio.sleep(0.15)
            data = await self.actor.call("planner_start", job_id)
            if data is None:
                return
            result = await self.worker.solve(data)
        except asyncio.CancelledError:
            return
        except TimeoutError:
            result = dict(
                status="timeout",
                candidates=[],
                compute_ms=None,
                validation_ms=None,
                outcome_reason_codes=["WORKER_DEADLINE"],
            )
        except Exception:
            result = dict(
                status="failed",
                candidates=[],
                compute_ms=None,
                validation_ms=None,
                outcome_reason_codes=["WORKER_FAILED"],
            )
        if self.actor.ready:
            await self.actor.call("planner_result", (job_id, result))

    def valid_result(self, job_id, result):
        started = time.monotonic()
        state = self.actor.state
        base = self.jobs[job_id]["base"]
        data = self.jobs[job_id]["inputs"]
        digest = hashlib.sha256(
            json.dumps({k: v for k, v in data.items() if k != "deadline"}, sort_keys=True).encode()
        ).hexdigest()
        matches = (
            result.get("run_id") == state.run_id
            and result.get("base_input_revision") == state.input_revision
            and result.get("config_version") == state.config_version
            and result.get("base_active_plan_id") == state.active_plan_id
            and result.get("base_state_version") == base.state_version
            and result.get("input_digest") == digest
            and state.sim_time_s <= self.cutover
            and physical_signature(state.model_dump(mode="json"))
            == expected_prefix(base, self.initial, state.sim_time_s)
        )
        self.apply_check_ms.append((time.monotonic() - started) * 1000)
        return matches

    async def result(self, job_id, result):
        if job_id != self.owner:
            return False
        assert self.origin_wall is not None and self.deadline is not None
        actor = self.actor
        state = actor.state.model_copy(deep=True)
        job = state.last_replan
        job.compute_ms, job.validation_ms = result.get("compute_ms"), result.get("validation_ms")
        job.finished_at = utc_now()
        job.elapsed_ms = (time.monotonic() - self.origin_wall) * 1000
        job.status = result["status"]
        job.outcome_reason_codes = result.get("outcome_reason_codes", [])
        if time.monotonic() >= self.deadline:
            job.status, job.outcome_reason_codes = "timeout", ["TOTAL_DEADLINE"]
        elif job.status == "succeeded" and not self.valid_result(job_id, result):
            job.status, job.outcome_reason_codes = "stale", ["STALE_INPUT_OR_PREFIX"]
        public_candidates = []
        if job.status == "succeeded":
            for index, candidate in enumerate(result["candidates"]):
                digest = hashlib.sha256(
                    json.dumps(candidate["operations"], sort_keys=True).encode()
                ).hexdigest()
                if not candidate["validator"]["passed"] or candidate.get("operations_sha256") != digest:
                    continue
                pid = f"plan-{job_id}-{index}"
                detail = PlanDetail(
                    id=pid,
                    status="proposed",
                    validity="feasible",
                    optimization_run_id=job_id,
                    strategy=candidate["strategy"],
                    base_input_revision=job.base_input_revision,
                    base_state_version=self.jobs[job_id]["base"].state_version,
                    base_active_plan_id=self.jobs[job_id]["base"].active_plan_id,
                    config_version=state.config_version,
                    created_at=utc_now(),
                    cutover_sim_s=self.cutover,
                    forecast=candidate["forecast"],
                    objective_value=candidate["objective_value"],
                    changed_operation_ids=candidate["changed_operation_ids"],
                    explanation=[
                        f"Допустимый {candidate['strategy']}; J={candidate['objective_value']:.6f}; optimal не доказан.",
                        *[
                            f"{i.id}: {i.kind} для {i.target_id}"
                            for i in state.incidents
                            if i.status != "resolved"
                        ],
                        *candidate["explanation"],
                    ],
                    can_apply=False,
                    operations=candidate["operations"],
                    validator=candidate["validator"],
                )
                self.details[pid] = detail.model_dump(mode="json")
                self.certificates[pid] = candidate
                public_candidates.append(
                    PlanSummary.model_validate(detail.model_dump(exclude={"operations", "validator"}))
                )
            if not public_candidates:
                job.status, job.outcome_reason_codes = "failed", ["INVALID_CERTIFICATE"]
        if public_candidates:
            state.plans = public_candidates
            best = public_candidates[0]
            best.status = "active"
            state.active_plan_id = best.id
            frozen = {o.id: o for o in state.operations if o.status in {"running", "completed"}}
            state.operations = [
                frozen[o["id"]] if o["id"] in frozen else Operation.model_validate(o)
                for o in self.details[best.id]["operations"]
            ]
            for train in state.trains:
                if train.status in {"expected", "waiting_entry"}:
                    train.planned_track_id = next(
                        o.target_track_id
                        for o in state.operations
                        if o.train_id == train.id and o.kind == "arrival"
                    )
            state.conflicts = []
            job.candidate_plan_ids = [p.id for p in public_candidates]
            job.applied_plan_id = best.id
        self.jobs[job_id]["result"] = {k: v for k, v in result.items() if k != "candidates"}
        if self.apply_check_ms:
            self.jobs[job_id]["apply_check_ms"] = self.apply_check_ms[-1]
        self.jobs[job_id]["job"] = job.model_dump(mode="json")
        self.dirty_jobs.add(job_id)
        self.update_can_apply(state)
        await actor.persist(
            state,
            [
                dict(
                    kind="plan_applied" if public_candidates else "replan_finished",
                    entity_ids=[job_id, *job.candidate_plan_ids],
                )
            ],
            "plan_applied" if public_candidates else "replan_updated",
            checkpoint=bool(public_candidates),
        )
        # Record elapsed through the first durable result publication, not just CPU completion.
        completed = actor.state.model_copy(deep=True)
        completed.last_replan.elapsed_ms = (time.monotonic() - self.origin_wall) * 1000
        completed.last_replan.finished_at = utc_now()
        await actor.persist(
            completed, [dict(kind="replan_timing_recorded", entity_ids=[job_id])], "replan_updated"
        )
        actor.simulator.state = actor.state.model_copy(deep=True)
        actor.simulator.index()
        actor.simulator.refresh()
        actor.simulator.launch_barrier = False
        self.owner, self.origin_wall, self.deadline, self.cutover = None, None, None, None
        self.task = None
        return bool(public_candidates)

    async def timeout_current(self):
        if self.task:
            self.task.cancel()
        await self.result(
            self.owner,
            dict(
                status="timeout",
                candidates=[],
                compute_ms=None,
                validation_ms=None,
                outcome_reason_codes=["TOTAL_DEADLINE"],
            ),
        )

    async def apply(self, user_id, body, plan_id, ingress):
        from .runtime import ActorError

        previous, digest = await self.command_check(user_id, body, scope=f"apply:{plan_id}")
        if previous:
            return previous
        state = self.actor.state.model_copy(deep=True)
        selected = next((p for p in state.plans if p.id == plan_id), None)
        active = next((p for p in state.plans if p.id == state.active_plan_id), None)
        if (
            selected is None
            or active is None
            or not selected.can_apply
            or self.owner
            or active.optimization_run_id != selected.optimization_run_id
        ):
            raise ActorError(409, "PLAN_STALE", "Нужны пауза и свежий расчёт для выбора альтернативы")
        detail = self.details[plan_id]
        if not detail["validator"]["passed"]:
            raise ActorError(409, "INVALID_PLAN", "План не прошёл независимую проверку")
        job_id = selected.optimization_run_id
        base = self.jobs[job_id].get("base")
        if base is None or physical_signature(state.model_dump(mode="json")) != expected_prefix(
            base, self.initial, state.sim_time_s
        ):
            raise ActorError(409, "PLAN_STALE", "Фактический префикс изменился")
        frozen = {o.id: o for o in state.operations if o.status in {"running", "completed"}}
        state.operations = [
            frozen[o["id"]] if o["id"] in frozen else Operation.model_validate(o)
            for o in detail["operations"]
        ]
        active.status = "superseded"
        selected.status = "active"
        state.active_plan_id = plan_id
        for train in state.trains:
            if train.status in {"expected", "waiting_entry"}:
                train.planned_track_id = next(
                    o.target_track_id
                    for o in state.operations
                    if o.train_id == train.id and o.kind == "arrival"
                )
        state.input_revision += 1
        self.update_can_apply(state)
        receipt = await self.actor.persist(
            state,
            [dict(kind="plan_applied", entity_ids=[plan_id])],
            "plan_applied",
            actor_id=user_id,
            command=(body, digest),
            ingested=ingress,
            checkpoint=True,
            receipt_result={"plan_id": plan_id},
        )
        self.actor.simulator.state = self.actor.state.model_copy(deep=True)
        self.actor.simulator.index()
        self.actor.simulator.refresh()
        return receipt
