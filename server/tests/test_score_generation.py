"""Rubric generation boundaries and PostgreSQL input-scope acceptance.

The DB-free cases cover the full outbound/redaction/untrusted-output boundary.  The
PostgreSQL cases intentionally use the normal migrated RLS runtime; a DB-free local
run may select only the ``not db`` cases by file/function name.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.providers.calls import current_accounting
from app.providers.rubric import HTTPRubricProvider
from app.schemas.score_contracts import (
    RubricAnsweredBatch,
    RubricCoverageDecisionRequest,
    RubricGenerateRequest,
    RubricItemsWireOutput,
    RubricReviseRequest,
    RubricStructureOutput,
)
from app.services import redaction, score_generation, score_inputs
from app.services.auth import Identity
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_check_combined import semantic_llm
from test_rls import seeded as seeded  # noqa: F401
from test_score_api import RubricVendor
from test_score_provider import Accounting
from test_score_review import replacement

REAL_REQUIREMENT = UUID("00000000-0000-0000-0000-000000000101")
PROVIDER_REQUIREMENT = UUID("00000000-0000-0000-0000-000000000001")


def fixed_secret(*, source_original: str = "联系人：Synthetic Secret。内存 64 GB 得 5 分。"):
    return {
        "requirements": [
            {
                "requirement_id": str(REAL_REQUIREMENT),
                "provider_id": str(PROVIDER_REQUIREMENT),
                "text": "内存 64 GB 得 5 分。",
                "source": {
                    "document_id": "00000000-0000-0000-0000-000000000201",
                    "chunk_id": "00000000-0000-0000-0000-000000000202",
                    "page": 1,
                    "location": None,
                    "quote": "内存 64 GB 得 5 分。",
                },
                "source_original": source_original,
            }
        ]
    }


def candidate(*, quote: str = "内存 64 GB 得 5 分") -> dict:
    return {
        "sections": [
            {
                "key": "technical",
                "title": "技术评分",
                "order": 1,
                "aggregation": "sum",
                "aggregation_rule_text": None,
                "score_range": {"minimum": "0", "maximum": "5"},
                "weight": None,
                "cap": None,
                "included_in_overall_total": True,
                "ambiguity_reason": None,
                "citations": [{"ref": "r1.tender", "quote": quote}],
            }
        ],
        "items": [
            {
                "requirement_id": str(PROVIDER_REQUIREMENT),
                "section_key": "technical",
                "key": "memory",
                "title": "内存",
                "rule_text": "内存 64 GB 得 5 分。",
                "order": 1,
                "assessment_mode": "model_assessable",
                "score_range": {"minimum": "0", "maximum": "5"},
                "weight": None,
                "ambiguity_reason": None,
                "citations": [{"ref": "r1.tender", "quote": quote}],
            }
        ],
        "overall_aggregation": "sum",
        "overall_rule_text": None,
        "overall_score_range": {"minimum": "0", "maximum": "5"},
        "overall_cap": None,
        "overall_citations": [{"ref": "r1.tender", "quote": quote}],
    }


def verified_structure(secret, outbound):
    wire = candidate()
    wire.pop("items")
    return score_generation.accept_structure(
        secret, outbound, RubricStructureOutput.model_validate(wire)
    )


@pytest.mark.parametrize("requirement_count", [1, 3])
async def test_two_stage_generation_keeps_items_for_existing_human_review_contracts(
    tmp_path, requirement_count
):
    """Run the shared API fixture vendor through both stages and review request building.

    Failures: generated keys collide with redaction, batch numbering loses items,
    coverage maps to an empty item list, or replacement items no longer validate.
    """
    import json

    secret = {"requirements": []}
    for index in range(1, requirement_count + 1):
        clause = f"Synthetic criterion {index} earns 5 points. " + "x" * 650
        row = fixed_secret(source_original=clause)["requirements"][0]
        row |= {
            "requirement_id": str(UUID(int=100 + index)),
            "provider_id": str(score_inputs.provider_id(index)),
            "text": clause,
            "source": {**row["source"], "chunk_id": str(UUID(int=200 + index)), "quote": clause},
        }
        secret["requirements"].append(row)
    outbound = score_generation.build_outbound(secret, [], [])
    vendor = RubricVendor()
    provider = HTTPRubricProvider(
        semantic_llm(
            tmp_path,
            vendor,
            llm_batch_chars=1000,
            database_url="postgresql+psycopg://unused/unused",
            encryption_key=Fernet.generate_key().decode(),
            token_key=Fernet.generate_key().decode(),
        )
    )
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        proposed = await provider.extract_structure(score_generation.provider_request(outbound))
        assert proposed.failure is None and proposed.output is not None
        structure = score_generation.accept_structure(secret, outbound, proposed.output)
        generated = await provider.extract_items(
            score_generation.items_request(outbound, structure)
        )
    finally:
        current_accounting.reset(token)
    assert generated.failure is None
    accepted = score_generation.accept_batches(secret, outbound, structure, generated.batches)
    assert accepted["normalization_errors"] == []
    assert len(accepted["items"]) == requirement_count
    assert accepted["unresolved_requirement_ids"] == []
    assert vendor.stages == ["structure", *(["items"] * requirement_count)]
    assert len(accounting.completed) == requirement_count + 1

    sections = [{**section, "id": str(uuid4())} for section in accepted["sections"]]
    by_key = {section["key"]: section["id"] for section in sections}
    report = {
        "rubric": {
            "revision": 1,
            "input_hash": "a" * 64,
            **{
                key: accepted[key]
                for key in (
                    "overall_aggregation",
                    "overall_rule_text",
                    "overall_score_range",
                    "overall_cap",
                )
            },
        },
        "sections": sections,
        "items": [
            {**item, "id": str(uuid4()), "section_id": by_key[item["section_key"]]}
            for item in accepted["items"]
        ],
        "coverage": [
            {"requirement_id": row["requirement_id"], "source": row["source"]}
            for row in secret["requirements"]
        ],
    }
    revision = RubricReviseRequest.model_validate(replacement(report))
    decisions = [
        RubricCoverageDecisionRequest(
            expected_revision=1,
            expected_input_hash=report["rubric"]["input_hash"],
            action="mapped",
            rubric_item_ids=[
                UUID(item["id"])
                for item in report["items"]
                if item["requirement_id"] == row["requirement_id"]
            ],
            reason="Synthetic human coverage review",
        )
        for row in report["coverage"]
    ]
    artifact = tmp_path / "two-stage-review-contracts.json"
    artifact.write_text(
        json.dumps(
            {
                "stages": vendor.stages,
                "accepted": accepted,
                "revision": revision.model_dump(mode="json"),
                "coverage_decisions": [decision.model_dump(mode="json") for decision in decisions],
            },
            indent=2,
        )
    )
    assert len(json.loads(artifact.read_text())["revision"]["items"]) == requirement_count


def test_outbound_redaction_and_dual_citation_acceptance_use_local_ids_only():
    library = [redaction.library_value("contact", "contact", "Synthetic Secret")]
    outbound = score_generation.build_outbound(
        fixed_secret(),
        [{"placeholder": "{{secret.contact}}", "label": "联系人", "kind": "contact"}],
        [entry for entry in library if entry is not None],
    )
    assert outbound["context"]["texts"][0]["ref"] == "r1.tender"
    sent = outbound["context"]["texts"][0]["text"]
    assert "来源位置：" in sent and "招标原文：\n内存 64 GB 得 5 分。" in sent
    assert "Synthetic Secret" not in sent
    assert outbound["requirements"] == {str(PROVIDER_REQUIREMENT): str(REAL_REQUIREMENT)}
    assert "condition" not in str(outbound)

    secret = fixed_secret()
    structure = verified_structure(secret, outbound)
    batch = RubricAnsweredBatch(
        requested_requirement_ids=[PROVIDER_REQUIREMENT],
        sent_refs=["r1.tender"],
        structure_hash=structure["structure_hash"],
        output=RubricItemsWireOutput(items=candidate()["items"]),
    )
    accepted = score_generation.accept_batches(secret, outbound, structure, [batch])
    assert accepted["normalization_errors"] == []
    assert accepted["unresolved_requirement_ids"] == []
    assert accepted["sections"][0]["requirement_id"] == str(REAL_REQUIREMENT)
    assert accepted["items"][0]["requirement_id"] == str(REAL_REQUIREMENT)
    assert accepted["items"][0]["source"] == fixed_secret()["requirements"][0]["source"]
    assert str(PROVIDER_REQUIREMENT) not in str(accepted)
    assert "Synthetic Secret" not in str(accepted)


def test_invalid_or_ambiguous_structure_citation_fails_before_items():
    secret = fixed_secret(source_original="内存 64 GB 得 5 分。重复说明：内存 64 GB 得 5 分。")
    outbound = score_generation.build_outbound(secret, [], [])
    with pytest.raises(ServiceError) as failure:
        verified_structure(secret, outbound)
    assert failure.value.code == "invalid_overall_citation"


@pytest.mark.parametrize(
    "attack,expected_error",
    [
        ("unknown_id", "invalid_item_citation"),
        ("unknown_ref", "invalid_item_citation"),
        ("stitched_quote", "invalid_item_citation"),
        ("placeholder_quote", "invalid_item_citation"),
        ("unknown_placeholder", "sensitive_model_output"),
        ("sensitive_key", "sensitive_model_output"),
        ("missing_candidate", "missing_requirement_output"),
        ("duplicate_candidate", "duplicate_item_key"),
    ],
)
def test_untrusted_candidate_attacks_remain_unresolved(attack, expected_error):
    secret = fixed_secret()
    outbound = score_generation.build_outbound(secret, [], [])
    wire = candidate()
    item = wire["items"][0]
    if attack == "unknown_id":
        item["requirement_id"] = str(uuid4())
    elif attack == "unknown_ref":
        item["citations"][0]["ref"] = "untrusted-sensitive-ref"
    elif attack == "stitched_quote":
        item["citations"][0]["quote"] = "内存 64 GB 5 分"
    elif attack == "placeholder_quote":
        item["citations"][0]["quote"] = "{{secret.contact}}"
    elif attack == "unknown_placeholder":
        item["rule_text"] = "Use {{secret.unknown}} for this rule."
    elif attack == "sensitive_key":
        item["key"] = "联系人：13800138000"
    elif attack == "missing_candidate":
        wire["items"] = []
    elif attack == "duplicate_candidate":
        wire["items"] = [item, deepcopy(item)]
    structure = verified_structure(secret, outbound)
    batch = RubricAnsweredBatch(
        requested_requirement_ids=[PROVIDER_REQUIREMENT],
        sent_refs=["r1.tender"],
        structure_hash=structure["structure_hash"],
        output=RubricItemsWireOutput(items=wire["items"]),
    )

    accepted = score_generation.accept_batches(secret, outbound, structure, [batch])

    assert expected_error in accepted["normalization_errors"]
    assert accepted["unresolved_requirement_ids"] == [str(REAL_REQUIREMENT)]
    assert "untrusted-sensitive-ref" not in str(accepted)


def test_cross_requirement_ref_cannot_bind_a_model_uuid_to_another_source():
    second_real = UUID("00000000-0000-0000-0000-000000000102")
    second_provider = UUID("00000000-0000-0000-0000-000000000002")
    secret = fixed_secret()
    second = deepcopy(secret["requirements"][0])
    second |= {
        "requirement_id": str(second_real),
        "provider_id": str(second_provider),
        "text": "处理器性能得 5 分。",
        "source": {
            **second["source"],
            "chunk_id": "00000000-0000-0000-0000-000000000203",
            "quote": "处理器性能得 5 分。",
        },
        "source_original": "处理器性能得 5 分。",
    }
    secret["requirements"].append(second)
    outbound = score_generation.build_outbound(secret, [], [])
    wire = candidate()
    wire["items"][0]["citations"] = [{"ref": "r2.tender", "quote": "处理器性能得 5 分。"}]
    structure = verified_structure(secret, outbound)
    batch = RubricAnsweredBatch(
        requested_requirement_ids=[PROVIDER_REQUIREMENT, second_provider],
        sent_refs=["r1.tender", "r2.tender"],
        structure_hash=structure["structure_hash"],
        output=RubricItemsWireOutput(items=wire["items"]),
    )

    accepted = score_generation.accept_batches(secret, outbound, structure, [batch])

    assert "invalid_item_citation" in accepted["normalization_errors"]
    assert set(accepted["unresolved_requirement_ids"]) == {
        str(REAL_REQUIREMENT),
        str(second_real),
    }


def test_no_completed_item_batch_retains_verified_structure_and_unresolved_requirements():
    secret = fixed_secret()
    outbound = score_generation.build_outbound(secret, [], [])
    structure = verified_structure(secret, outbound)
    accepted = score_generation.accept_batches(secret, outbound, structure, [])
    assert accepted["sections"] == structure["sections"]
    assert accepted["items"] == []
    assert accepted["unresolved_requirement_ids"] == [str(REAL_REQUIREMENT)]


async def test_redacted_source_location_is_a_zero_call_admission_blocker():
    secret = fixed_secret()
    secret["requirements"][0]["source"] |= {
        "page": None,
        "location": {
            "block_id": "p1",
            "kind": "paragraph",
            "section_path": ["联系人：Synthetic Secret"],
            "paragraph": 1,
            "table": None,
            "row": None,
            "column": None,
            "label": "联系人：Synthetic Secret",
        },
    }
    library = [redaction.library_value("contact", "contact", "Synthetic Secret")]
    outbound = score_generation.build_outbound(
        secret,
        [{"placeholder": "{{secret.contact}}", "label": "联系人", "kind": "contact"}],
        [entry for entry in library if entry is not None],
    )
    assert outbound["redacted_requirements"] == [str(REAL_REQUIREMENT)]
    fixed = SimpleNamespace(
        manifest={"model_redaction_enabled": True},
        secret={"outbound": outbound},
    )
    body = RubricGenerateRequest(
        extraction_job_id=uuid4(),
        dry_run=True,
    )

    preview = await score_generation.preview_cost(None, fixed, object(), body, SimpleNamespace())

    assert preview["admission_blocker"] == "sensitive_scoring_source"
    assert preview["estimated_cost"]["llm_tokens"] == 0


async def test_postgres_snapshot_selects_only_scoring_and_omits_condition(seeded):
    """Real RLS/query gate: fixed scoring rows only, with no free condition payload."""

    org = seeded["orgs"][0]
    user = seeded["users"][0]
    ids = seeded["ids"][org]
    db = Database(Settings())
    try:
        async with db.transaction(org) as session:
            from app.models.entities import Requirement

            requirement = await session.scalar(
                select(Requirement).where(Requirement.task_id == ids["task"])
            )
            requirement.category = "scoring"
            requirement.condition = {"forbidden": "must never leave"}
            extraction_id = requirement.job_id
        actor = Identity(
            user,
            org,
            {"task:read", "score:rubric:generate"},
            "technical",
        )
        async with db.transaction(org) as session:
            fixed = await score_inputs.snapshot(session, actor, ids["task"], extraction_id)
        assert len(fixed.secret["requirements"]) == 1
        assert "condition" not in fixed.secret["requirements"][0]
        assert fixed.manifest["scope"] == "scoring_requirements"
        assert fixed.manifest["requirements"][0]["requirement_id"] == str(requirement.id)
    finally:
        await db.engine.dispose()


async def test_postgres_snapshot_hides_another_tenants_task(seeded):
    org_a, org_b = seeded["orgs"]
    user_a = seeded["users"][0]
    ids_b = seeded["ids"][org_b]
    db = Database(Settings())
    try:
        actor = Identity(
            user_a,
            org_a,
            {"task:read", "score:rubric:generate"},
            "technical",
        )
        with pytest.raises(ServiceError) as hidden:
            async with db.transaction(org_a) as session:
                await score_inputs.snapshot(
                    session,
                    actor,
                    ids_b["task"],
                    uuid4(),
                )
        assert hidden.value.code == "not_found"
    finally:
        await db.engine.dispose()
