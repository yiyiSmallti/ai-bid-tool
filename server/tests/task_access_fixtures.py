"""Explicit legacy access-state fixtures; all mutations use the isolated DB owner."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.models.entities import Membership, User
from app.models.team_workflow import TaskMember, TaskWorkflow
from conftest import PASSWORD, PASSWORD_HASH
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from task_fixtures import add_member as insert_member
from task_fixtures import owner_session


async def new_task(api, headers):
    response = await api.post("/tasks", headers=headers, json={"name": "Synthetic workflow task"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def person(api, admin_engine, org, role="bidder"):
    uid = uuid4()
    email = f"{uid}@example.test"
    with Session(admin_engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)})
        session.add(User(id=uid, email=email, password_hash=PASSWORD_HASH))
        session.flush()
        session.add(Membership(org_id=org, user_id=uid, role=role))
    response = await api.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org)}
    )
    assert response.status_code == 200
    return uid, {
        "Authorization": "Bearer " + response.json()["data"]["session"],
        "X-Org-Id": str(org),
    }


async def workflow(api, headers, task):
    response = await api.get(f"/tasks/{task}/progress", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["workflow"]


@contextmanager
def fixture_session(headers):
    org = UUID(headers["X-Org-Id"])
    with owner_session(org) as session:
        yield session, org


async def add_member(api, headers, task, user, revision, role="contributor", domains=None):
    with fixture_session(headers) as (session, org):
        state = session.scalar(
            select(TaskWorkflow).where(
                TaskWorkflow.org_id == org, TaskWorkflow.task_id == UUID(str(task))
            )
        )
        assert state is not None and state.revision == revision
        return insert_member(
            session, org, UUID(str(task)), UUID(str(user)), role=role, review_domains=domains or []
        )


async def archive_task(api, headers, task, *, archived=True):
    with fixture_session(headers) as (session, org):
        row = session.scalar(
            select(TaskWorkflow).where(
                TaskWorkflow.org_id == org, TaskWorkflow.task_id == UUID(str(task))
            )
        )
        assert row is not None
        row.state = "archived" if archived else "active"
        row.revision += 1
        row.access_epoch += 1
        row.archived_at = datetime.now(UTC) if archived else None
        row.archived_by_user_id = row.owner_user_id if archived else None


async def remove_member(api, headers, task, user):
    with fixture_session(headers) as (session, org):
        row = session.scalar(
            select(TaskMember).where(
                TaskMember.org_id == org,
                TaskMember.task_id == UUID(str(task)),
                TaskMember.user_id == UUID(str(user)),
            )
        )
        state = session.scalar(
            select(TaskWorkflow).where(
                TaskWorkflow.org_id == org, TaskWorkflow.task_id == UUID(str(task))
            )
        )
        assert row is not None and state is not None and row.role != "owner"
        state.revision += 1
        state.access_epoch += 1
        row.active = False
        row.revision += 1
        row.workflow_revision = state.revision
        row.changed_by_user_id = state.owner_user_id
        row.changed_at = datetime.now(UTC)
