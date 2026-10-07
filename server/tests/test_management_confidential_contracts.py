"""DB-free boundary checks declared before implementation.

Failures: omitted null CAS precondition is accepted; generic serialization or
repr exposes plaintext; history permits unrelated search filters; pages are
unbounded or blank queries silently become broad searches.
"""

from uuid import UUID

import pytest
from app.schemas.management_pages import (
    ConfidentialHistoryQuery,
    ConfidentialQuery,
    ConfidentialValueRevisionSet,
)
from pydantic import ValidationError

ID = UUID("00000000-0000-4000-8000-000000000001")
SECRET = "synthetic-secret-only"


def test_checked_value_requires_explicit_absence_and_hides_generic_serialization():
    with pytest.raises(ValidationError):
        ConfidentialValueRevisionSet(value=SECRET, expected_field_revision=1)
    for expected in (None, ID):
        command = ConfidentialValueRevisionSet(
            value=SECRET, task_id=ID, expected_field_revision=1, expected_value_id=expected
        )
        assert command.value == SECRET
        assert command.expected_value_id == expected
        assert "value" not in command.model_dump()
        assert SECRET not in command.model_dump_json()
        assert SECRET not in repr(command)
    for revision in (0, -1, True, "1"):
        with pytest.raises(ValidationError):
            ConfidentialValueRevisionSet(
                value=SECRET, expected_field_revision=revision, expected_value_id=None
            )


def test_confidential_queries_are_bounded_and_metadata_only():
    assert ConfidentialQuery().limit == 25
    assert ConfidentialQuery().archived is False
    assert ConfidentialQuery(q="  投标  ").q == "投标"
    assert ConfidentialQuery().field_id is None
    assert ConfidentialQuery(field_id=ID).field_id == ID
    with pytest.raises(ValidationError):
        ConfidentialQuery(field_id="invalid")
    for model in (ConfidentialQuery, ConfidentialHistoryQuery):
        assert model(limit=100, task_id=ID).task_id == ID
        for payload in (
            {"limit": 101},
            {"limit": 0},
            {"limit": True},
            {"cursor": ""},
            {"cursor": "x" * 2049},
            {"value": SECRET},
        ):
            with pytest.raises(ValidationError):
                model.model_validate(payload)
    for payload in ({"q": " "}, {"q": "x" * 201}):
        with pytest.raises(ValidationError):
            ConfidentialQuery.model_validate(payload)
    for payload in ({"q": "name"}, {"archived": True}, {"field_id": ID}):
        with pytest.raises(ValidationError):
            ConfidentialHistoryQuery.model_validate(payload)
