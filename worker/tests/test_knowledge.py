from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from tennis_worker.contracts import MetricValue, StrokeType
from tennis_worker.errors import UnsupportedStrokeError
from tennis_worker.pipeline.biomechanics.registry import default_registry
from tennis_worker.pipeline.knowledge.engine import RuleEngine
from tennis_worker.pipeline.knowledge.models import Condition, KnowledgeBase
from tennis_worker.pipeline.knowledge.repository import YamlKnowledgeBaseRepository
from tests.conftest import KNOWLEDGE_DIR


def metric(id_: str, value: float, confidence: float = 0.9) -> MetricValue:
    return MetricValue(id=id_, label=id_, value=value, unit="u", confidence=confidence)


def rule(id_: str, when: dict[str, Any], category: str = "fundamental", impact: int = 3,
         difficulty: int = 2) -> dict[str, Any]:
    return {"id": id_, "title": id_, "category": category, "impact": impact, "difficulty": difficulty,
            "when": when, "explanation": "e", "correction": "c"}


def kb(*rules: dict[str, Any]) -> KnowledgeBase:
    return KnowledgeBase.model_validate({"stroke": "forehand", "version": "t", "rules": list(rules)})


# --- Base de conocimiento real ---

def test_real_knowledge_bases_only_reference_existing_metrics() -> None:
    """Protege contra erratas en el YAML que harían que una regla nunca se dispare."""
    registry = default_registry()
    for base in YamlKnowledgeBaseRepository(KNOWLEDGE_DIR).load_all():
        referenced = {m for r in base.rules for m in r.metric_ids}
        assert referenced <= registry.metric_ids(base.stroke), base.label


def test_load_every_stroke_and_reject_a_missing_one(tmp_path: Path) -> None:
    repo = YamlKnowledgeBaseRepository(KNOWLEDGE_DIR)
    for stroke in get_args(StrokeType):
        assert repo.load(stroke).label.startswith(f"{stroke}@")
    with pytest.raises(UnsupportedStrokeError):
        YamlKnowledgeBaseRepository(tmp_path).load("volley")


def test_every_seed_has_errors_and_strengths() -> None:
    for base in YamlKnowledgeBaseRepository(KNOWLEDGE_DIR).load_all():
        assert len(base.errors) >= 6 and len(base.strengths) >= 6, base.label
        assert not any(rule.validated for rule in base.rules), base.label  # solo un validador valida


def test_invalid_yaml_fails_on_load(tmp_path: Path) -> None:
    (tmp_path / "forehand.yaml").write_text("stroke: forehand\nversion: '1'\nrules:\n  - id: X\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        YamlKnowledgeBaseRepository(tmp_path).load("forehand")


# --- Validación del esquema ---

@pytest.mark.parametrize("payload", [
    {"metric": "m", "op": "between", "value": 3},
    {"metric": "m", "op": "lt", "value": [1, 2]},
    {"metric": "m", "op": "unknown", "value": 1},
])
def test_condition_rejects_mismatched_value(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(payload)


def test_group_requires_exactly_one_combinator() -> None:
    cond = {"metric": "m", "op": "lt", "value": 1}
    with pytest.raises(ValidationError):
        kb(rule("R", {"all": [cond], "any": [cond]}))
    with pytest.raises(ValidationError):
        kb(rule("R", {}))


def test_duplicate_rule_ids_rejected() -> None:
    when = {"all": [{"metric": "m", "op": "lt", "value": 1}]}
    with pytest.raises(ValidationError):
        kb(rule("R", when), rule("R", when))


# --- Motor de reglas ---

@pytest.mark.parametrize(("op", "threshold", "value", "fires"), [
    ("lt", 10, 9, True), ("lt", 10, 10, False),
    ("lte", 10, 10, True), ("gt", 10, 11, True),
    ("gte", 10, 9, False), ("eq", 0, 0, True), ("eq", 0, 1, False),
    ("between", [5, 10], 7, True), ("between", [5, 10], 11, False),
])
def test_operators(op: str, threshold: Any, value: float, fires: bool) -> None:
    base = kb(rule("R", {"all": [{"metric": "m", "op": op, "value": threshold}]}))
    result = RuleEngine(0.5).evaluate(base, {"m": metric("m", value)})
    assert bool(result.fired) is fires


def test_all_vs_any() -> None:
    conds = [{"metric": "a", "op": "lt", "value": 1}, {"metric": "b", "op": "lt", "value": 1}]
    base = kb(rule("ALL", {"all": conds}), rule("ANY", {"any": conds}))
    result = RuleEngine(0.5).evaluate(base, {"a": metric("a", 0), "b": metric("b", 5)})
    assert [f.rule.id for f in result.fired] == ["ANY"]
    assert result.fired[0].evidence == {"a": 0, "b": 5}


def test_low_confidence_metric_blocks_rule() -> None:
    base = kb(rule("R", {"all": [{"metric": "m", "op": "lt", "value": 10}]}))
    result = RuleEngine(0.5).evaluate(base, {"m": metric("m", 1, confidence=0.3)})
    assert result.fired == []
    assert result.unmeasured_metrics == {"m"}


def test_missing_metric_is_reported_as_unmeasured() -> None:
    base = kb(rule("R", {"all": [{"metric": "ghost", "op": "lt", "value": 10}]}))
    assert RuleEngine(0.5).evaluate(base, {}).unmeasured_metrics == {"ghost"}


def test_polarity_requires_matching_texts_and_references() -> None:
    when = {"all": [{"metric": "m", "op": "lt", "value": 1}]}
    strength = rule("S", when) | {"polarity": "strength", "correction": ""}
    with pytest.raises(ValidationError):
        kb(strength)  # falta why_it_matters
    with pytest.raises(ValidationError):
        kb(rule("E", when) | {"caused_by": ["NOPE"]})
    with pytest.raises(ValidationError):
        kb(rule("E", when) | {"suppressed_by": ["E2"]}, rule("E2", when))  # solo en fortalezas
    assert kb(rule("E", when), strength | {"why_it_matters": "w", "suppressed_by": ["E"]}).strengths


def test_seed_yaml_matches_shared_json_schema() -> None:
    """contracts/knowledge_base.schema.json es la referencia común para worker, API y portal."""
    import json

    import yaml

    from tests.conftest import CONTRACTS_DIR

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((CONTRACTS_DIR / "knowledge_base.schema.json").read_text(encoding="utf-8"))
    for path in sorted(KNOWLEDGE_DIR.glob("*.yaml")):
        jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), schema)


# --- KB 2.1: estado de validación ---

def test_kb21_validation_fields_and_legacy_validated() -> None:
    legacy = kb({**rule("FH-A-001", {"all": [{"metric": "m", "op": "gt", "value": 1}]}), "validated": True})
    assert legacy.rules[0].status == "expert_reviewed" and legacy.rules[0].is_validated
    seed = kb(rule("FH-A-001", {"all": [{"metric": "m", "op": "gt", "value": 1}]}))
    assert seed.rules[0].status == "heuristic_unvalidated" and not seed.rules[0].is_validated
    empirical = kb({**rule("FH-A-001", {"all": [{"metric": "m", "op": "gt", "value": 1}]}),
                    "validation_status": "empirically_validated", "evidence": "n=40, P=0.85",
                    "evidence_level": "expert_validated",
                    "validation": {"validated_by": ["u1"], "n": 40, "precision": 0.85, "recall": 0.82}})
    assert empirical.rules[0].status == "empirically_validated"
    assert empirical.rules[0].validation is not None and empirical.rules[0].validation.n == 40


def test_kb21_rejects_unknown_validation_status() -> None:
    with pytest.raises(ValidationError):
        kb({**rule("FH-A-001", {"all": [{"metric": "m", "op": "gt", "value": 1}]}), "validation_status": "certified"})
