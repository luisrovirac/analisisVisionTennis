from __future__ import annotations

from tennis_worker.pipeline.knowledge.engine import FiredRule
from tennis_worker.pipeline.knowledge.models import Rule
from tennis_worker.pipeline.prioritization import CategoryImpactPrioritizer


def fired(id_: str, category: str, impact: int, difficulty: int) -> FiredRule:
    rule = Rule.model_validate({
        "id": id_, "title": id_, "category": category, "impact": impact, "difficulty": difficulty,
        "when": {"all": [{"metric": "m", "op": "lt", "value": 1}]},
        "explanation": "e", "correction": "c", "drills": ["d"],
    })
    return FiredRule(rule=rule, evidence={"m": 0.5})


def test_fundamental_before_sync_then_impact_then_difficulty() -> None:
    items = [
        fired("SYNC-HIGH", "sincronizacion", 5, 1),
        fired("FUND-LOW", "fundamental", 2, 1),
        fired("FUND-HIGH-HARD", "fundamental", 5, 4),
        fired("FUND-HIGH-EASY", "fundamental", 5, 1),
    ]
    findings = CategoryImpactPrioritizer().prioritize(items)
    assert [f.rule_id for f in findings] == ["FUND-HIGH-EASY", "FUND-HIGH-HARD", "FUND-LOW", "SYNC-HIGH"]
    assert [f.rank for f in findings] == [1, 2, 3, 4]
    assert findings[0].drills == ["d"] and findings[0].evidence == {"m": 0.5}


def test_empty_input() -> None:
    assert CategoryImpactPrioritizer().prioritize([]) == []
