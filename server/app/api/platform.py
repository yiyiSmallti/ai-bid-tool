"""Platform operator routes; no org context and no access to org business data."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.platform_clef import create_router as create_clef_router
from app.api.platform_credentials import create_router as create_credentials_router
from app.api.platform_trust_anchors import create_router as create_trust_anchors_router
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.schemas.budget_contracts import BudgetPlatformModelTest
from app.schemas.contracts import Result
from app.schemas.operator_enrollment import (
    EnrollmentComplete,
    EnrollmentLinkRequest,
    EnrollmentStartRequest,
)
from app.schemas.org_signup import (
    ApplicationStatus,
    OrgApplicationApprove,
    OrgApplicationListQuery,
    OrgApplicationReject,
    OrgApplicationSubmit,
)
from app.schemas.platform_contracts import (
    OrgLookup,
    PasswordSetup,
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformLogin,
    PlatformModelSet,
    PlatformOrgActive,
    PlatformOrgCreate,
)
from app.schemas.platform_credentials import CatalogResolveTarget
from app.services import platform
from app.services.operator_enrollment import OperatorEnrollmentService
from app.services.org_signup import OrgSignupService
from app.services.platform_credentials import PlatformCredentialResolver, database_error


def result(command: str, data=None, items=None) -> dict:
    return Result(ok=True, command=command, data=data or {}, items=items or []).model_dump(
        mode="json"
    )


def create_router(
    settings: Settings,
    db: Database,
    crypto: TokenSigner,
    attempts: PasswordAttempts,
    transport=None,
    processor=None,
) -> APIRouter:
    router = APIRouter()
    bearer = HTTPBearer(auto_error=False)
    signup = OrgSignupService(settings, db, attempts)
    enrollment = OperatorEnrollmentService(db, settings, crypto, attempts)

    async def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            raise ServiceError("invalid_session", "Bearer credentials required", 401, 4)
        return platform.identify(settings, crypto, credentials.credentials)

    @router.get("/platform/operators", name="platform_operator_list", response_model=Result)
    async def operator_list(actor=Depends(operator)):
        rows = await enrollment.list_operators(actor.email)
        return result("platform operator list", items=[row.model_dump(mode="json") for row in rows])

    @router.post(
        "/platform/operators/{email}/enrollment-links",
        name="platform_operator_enrollment-link",
        response_model=Result,
    )
    async def operator_enrollment_link(email: str, actor=Depends(operator)):
        try:
            target = EnrollmentLinkRequest(email=email)
        except ValidationError:
            raise ServiceError("invalid_input", "Invalid email address", 422, 2) from None
        link = await enrollment.issue_link(actor.email, target)
        return result("platform operator enrollment-link", link.model_dump(mode="json"))

    @router.post(
        "/platform/enrollment/start", name="platform_enrollment_start", response_model=Result
    )
    async def enrollment_start(body: EnrollmentStartRequest):
        started = await enrollment.start(body)
        return result("platform enrollment start", started.model_dump(mode="json"))

    @router.post(
        "/platform/enrollment/complete", name="platform_enrollment_complete", response_model=Result
    )
    async def enrollment_complete(body: EnrollmentComplete, request: Request):
        completed = await enrollment.complete(body, request.client.host if request.client else None)
        return result("platform enrollment complete", completed.model_dump(mode="json"))

    @router.post(
        "/auth/org-applications", name="auth_org-application_submit", response_model=Result
    )
    async def application_submit(body: OrgApplicationSubmit, request: Request):
        receipt = await signup.submit(body, request.client.host if request.client else None)
        return result("auth org-application submit", receipt.model_dump(mode="json"))

    @router.get(
        "/platform/org-applications", name="platform_org_application_list", response_model=Result
    )
    async def application_list(
        status: ApplicationStatus | None = "pending",
        limit: int = Query(default=50, ge=1, le=200),
        before: AwareDatetime | None = None,
        actor=Depends(operator),
    ):
        query = OrgApplicationListQuery(status=status, limit=limit, before=before)
        rows = await signup.list_applications(actor.email, query)
        return result(
            "platform org application list", items=[row.model_dump(mode="json") for row in rows]
        )

    @router.post(
        "/platform/org-applications/{application_id}/approve",
        name="platform_org_application_approve",
        response_model=Result,
    )
    async def application_approve(
        application_id: UUID, body: OrgApplicationApprove, actor=Depends(operator)
    ):
        decision = await signup.approve(actor.email, application_id, body)
        return result("platform org application approve", decision.model_dump(mode="json"))

    @router.post(
        "/platform/org-applications/{application_id}/reject",
        name="platform_org_application_reject",
        response_model=Result,
    )
    async def application_reject(
        application_id: UUID, body: OrgApplicationReject, actor=Depends(operator)
    ):
        decision = await signup.reject(actor.email, application_id, body)
        return result("platform org application reject", decision.model_dump(mode="json"))

    @router.post("/platform/auth/login", name="platform_login", response_model=Result)
    async def platform_login(body: PlatformLogin, request: Request):
        data = await platform.login(
            attempts,
            settings,
            crypto,
            body.email,
            body.password,
            body.totp,
            request.client.host if request.client else None,
        )
        return result("platform login", data)

    @router.post("/auth/setup-password", name="auth_setup_password", response_model=Result)
    async def setup_password(body: PasswordSetup):
        await platform.setup_password(db, crypto, body.token, body.password)
        return result("auth setup-password", {"password_set": True})

    @router.post("/auth/orgs", name="auth_orgs", response_model=Result)
    async def auth_orgs(body: OrgLookup, request: Request):
        return result(
            "auth orgs",
            items=await platform.user_orgs(
                attempts, body.email, body.password, request.client.host if request.client else None
            ),
        )

    @router.get("/platform/orgs", name="platform_org_list", response_model=Result)
    async def org_list(actor=Depends(operator)):
        pending = await signup.list_applications(actor.email, OrgApplicationListQuery(limit=200))
        async with db.transaction() as session:
            return result(
                "platform org list",
                {"pending_applications": len(pending)},
                items=await platform.list_orgs(session),
            )

    @router.post("/platform/orgs", name="platform_org_create", response_model=Result)
    async def org_create(body: PlatformOrgCreate, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.create_org(session, crypto, actor, body.name, body.admin_email)
        return result("platform org create", data)

    @router.post(
        "/platform/orgs/{org_id}/active", name="platform_org_set_active", response_model=Result
    )
    async def org_set_active(org_id: UUID, body: PlatformOrgActive, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.set_org_active(session, actor, org_id, body.active)
        return result("platform org set-active", data)

    @router.get("/platform/models", name="platform_model_list", response_model=Result)
    async def model_list(actor=Depends(operator)):
        async with db.transaction() as session:
            return result(
                "platform model list",
                {"currency": settings.billing_currency},
                await platform.list_models(session, PlatformCredentialResolver(settings)),
            )

    @router.post("/platform/models", name="platform_model_set", response_model=Result)
    async def model_set(body: PlatformModelSet, actor=Depends(operator)):
        try:
            async with db.transaction() as session:
                data = await platform.set_model(session, actor, body)
        except SQLAlchemyError as exc:
            raise database_error(exc) from None
        state = await PlatformCredentialResolver(settings).readiness(
            CatalogResolveTarget(model_id=data["id"], expected_model_revision=data["revision"])
        )
        data["credential_configured"] = state.configured
        return result("platform model set", data)

    @router.post(
        "/platform/models/{model_id}/test", name="platform_model_test", response_model=Result
    )
    async def model_test(model_id: str, body: BudgetPlatformModelTest, actor=Depends(operator)):
        data = await platform.test_model(
            db, settings, actor, model_id, transport, body=body, processor=processor
        )
        return Result(
            ok=data.get("passed", True),
            command="platform model test",
            data=data,
            cost=data.get("budget_preflight", {}).get("estimate", data.get("cost", {})),
        )

    @router.post(
        "/platform/orgs/{org_id}/balance", name="platform_org_balance", response_model=Result
    )
    async def org_balance(org_id: UUID, body: PlatformBalanceAdjust, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.adjust_balance(
                session, actor, org_id, body, settings.billing_currency
            )
        return result("platform org balance", data)

    @router.post("/platform/cards", name="platform_card_create", response_model=Result)
    async def card_create(body: PlatformCardCreate, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.create_cards(session, actor, body, settings.billing_currency)
        return result("platform card create", data)

    @router.get("/platform/cards", name="platform_card_list", response_model=Result)
    async def card_list(
        batch_id: UUID | None = None,
        status: Literal["active", "redeemed", "void"] | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        actor=Depends(operator),
    ):
        async with db.transaction() as session:
            items = await platform.list_cards(session, batch_id, status, limit)
        return result("platform card list", {"currency": settings.billing_currency}, items)

    @router.post("/platform/cards/{card_id}/void", name="platform_card_void", response_model=Result)
    async def card_void(card_id: UUID, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.void_card(session, actor, card_id)
        return result("platform card void", data)

    @router.get("/platform/usage", name="platform_usage", response_model=Result)
    async def usage(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        actor=Depends(operator),
    ):
        async with db.transaction() as session:
            data, items = await platform.usage(session, start, end)
        return result("platform usage", {**data, "currency": settings.billing_currency}, items)

    @router.get("/platform/audit", name="platform_audit", response_model=Result)
    async def audit(limit: int = Query(default=100, ge=1, le=500), actor=Depends(operator)):
        async with db.transaction() as session:
            return result("platform audit", items=await platform.audit_entries(session, limit))

    router.include_router(create_clef_router(settings, operator))
    router.include_router(create_credentials_router(settings, operator))
    router.include_router(create_trust_anchors_router(db, operator))
    return router
