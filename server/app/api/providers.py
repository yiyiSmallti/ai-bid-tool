"""Provider configuration API, mounted on the shared installation authentication context."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.models.entities import Job, UsageRecord
from app.schemas.contracts import ProviderUsage, Result
from app.schemas.provider_contracts import ProviderConfigSet, ProviderTest


def create_router(context, db, settings, llm, resolve, processor, transport=None):
    router = APIRouter()

    @router.get("/providers", name="provider_list", response_model=Result)
    async def provider_list(history: bool = False, ctx=Depends(context, scope="function")):
        from app.services import provider_configs as service

        session, actor = ctx
        data, items, active = await service.list_configs(session, actor, settings, history=history)
        return Result(
            ok=True,
            command="provider history" if history else "provider list",
            data=data,
            items=items,
        )

    @router.post("/providers", name="provider_set", response_model=Result)
    async def provider_set(body: ProviderConfigSet, ctx=Depends(context, scope="function")):
        from app.services import provider_configs as service

        return Result(
            ok=True,
            command="provider set",
            data=await service.set_config(ctx[0], ctx[1], body, settings),
        )

    @router.post("/providers/models/{slot}/default", name="provider_default", response_model=Result)
    async def provider_default(slot: UUID, ctx=Depends(context, scope="function")):
        from app.services import provider_configs as service

        return Result(
            ok=True,
            command="provider default",
            data=await service.set_default(ctx[0], ctx[1], slot),
        )

    @router.delete("/providers/models/{slot}", name="provider_remove", response_model=Result)
    async def provider_remove(slot: UUID, ctx=Depends(context, scope="function")):
        from app.services import provider_configs as service

        return Result(
            ok=True,
            command="provider remove",
            data=await service.remove_model(ctx[0], ctx[1], slot),
        )

    @router.post("/providers/test", name="provider_test", response_model=Result)
    async def provider_test(body: ProviderTest, ctx=Depends(context, scope="function")):
        from app.services import provider_configs as service

        session, actor = ctx
        await service.require_access(session, actor, write=True)
        provider = await resolve(session, model=body.model) if resolve else llm
        if body.dry_run:
            data, warnings = await service.preview_test(session, actor, body, provider, settings)
            return Result(
                ok=True,
                command="provider test",
                data=data,
                warnings=warnings,
                cost=data["preflight"]["estimate"],
            )
        job = await service.submit_test(session, actor, body, provider, settings)
        await session.commit()
        # The response waits for the same processor used by queued extraction/drafting.
        # No transaction/connection is held while the vendor request is in flight.
        await processor(str(job.id))
        async with db.transaction() as read:
            saved = await read.get(Job, job.id)
            assert saved is not None
            recorded = await read.scalar(select(UsageRecord).where(UsageRecord.job_id == job.id))
            data = {
                "job_id": str(saved.id),
                "status": saved.status,
                "provider_config_id": str(saved.provider_config_id)
                if saved.provider_config_id
                else None,
                "reasoning": saved.reasoning,
                "usage": ProviderUsage.model_validate(recorded).model_dump(mode="json")
                if recorded is not None
                else None,
                "month_usage": await service.monthly_usage(read, saved.provider_config_id),
                **({"error": saved.error} if saved.error else {}),
            }
            data["result"] = {
                key: value for key, value in saved.result.items() if key != "submission"
            }
            cost, warnings = saved.result.get("cost", {}), saved.result.get("warnings", [])
        return Result(
            ok=saved.status == "succeeded",
            command="provider test",
            data=data,
            cost=cost,
            warnings=warnings,
        )

    return router
