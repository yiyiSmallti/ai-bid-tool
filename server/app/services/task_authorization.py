"""Explicit task boundary for existing service entry points.

Locators are declared at the service, never derived from an HTTP path. A stored
parent is resolved under org RLS before task authorization, and the original
service still applies its object binding, domain, human and content gates.
"""

from collections.abc import Callable
from functools import wraps
from inspect import signature
from typing import Any

from sqlalchemy import select

from app.core.errors import not_found
from app.models import Base
from app.services.auth import Identity


def task_authorized(
    scope: str | Callable[[dict], str] = "task:read",
    *,
    task: str = "task_id",
    parent: tuple[str, str] | None = None,
    write: bool = False,
    lock: bool | None = None,
    review: bool = False,
    optional: bool = False,
):
    """Apply the declared task ceiling before any old service scope disclosure."""

    def decorate(function):
        binding = signature(function)

        @wraps(function)
        async def guarded(*args, **kwargs):
            from app.services.task_workflow import access

            bound = binding.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            session = values["session"]
            actor: Identity | None = values.get("actor", values.get("identity"))
            if actor is None:
                actor = session.info.get("actor")
            if actor is None:
                raise not_found()
            if parent:
                argument, table_name = parent
                table = Base.metadata.tables[table_name]
                stored = (
                    await session.execute(
                        select(table.c.task_id).where(
                            table.c.id == values[argument], table.c.org_id == actor.org_id
                        )
                    )
                ).first()
                if stored is None:
                    raise not_found()
                task_id = stored[0]
            else:
                parts = task.split(".")
                target: Any = values[parts[0]]
                for part in parts[1:]:
                    target = getattr(target, part)
                task_id = target
            if task_id is not None:
                action = scope(values) if callable(scope) else scope
                domain = (
                    {"bidder": "commercial", "technical": "technical"}.get(actor.role)
                    if review
                    else None
                )
                await access(
                    session, actor, task_id, scope=action, write=write, domain=domain, lock=lock
                )
            elif not optional:
                raise not_found()
            return await function(*args, **kwargs)

        return guarded

    return decorate
