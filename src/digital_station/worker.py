"""Single isolated planner process with a hard watchdog and replace-on-cancel."""

import asyncio
import multiprocessing as mp
import os
import signal
import time

from .planner import search


def worker_loop(connection):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    connection.send({"ready": True, "pid": os.getpid()})
    try:
        while True:
            data = connection.recv()
            if data is None:
                return
            try:
                result = search(data)
                state = data["state"]
                result.update(
                    worker_pid=os.getpid(),
                    run_id=state["run_id"],
                    base_input_revision=state["input_revision"],
                    config_version=state["config_version"],
                    base_active_plan_id=state["active_plan_id"],
                    base_state_version=state["state_version"],
                )
                connection.send(result)
            except Exception as exc:
                connection.send(
                    dict(
                        status="failed",
                        candidates=[],
                        compute_ms=0,
                        validation_ms=0,
                        outcome_reason_codes=[f"WORKER_{type(exc).__name__}"],
                    )
                )
    except (EOFError, OSError):
        return
    finally:
        connection.close()


class PlannerWorker:
    def __init__(self):
        self.context = mp.get_context("spawn")
        self.process = None
        self.connection = None
        self.ready = False
        self.lock = asyncio.Lock()
        self.pid = None

    async def start(self):
        parent, child = self.context.Pipe()
        self.process = self.context.Process(target=worker_loop, args=(child,), daemon=True)
        self.process.start()
        child.close()
        self.connection = parent
        hello = await asyncio.wait_for(asyncio.to_thread(parent.recv), 3)
        self.pid, self.ready = hello["pid"], hello["ready"]

    async def stop(self):
        self.ready = False
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            await asyncio.to_thread(self.process.join, 0.25)
            if self.process.is_alive():
                self.process.kill()
                await asyncio.to_thread(self.process.join, 0.25)
            self.process.close()
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    async def solve(self, data):
        async with self.lock:
            if not self.ready:
                await self.start()
            assert self.connection is not None
            try:
                self.connection.send(data)
                remaining = max(0.001, data["deadline"] - time.monotonic())
                return await asyncio.wait_for(asyncio.to_thread(self.connection.recv), remaining)
            except (TimeoutError, asyncio.CancelledError, EOFError, OSError):
                await self.stop()
                raise
