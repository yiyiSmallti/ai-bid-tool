"""Provider configuration API, mounted on the shared tenant authentication context."""

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.models.entities import Job, UsageRecord
from app.models.provider_configs import ProviderConfig
from app.schemas.contracts import ProviderUsage, Result
from app.schemas.provider_contracts import ProviderConfigSet, ProviderTest
from app.services import provider_configs as service


def create_router(context, db, settings, llm, resolve, processor, transport=None):
    router = APIRouter()

    @router.get("/providers", name="provider_list", response_model=Result)
    async def provider_list(history: bool = False, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data, items, active = await service.list_configs(session, actor, settings, history=history)
        # Release the read transaction before a vendor's optional balance endpoint.
        await session.commit()
        if items and not history:
            items[0]["balance"] = await service.balance_view(active, settings, transport)
        return Result(
            ok=True,
            command="provider history" if history else "provider list",
            data=data,
            items=items,
        )

    @router.post("/providers", name="provider_set", response_model=Result)
    async def provider_set(body: ProviderConfigSet, ctx=Depends(context, scope="function")):
        return Result(
            ok=True,
            command="provider set",
            data=await service.set_config(ctx[0], ctx[1], body, settings),
        )

    @router.post("/providers/test", name="provider_test", response_model=Result)
    async def provider_test(body: ProviderTest, ctx=Depends(context, scope="function")):
        session, actor = ctx
        await service.require_access(session, actor, write=True)
        provider = await resolve(session) if resolve else llm
        job = await service.submit_test(session, actor, body, provider, settings)
        await session.commit()
        # The response waits for the same processor used by queued extraction/drafting.
        # No transaction/connection is held while the vendor request is in flight.
        await processor(str(actor.org_id), str(job.id))
        async with db.transaction(actor.org_id) as read:
            saved = await read.get(Job, job.id)
            assert saved is not None
            config = (
                await read.get(ProviderConfig, saved.provider_config_id)
                if saved.provider_config_id
                else None
            )
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
            cost, warnings = saved.result.get("cost", {}), saved.result.get("warnings", [])
        data["balance"] = await service.balance_view(config, settings, transport)
        return Result(
            ok=saved.status == "succeeded",
            command="provider test",
            data=data,
            cost=cost,
            warnings=warnings,
        )

    return router
