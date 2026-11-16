"""Los fixtures compartidos son la frontera API→worker: la API (Go) comprueba que los produce
(api/internal/queue/redis_test.go) y aquí se comprueba que cumplen el schema y que el worker los acepta."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tennis_worker.contracts import JobMessage
from tests.conftest import CONTRACTS_DIR

FIXTURES = CONTRACTS_DIR / "fixtures"


def schema() -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / "job_message.schema.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.mark.parametrize(("fixture", "stroke"), [("job_message.example.json", "forehand"),
                                                  ("job_message.auto.example.json", None)])
def test_job_message_fixtures_match_schema_and_model(fixture: str, stroke: str | None) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    raw = (FIXTURES / fixture).read_text(encoding="utf-8")
    jsonschema.validate(json.loads(raw), schema())
    assert JobMessage.model_validate_json(raw).stroke_type == stroke


def test_legacy_1_0_messages_still_require_stroke_type() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    legacy = json.loads((FIXTURES / "job_message.example.json").read_text(encoding="utf-8")) | {"schema_version": "1.0"}
    jsonschema.validate(legacy, schema())
    assert JobMessage.model_validate(legacy).stroke_type == "forehand"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({k: v for k, v in legacy.items() if k != "stroke_type"}, schema())
