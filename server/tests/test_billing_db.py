"""Prepaid balance, ledger and recharge card rules enforced by PostgreSQL itself."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import PlatformCard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


@pytest.fixture
async def runtime(tenants, tmp_path):
    database = Database(Settings(data_dir=tmp_path))
    yield database
    await database.engine.dispose()


def add_card(admin_engine, code, *, face=100, currency="USD", status="active", expires=None):
    card_id = uuid4()
    with Session(admin_engine) as session, session.begin():
        session.add(
            PlatformCard(
                id=card_id,
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
                last4=code[-4:],
                face_value=face,
                currency=currency,
                batch_id=uuid4(),
                status=status,
                expires_at=expires,
                created_by="ops@example.test",
            )
        )
    return card_id


async def redeem(runtime, code, org, user, currency="USD"):
    async with runtime.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM redeem_card(:h, :o, :u, :c)"),
                {
                    "h": hashlib.sha256(code.encode()).hexdigest(),
                    "o": org,
                    "u": user,
                    "c": currency,
                },
            )
        ).all()
        leaked = await session.scalar(text("SELECT current_setting('app.current_org', true)"))
        assert leaked == ""
        return rows


async def ledger(runtime, org):
    async with runtime.transaction(org) as session:
        balance = await session.scalar(text("SELECT balance FROM org_balances"))
        total = await session.scalar(text("SELECT coalesce(sum(amount), 0) FROM balance_entries"))
        return balance, total


async def test_concurrent_redemption_credits_once(runtime, admin_engine, tenants):
    add_card(admin_engine, "AAAA-BBBB-CCCC-DDDD", face=100)
    org, user = tenants["orgs"][0], tenants["users"][0]
    results = await asyncio.gather(
        *(redeem(runtime, "AAAA-BBBB-CCCC-DDDD", org, user) for _ in range(10))
    )
    assert sorted(len(rows) for rows in results) == [0] * 9 + [1]
    balance, total = await ledger(runtime, org)
    assert float(balance) == 100 and float(total) == 100


@pytest.mark.parametrize("case", ["used", "void", "expired", "other_currency", "missing"])
async def test_rejected_cards_return_no_row(case, runtime, admin_engine, tenants):
    org, user = tenants["orgs"][0], tenants["users"][0]
    code = "EEEE-FFFF-GGGG-HHHH"
    if case == "used":
        add_card(admin_engine, code)
        assert await redeem(runtime, code, org, user)
    elif case == "void":
        add_card(admin_engine, code, status="void")
    elif case == "expired":
        add_card(admin_engine, code, expires=datetime.now(UTC) - timedelta(seconds=1))
    elif case == "other_currency":
        add_card(admin_engine, code, currency="CNY")
    assert await redeem(runtime, code, org, user) == []


async def test_balance_in_another_currency_blocks_redemption(runtime, admin_engine, tenants):
    org, user = tenants["orgs"][0], tenants["users"][0]
    add_card(admin_engine, "JJJJ-KKKK-MMMM-NNNN", currency="USD")
    add_card(admin_engine, "PPPP-QQQQ-RRRR-SSSS", currency="CNY")
    assert await redeem(runtime, "JJJJ-KKKK-MMMM-NNNN", org, user, "USD")
    assert await redeem(runtime, "PPPP-QQQQ-RRRR-SSSS", org, user, "CNY") == []


async def test_card_credits_only_the_redeeming_org(runtime, admin_engine, tenants):
    add_card(admin_engine, "TTTT-VVVV-WWWW-XXXX", face=40)
    a, b = tenants["orgs"]
    assert await redeem(runtime, "TTTT-VVVV-WWWW-XXXX", a, tenants["users"][0])
    async with runtime.transaction(b) as session:
        assert await session.scalar(text("SELECT count(*) FROM org_balances")) == 0
        assert await session.scalar(text("SELECT count(*) FROM balance_entries")) == 0


async def test_adjust_balance_add_set_and_validation(runtime, tenants):
    org = tenants["orgs"][0]

    async def adjust(mode, amount, reason="Synthetic correction", currency="USD"):
        async with runtime.transaction() as session:
            return (
                await session.execute(
                    text(
                        "SELECT * FROM platform_adjust_balance(:o, :m, CAST(:a AS numeric), :r, 'ops@example.test', :c)"
                    ),
                    {"o": org, "m": mode, "a": amount, "r": reason, "c": currency},
                )
            ).all()

    assert [tuple(map(float, r)) for r in await adjust("add", 30)] == [(30.0, 30.0)]
    assert [tuple(map(float, r)) for r in await adjust("set", 12.5)] == [(-17.5, 12.5)]
    assert [tuple(map(float, r)) for r in await adjust("set", 12.5)] == [(0.0, 12.5)]
    balance, total = await ledger(runtime, org)
    assert float(balance) == float(total) == 12.5
    async with runtime.transaction(org) as session:
        assert await session.scalar(text("SELECT count(*) FROM balance_entries")) == 2
    async with runtime.transaction() as session:
        missing = await session.execute(
            text("SELECT * FROM platform_adjust_balance(:o, 'add', 1, 'x', 'ops', 'USD')"),
            {"o": uuid4()},
        )
        assert missing.all() == []
    for mode, reason, currency in (
        ("add", "  ", "USD"),
        ("double", "x", "USD"),
        ("add", "x", "CNY"),
    ):
        with pytest.raises(DBAPIError):
            await adjust(mode, 1, reason, currency)


async def test_runtime_cannot_force_card_or_ledger_state(runtime, admin_engine, tenants):
    card = add_card(admin_engine, "YYYY-ZZZZ-2222-3333")
    statements = [
        ("UPDATE platform_cards SET status = 'redeemed' WHERE id = :id", None),
        ("UPDATE platform_cards SET redeemed_at = now() WHERE id = :id", None),
    ]
    for statement, org in statements:
        with pytest.raises(DBAPIError):
            async with runtime.transaction(org) as session:
                await session.execute(text(statement), {"id": card})
    async with runtime.transaction() as session:
        await session.execute(
            text("UPDATE platform_cards SET status = 'void' WHERE id = :id"), {"id": card}
        )
    with pytest.raises(DBAPIError):
        async with runtime.transaction() as session:
            await session.execute(
                text("UPDATE platform_cards SET status = 'active' WHERE id = :id"), {"id": card}
            )
    org = tenants["orgs"][0]
    async with runtime.transaction() as session:
        await session.execute(
            text("SELECT * FROM platform_adjust_balance(:o, 'add', 5, 'seed', 'ops', 'USD')"),
            {"o": org},
        )
    for statement in ("UPDATE balance_entries SET amount = 1000", "DELETE FROM balance_entries"):
        with pytest.raises(DBAPIError):
            async with runtime.transaction(org) as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError):
        async with runtime.transaction(org) as session:
            await session.execute(text("UPDATE org_balances SET currency = 'CNY'"))


async def test_memberships_listing_and_token_scope_constraint(runtime, admin_engine, tenants):
    async with runtime.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM user_org_memberships(:u)"), {"u": tenants["users"][0]}
            )
        ).all()
        assert [(r.org_id, r.role, r.org_active) for r in rows] == [
            (tenants["orgs"][0], "admin", True)
        ]
    with pytest.raises(DBAPIError):
        async with runtime.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO api_tokens (id, org_id, user_id, name, digest, encrypted_secret, scopes, expires_at, revoked) "
                    "VALUES (gen_random_uuid(), :o, :u, 'x', :d, 'x', '[\"billing:redeem\"]', now() + interval '1 day', false)"
                ),
                {"o": tenants["orgs"][0], "u": tenants["users"][0], "d": "f" * 64},
            )
