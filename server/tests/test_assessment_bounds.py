"""DB-free projection failure cases, written before service implementation.

Reject cross-principal/filter/snapshot replay and malformed cursor anchors;
measure the complete Unicode JSON envelope; never omit a text continuation;
verify a saved quote before returning any source window.
"""

from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.console_assessments import CitationRequest
from app.schemas.contracts import Result
from app.services.assessment_bounds import bounded_result, cursor, read_cursor, text_window
from app.services.auth import Identity
from cryptography.fernet import Fernet


def test_cursor_principal_filters_and_snapshot():
    actor = Identity(uuid4(), uuid4(), {"check:read"}, "viewer")
    settings = Settings(
        database_url="postgresql+psycopg://synthetic@localhost/bid_test_unused",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
    )
    binding = {"parent": str(uuid4()), "part": "findings", "snapshot": "a" * 64}
    value = cursor(settings, actor, binding, {"id": str(uuid4())})
    assert read_cursor(settings, actor, binding, value)["id"]
    for changed in ({**binding, "part": "coverage"},):
        with pytest.raises(ServiceError, match="Cursor"):
            read_cursor(settings, actor, changed, value)
    with pytest.raises(ServiceError) as changed_snapshot:
        read_cursor(settings, actor, {**binding, "snapshot": "b" * 64}, value)
    assert changed_snapshot.value.code == "assessment_view_changed"
    assert changed_snapshot.value.status == 409
    other = Identity(uuid4(), actor.org_id, actor.scopes, actor.role)
    with pytest.raises(ServiceError, match="Cursor"):
        read_cursor(settings, other, binding, value)


def test_whole_envelope_including_multibyte_text():
    assert bounded_result(Result(ok=True, command="check show", data={}))
    with pytest.raises(ServiceError) as caught:
        bounded_result(Result(ok=True, command="check show", data={"text": "原" * 800000}))
    assert caught.value.exit_code == 2
    assert caught.value.code == "assessment_entry_too_large"


def test_windows_do_not_hide_saved_text():
    query = CitationRequest(
        parent_kind="check", parent_id=uuid4(), part="finding", entry_id=uuid4(), limit=3
    )
    window, start, end = text_window("x原文x正文", "原文", query)
    assert (start, end) == (1, 3)
    assert window.text == "x原文"
    assert window.next_offset == 3
    query.offset = 3
    assert text_window("x原文x正文", "原文", query)[0].next_offset is None
    with pytest.raises(ServiceError):
        text_window("x原文", "不存在", query)


def test_page_caps_whole_rows_and_rebuilds_continuation():
    from app.schemas.console_assessments import Notice, PageData
    from app.services.assessment_bounds import fit_items

    parent = uuid4()
    data = PageData(
        task_id=uuid4(),
        parent_id=parent,
        part="notices",
        snapshot="signed",
        total=3,
        filtered_total=3,
        returned=3,
        next_cursor=None,
        validity="current",
    )
    items = [Notice(code="notice", message="原" * 500) for _ in range(3)]
    # Use a small injected cap so this exercises actual envelope encoding,
    # metadata, whole rows, and the exact continuation without huge fixtures.
    shaped, selected = fit_items(
        "check show", data, items, lambda index: f"after-{index}", byte_limit=2400
    )
    assert 0 < len(selected) < 3
    assert shaped.returned == len(selected)
    assert shaped.next_cursor == f"after-{len(selected) - 1}"
    body = Result(
        ok=True,
        command="check show",
        data=shaped.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in selected],
    )
    assert len(body.model_dump_json().encode()) <= 2400
    with pytest.raises(ServiceError) as too_large:
        fit_items("check show", data, items, lambda index: f"after-{index}", byte_limit=800)
    assert too_large.value.code == "assessment_entry_too_large"
    assert too_large.value.status == 422


def test_projection_accounts_for_real_http_spaces_unicode_and_duration():
    import json

    from app.schemas.console_assessments import Notice, PageData
    from app.services.assessment_bounds import fit_items

    data = PageData(
        task_id=uuid4(),
        parent_id=uuid4(),
        part="notices",
        snapshot="signed",
        total=3,
        filtered_total=3,
        returned=3,
        next_cursor=None,
        validity="current",
    )
    items = [Notice(code="notice", message="核对原文" * 60) for _ in range(3)]
    shaped, selected = fit_items(
        "check show", data, items, lambda index: "after-" + str(index), byte_limit=1800
    )
    result = Result(
        ok=True,
        command="check show",
        data=shaped.model_dump(mode="json"),
        items=[item.model_dump(mode="json") for item in selected],
        duration_ms=2**63 - 1,
    )
    # This is the exact middleware JSON encoder, not Pydantic's compact JSON.
    encoded = json.dumps(result.model_dump(mode="json"), ensure_ascii=False).encode()
    assert len(encoded) <= 1800
    assert len(selected) < len(items)
