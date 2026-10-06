"""Two-stage fake-HTTP acceptance for independently pinned section citations.

Failure modes: a valid second Requirement is rejected or dropped; one bad citation
is hidden by a good one; joined text crosses pinned Sources; item citations escape
their single Requirement; stage 2 changes the fixed section; incompatible preview
identity is reused. Artifacts contain only synthetic clauses and accepted bindings.
"""

import json
from copy import deepcopy
from uuid import UUID

import pytest
from app.core.errors import ServiceError
from app.providers.calls import current_accounting
from app.providers.rubric import HTTPRubricProvider
from app.schemas.score_contracts import RubricStructureOutput
from app.services import score_generation
from cryptography.fernet import Fernet
from test_check_combined import semantic_llm
from test_score_api import RubricVendor
from test_score_generation import fixed_secret
from test_score_provider import Accounting


def multi_source_secret():
    secret = fixed_secret(source_original="合成基准价由有效报价计算。")
    first = secret["requirements"][0]
    first["text"] = first["source"]["quote"] = first["source_original"]
    second = deepcopy(first)
    second.update(
        requirement_id=str(UUID(int=102)),
        provider_id=str(UUID(int=2)),
        text="合成价格分按基准价与报价比值计算。",
        source_original="合成价格分按基准价与报价比值计算。",
    )
    second["source"].update(chunk_id=str(UUID(int=203)), page=2, quote=second["source_original"])
    secret["requirements"].append(second)
    return secret


class MultiSourceVendor(RubricVendor):
    def __init__(self, attack=None):
        super().__init__()
        self.attack = attack

    async def __call__(self, request):
        response = await super().__call__(request)
        body = response.json()
        output = json.loads(body["choices"][0]["message"]["content"])
        payload = self.requests[-1]
        refs = [
            {"ref": row["ref"], "quote": row["text"].split("\n招标原文：\n", 1)[-1]}
            for row in payload["context"]["texts"]
        ]
        if "structure_hash" not in payload:
            output["sections"][0]["citations"] = deepcopy(refs)
            if self.attack == "bad_second":
                output["sections"][0]["citations"][1]["quote"] = "不在原文中的合成引文"
            elif self.attack == "joined":
                output["sections"][0]["citations"] = [
                    {"ref": refs[0]["ref"], "quote": refs[0]["quote"] + refs[1]["quote"]}
                ]
            elif self.attack == "unknown_second":
                output["sections"][0]["citations"][1]["ref"] = "unknown.tender"
        elif self.attack == "cross_item":
            output["items"][0]["citations"] = deepcopy(refs)
        body["choices"][0]["message"]["content"] = json.dumps(output)
        import httpx

        return httpx.Response(200, json=body)


@pytest.mark.parametrize("attack", [None, "bad_second", "joined", "unknown_second", "cross_item"])
async def test_two_stage_section_citations_bind_each_pinned_requirement(tmp_path, attack):
    secret = multi_source_secret()
    outbound = score_generation.build_outbound(secret, [], [])
    vendor = MultiSourceVendor(attack)
    provider = HTTPRubricProvider(
        semantic_llm(
            tmp_path,
            vendor,
            database_url="postgresql+psycopg://unused/unused",
            encryption_key=Fernet.generate_key().decode(),
            token_key=Fernet.generate_key().decode(),
        )
    )
    accounting = Accounting()
    token = current_accounting.set(accounting)
    try:
        result = await provider.extract_structure(score_generation.provider_request(outbound))
        assert result.failure is None and result.output is not None
        if attack in {"bad_second", "joined", "unknown_second"}:
            with pytest.raises(ServiceError) as error:
                score_generation.accept_structure(secret, outbound, result.output)
            assert error.value.code == "invalid_section_citation"
            assert vendor.stages == ["structure"]
            (tmp_path / "section-citation-rejected.json").write_text(
                json.dumps({"attack": attack, "error": error.value.code, "stages": vendor.stages})
            )
            return
        structure = score_generation.accept_structure(secret, outbound, result.output)
        generated = await provider.extract_items(
            score_generation.items_request(outbound, structure)
        )
    finally:
        current_accounting.reset(token)
    assert generated.failure is None
    accepted = score_generation.accept_batches(secret, outbound, structure, generated.batches)
    expected = [
        {
            "requirement_id": row["requirement_id"],
            "source": row["source"],
            "quote": row["source"]["quote"],
        }
        for row in secret["requirements"]
    ]
    assert accepted["sections"][0]["sources"] == expected
    assert accepted["sections"] == structure["sections"]
    assert vendor.stages == ["structure", "items"]
    assert len(accounting.completed) == 2
    assert vendor.requests[1]["sections"][0]["key"] == structure["sections"][0]["key"]
    if attack == "cross_item":
        assert "invalid_item_citation" in accepted["normalization_errors"]
        assert len(accepted["items"]) == 1
    else:
        assert accepted["normalization_errors"] == []
        assert accepted["unresolved_requirement_ids"] == []
        assert len(accepted["items"]) == 2
    artifact = tmp_path / "section-sources-two-stage.json"
    artifact.write_text(json.dumps({"stages": vendor.stages, "accepted": accepted}, indent=2))
    assert json.loads(artifact.read_text())["accepted"]["sections"][0]["sources"] == expected


def test_section_preserves_separate_quotes_from_the_same_requirement():
    secret = fixed_secret(source_original="内存 64 GB 得 5 分。提供证明。")
    secret["requirements"][0]["source"]["quote"] = "内存 64 GB 得 5 分。提供证明。"
    from test_score_generation import candidate

    wire = candidate()
    wire.pop("items")
    wire["sections"][0]["citations"].append({"ref": "r1.tender", "quote": "提供证明。"})
    outbound = score_generation.build_outbound(secret, [], [])
    structure = score_generation.accept_structure(
        secret, outbound, RubricStructureOutput.model_validate(wire)
    )
    assert [row["quote"] for row in structure["sections"][0]["sources"]] == [
        "内存 64 GB 得 5 分",
        "提供证明。",
    ]
