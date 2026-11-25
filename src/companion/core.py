"""Shared gateway state: database sessions, providers, background jobs,
audit and usage records."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from companion.config import Settings
from companion.models import AuditLog, Execution, SessionTask, UsageRecord, utcnow
from companion.providers import ProviderRegistry

log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "cancelled"}


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


class Core:
    def __init__(self, settings: Settings, sessions: async_sessionmaker[AsyncSession], registry: ProviderRegistry):
        self.settings = settings
        self.sessions = sessions
        self.registry = registry
        self.tasks: dict[str, asyncio.Task] = {}
        self.checkpointer: Any = None  # set by the gateway lifespan

    # permissions -------------------------------------------------------
    def require(self, capability: str, actor: str = "api") -> None:
        s = self.settings
        allowed = {
            "write": s.allow_write,
            "execute": s.allow_execute,
            "paid": s.allow_paid_calls,
            "approve": actor != "mcp" or s.mcp_can_approve,
        }[capability]
        if not allowed:
            raise PermissionDenied(f"'{capability}' is disabled by gateway settings for actor '{actor}'")

    # audit --------------------------------------------------------------
    async def audit(self, actor: str, action: str, target: str = "", outcome: str = "ok", **details: Any) -> None:
        async with self.sessions() as s:
            s.add(AuditLog(actor=actor, action=action, target=target, outcome=outcome, details=details))
            await s.commit()

    async def record_usage(self, **fields: Any) -> None:
        async with self.sessions() as s:
            s.add(UsageRecord(**fields))
            await s.commit()

    # executions ---------------------------------------------------------
    async def create_execution(self, kind: str, params: dict, actor: str) -> Execution:
        async with self.sessions() as s:
            ex = Execution(kind=kind, params=params, actor=actor)
            s.add(ex)
            await s.commit()
            return ex

    async def set_status(self, execution_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow()
        async with self.sessions() as s:
            await s.execute(update(Execution).where(Execution.id == execution_id).values(**fields))
            await s.commit()

    async def get_execution(self, execution_id: str) -> Execution:
        async with self.sessions() as s:
            ex = await s.get(Execution, execution_id)
            if ex is None:
                raise NotFound(f"execution {execution_id} not found")
            return ex

    def spawn(self, execution_id: str, work: Callable[[], Awaitable[None]]) -> None:
        async def runner() -> None:
            try:
                await work()
            except asyncio.CancelledError:
                await self.set_status(execution_id, status="cancelled", message="cancelled")
                raise
            except Exception as e:
                log.exception("execution %s failed", execution_id)
                await self.set_status(execution_id, status="failed", error=f"{type(e).__name__}: {e}")
            finally:
                self.tasks.pop(execution_id, None)

        self.tasks[execution_id] = asyncio.create_task(runner(), name=f"execution-{execution_id}")

    async def cancel(self, execution_id: str) -> Execution:
        ex = await self.get_execution(execution_id)
        if ex.status in TERMINAL:
            return ex
        task = self.tasks.get(execution_id)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.set_status(execution_id, status="cancelled", message="cancelled")
        async with self.sessions() as s:
            await s.execute(
                update(SessionTask)
                .where(SessionTask.execution_id == execution_id, SessionTask.status == "open")
                .values(status="cancelled", resolved_at=utcnow())
            )
            await s.commit()
        return await self.get_execution(execution_id)

    async def recover_after_restart(self) -> None:
        """Jobs that were mid-flight when the gateway stopped cannot resume
        (except workflows parked on a session task, which are checkpointed)."""
        async with self.sessions() as s:
            await s.execute(
                update(Execution)
                .where(Execution.status.in_(["queued", "running"]))
                .values(status="failed", error="gateway restarted while running", updated_at=utcnow())
            )
            await s.commit()

    async def open_task(self, execution_id: str, kind: str, payload: dict) -> SessionTask:
        async with self.sessions() as s:
            t = SessionTask(execution_id=execution_id, kind=kind, payload=payload)
            s.add(t)
            await s.commit()
            return t

    async def list_tasks(self, status: str = "open", kind: str | None = None, execution_id: str | None = None) -> list[SessionTask]:
        q = select(SessionTask).where(SessionTask.status == status).order_by(SessionTask.created_at)
        if kind:
            q = q.where(SessionTask.kind == kind)
        if execution_id:
            q = q.where(SessionTask.execution_id == execution_id)
        async with self.sessions() as s:
            return list((await s.scalars(q)).all())
