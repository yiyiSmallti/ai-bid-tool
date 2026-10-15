"""Gateway project member, owner and sharing routes."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.common import result
from app.schemas.contracts import Result
from app.schemas.members import ProjectMemberAdd, ProjectOwnerChange, ProjectSharingUpdate
from app.services import project_members


def create_router(context: Callable[..., Any]) -> APIRouter:
    router = APIRouter()

    def listing(command, rows):
        return result(command, items=[row.model_dump(mode="json") for row in rows])

    @router.get("/tasks/{task_id}/members", name="task_member_list", response_model=Result)
    async def task_member_list(task_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        rows = await project_members.list_members(session, identity, task_id)
        return listing("task member list", rows)

    @router.post("/tasks/{task_id}/members", name="task_member_add", response_model=Result)
    async def task_member_add(
        task_id: UUID, body: ProjectMemberAdd, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        rows = await project_members.add_members(session, identity, task_id, body)
        return listing("task member add", rows)

    @router.delete(
        "/tasks/{task_id}/members/{member_id}", name="task_member_remove", response_model=Result
    )
    async def task_member_remove(
        task_id: UUID, member_id: UUID, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        await project_members.remove_member(session, identity, task_id, member_id)
        return result("task member remove", {"member_id": str(member_id), "removed": True})

    @router.post("/tasks/{task_id}/owner", name="task_owner_change", response_model=Result)
    async def task_owner_change(
        task_id: UUID, body: ProjectOwnerChange, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        rows = await project_members.change_owner(session, identity, task_id, body)
        return listing("task owner", rows)

    @router.patch("/tasks/{task_id}/sharing", name="task_sharing_update", response_model=Result)
    async def task_sharing_update(
        task_id: UUID, body: ProjectSharingUpdate, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        task = await project_members.update_sharing(session, identity, task_id, body)
        return result(
            "task sharing",
            {"id": str(task.id), "export_to_personal_allowed": task.export_to_personal_allowed},
        )

    return router
