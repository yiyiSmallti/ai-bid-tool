"""Offline reviewed imports; application access fails closed until tasks are mapped."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Job, Membership, Org, Task, User, VendorCall
from app.models.team_workflow import TaskEventHead, TaskMember, TaskWorkflow


class ImportMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: Literal["contributor", "reviewer", "observer"]
    review_domains: list[Literal["commercial", "technical"]] = Field(
        default_factory=list, max_length=1
    )

    @model_validator(mode="after")
    def domains_match_role(self):
        if self.role == "reviewer" and not self.review_domains:
            raise ValueError("reviewer requires a domain")
        if self.role == "observer" and self.review_domains:
            raise ValueError("observer cannot review")
        return self


class ImportTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    owner_user_id: UUID
    members: list[ImportMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_users(self):
        users = [self.owner_user_id, *(m.user_id for m in self.members)]
        if len(users) != len(set(users)):
            raise ValueError("duplicate task member")
        return self


class ReviewedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: Literal[True]
    reviewed_by_user_id: UUID
    org_id: UUID
    tasks: list[ImportTask]

    @model_validator(mode="after")
    def unique_tasks(self):
        ids = [item.task_id for item in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task mapping")
        return self


def report(session: Session, org_id: UUID) -> dict:
    task_ids = set(session.scalars(select(Task.id).where(Task.org_id == org_id)))
    mapped = set(session.scalars(select(TaskWorkflow.task_id).where(TaskWorkflow.org_id == org_id)))
    unfinished = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.org_id == org_id, Job.status.in_(["queued", "running"]))
    )
    unsettled = session.scalar(
        select(func.count())
        .select_from(VendorCall)
        .where(VendorCall.org_id == org_id, VendorCall.state.in_(["pending", "unknown"]))
    )
    creator_issues = []
    for task, member, user in session.execute(
        select(Task, Membership, User)
        .outerjoin(
            Membership, (Membership.org_id == Task.org_id) & (Membership.user_id == Task.created_by)
        )
        .outerjoin(User, User.id == Task.created_by)
        .where(Task.org_id == org_id)
    ):
        if (
            member is None
            or user is None
            or not member.active
            or not user.active
            or member.role not in {"admin", "bidder"}
        ):
            creator_issues.append(
                {
                    "task_id": str(task.id),
                    "creator_user_id": str(task.created_by),
                    "code": "creator_not_eligible",
                }
            )
    return {
        "org_id": str(org_id),
        "creator_issues": creator_issues,
        "tasks": len(task_ids),
        "mapped": len(mapped),
        "unresolved_task_ids": sorted(str(v) for v in task_ids - mapped),
        "unfinished_jobs": unfinished,
        "unsettled_calls": unsettled,
        "ready": not (task_ids - mapped or unfinished or unsettled),
    }


def run(
    engine,
    org_id: UUID,
    *,
    mapping_path: str | None = None,
    apply=False,
    cutover=False,
    workers_drained=False,
):
    mapping = (
        ReviewedMapping.model_validate_json(Path(mapping_path).read_text())
        if mapping_path
        else None
    )
    if mapping and mapping.org_id != org_id:
        raise ValueError("mapping organization mismatch")
    if apply and not mapping:
        raise ValueError("reviewed mapping is required for import")
    if cutover and not workers_drained:
        raise ValueError(
            "stop old API binaries and workers before cutover; --workers-drained is required"
        )
    with Session(engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org_id)}
        )
        runtime = session.scalar(text("SELECT current_user"))
        if runtime == "bid_app":
            raise ValueError("use the dedicated migration connection")
        org = session.scalar(select(Org).where(Org.id == org_id, Org.active.is_(True)))
        if org is None:
            raise ValueError("active organization required")
        # Lock the full task set in stable order while validating the reviewed map.
        tasks = {
            t.id: t
            for t in session.scalars(
                select(Task).where(Task.org_id == org_id).order_by(Task.id).with_for_update()
            )
        }
        existing = {
            w.task_id
            for w in session.scalars(select(TaskWorkflow).where(TaskWorkflow.org_id == org_id))
        }
        if mapping:
            rows = session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.org_id == org_id)
            ).all()
            people = {m.user_id: (m, u) for m, u in rows}
            reviewer = people.get(mapping.reviewed_by_user_id)
            if (
                reviewer is None
                or not reviewer[0].active
                or not reviewer[1].active
                or reviewer[0].role != "admin"
            ):
                raise ValueError("mapping reviewer must be an active organization administrator")
            from app.services.task_workflow import domains

            for entry in mapping.tasks:
                if entry.task_id not in tasks:
                    raise ValueError("mapping references an inaccessible task")
                if entry.task_id in existing:
                    raise ValueError("mapping cannot replace existing task authorization")
                for uid, role, review in [
                    (entry.owner_user_id, "owner", []),
                    *((m.user_id, m.role, m.review_domains) for m in entry.members),
                ]:
                    person = people.get(uid)
                    if person is None or not person[0].active or not person[1].active:
                        raise ValueError("mapping requires active organization members")
                    if role == "owner" and person[0].role not in {"admin", "bidder"}:
                        raise ValueError("mapped owner is ineligible")
                    if not set(review) <= set(domains(person[0].role)):
                        raise ValueError("mapped review domains exceed current role")
            if apply:
                for entry in mapping.tasks:
                    owner_role = people[entry.owner_user_id][0].role
                    for uid, role, review in [
                        (entry.owner_user_id, "owner", domains(owner_role)),
                        *((m.user_id, m.role, m.review_domains) for m in entry.members),
                    ]:
                        session.add(
                            TaskMember(
                                org_id=org_id,
                                task_id=entry.task_id,
                                user_id=uid,
                                role=role,
                                review_domains=review,
                                changed_by_user_id=mapping.reviewed_by_user_id,
                                changed_at=datetime.now(UTC),
                            )
                        )
                    if not session.scalar(
                        select(TaskEventHead.id).where(TaskEventHead.task_id == entry.task_id)
                    ):
                        session.add(TaskEventHead(org_id=org_id, task_id=entry.task_id))
                    session.flush()
                    session.add(
                        TaskWorkflow(
                            org_id=org_id, task_id=entry.task_id, owner_user_id=entry.owner_user_id
                        )
                    )
                    session.flush()
                    session.add(
                        AuditLog(
                            org_id=org_id,
                            actor_user_id=mapping.reviewed_by_user_id,
                            action="task.workflow_imported",
                            object_id=entry.task_id,
                            details={
                                "actor_kind": "migration",
                                "task_id": str(entry.task_id),
                                "owner_user_id": str(entry.owner_user_id),
                                "revision": 1,
                                "member_user_ids": [str(m.user_id) for m in entry.members],
                                "mapping_sha256": hashlib.sha256(
                                    mapping.model_dump_json().encode()
                                ).hexdigest(),
                            },
                        )
                    )
        result = report(session, org_id)
        result["reviewed_mapping_tasks"] = len(mapping.tasks) if mapping else 0
        result["applied"] = apply
        if cutover and not result["ready"]:
            raise ValueError("cutover blocked by unresolved tasks, active jobs or unsettled calls")
        result["cutover_verified"] = cutover
        return result


def output(value):
    print(json.dumps(value, sort_keys=True))
