from __future__ import annotations

from typing import Any

from tennis_worker.contracts import Finding, MetricValue
from tennis_worker.pipeline import insights
from tennis_worker.pipeline.knowledge.engine import FiredRule, RuleEngine
from tennis_worker.pipeline.knowledge.models import KnowledgeBase, Rule

LT = {"all": [{"metric": "m", "op": "lt", "value": 1}]}
GTE = {"all": [{"metric": "m", "op": "gte", "value": 5}]}


def rule(id_: str, polarity: str = "error", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": id_, "title": id_, "category": "fundamental", "impact": 3, "difficulty": 2,
                            "polarity": polarity, "when": LT if polarity == "error" else GTE, "explanation": "e"}
    base |= {"correction": "c"} if polarity == "error" else {"why_it_matters": "w"}
    return base | extra


def kb(*rules: dict[str, Any]) -> KnowledgeBase:
    return KnowledgeBase.model_validate({"stroke": "forehand", "version": "t", "rules": list(rules)})


def metric(value: float, confidence: float = 0.9) -> dict[str, MetricValue]:
    return {"m": MetricValue(id="m", label="m", value=value, unit="u", confidence=confidence)}


def finding(id_: str, rank: int) -> Finding:
    return Finding(rule_id=id_, rank=rank, category="fundamental", title=id_.title(), explanation="e",
                   correction="c", drills=[f"drill {id_}"], evidence={})


def test_strength_needs_its_own_threshold_and_reliable_metric() -> None:
    base = kb(rule("STR", "strength"))
    engine = RuleEngine(min_confidence=0.5)
    assert engine.evaluate(base, metric(6)).strengths
    assert not engine.evaluate(base, metric(4)).strengths  # por debajo del umbral
    assert not engine.evaluate(base, metric(6, confidence=0.2)).strengths  # medición poco fiable


def test_strength_is_suppressed_by_a_contradicting_error() -> None:
    base = kb(rule("ERR", when={"all": [{"metric": "m", "op": "gt", "value": 0}]}),
              rule("STR", "strength", suppressed_by=["ERR"]))
    evaluation = RuleEngine(min_confidence=0.5).evaluate(base, metric(6))
    assert [f.rule.id for f in evaluation.fired] == ["ERR"] and evaluation.strengths == []


def test_rank_strengths_caps_and_orders_by_impact_then_confidence() -> None:
    items = [FiredRule(Rule.model_validate(rule(f"S{i}", "strength", impact=1 + i % 5)), {}, 0.5 + i / 100)
             for i in range(8)]
    ranked = insights.rank_strengths(items)
    assert len(ranked) == insights.MAX_STRENGTHS
    assert [s.rule_id for s in ranked[:2]] == ["S4", "S3"]


def test_root_cause_follows_the_causal_graph_transitively() -> None:
    base = kb(rule("A"), rule("B", caused_by=["A"]), rule("C", caused_by=["B"]), rule("D"))
    findings = [finding("C", 1), finding("D", 2), finding("B", 3), finding("A", 4)]
    diagnosis = insights.diagnose(findings, [], base, [], [])
    assert diagnosis.root_cause_rule_id == "D"  # mejor priorizado sin causa presente
    findings = [finding("C", 1), finding("B", 2), finding("A", 3), finding("D", 4)]
    diagnosis = insights.diagnose(findings, [], base, [], [])
    assert diagnosis.root_cause_rule_id == "A" and diagnosis.contributing_factors == ["C", "B"]


def test_plan_has_root_first_then_unexplained_focus() -> None:
    base = kb(rule("A"), rule("B", caused_by=["A"]), rule("D"))
    findings = [finding("A", 1), finding("B", 2), finding("D", 3)]
    plan = insights.plan_drills(findings, insights.diagnose(findings, [], base, [], []))
    assert plan is not None and plan.weeks == 4
    assert [i.focus_rule_id for i in plan.items] == ["A", "A", "D", "D"]


def test_reliability_is_capped_when_there_are_warnings() -> None:
    clean = insights.assess_reliability(1.0, metric(1, confidence=0.95), 0.5, [])
    warned = insights.assess_reliability(1.0, metric(1, confidence=0.95), 0.5, ["vista frontal"])
    assert (clean.level, warned.level) == ("high", "medium")
    assert insights.assess_reliability(0.6, metric(1, confidence=0.3), 0.5, []).level == "low"


def test_knowledge_status_counts_validated_rules() -> None:
    assert insights.knowledge_status(kb(rule("A"), rule("B"))).status == "provisional"
    assert insights.knowledge_status(kb(rule("A", validated=True), rule("B"))).status == "partially_validated"
    assert insights.knowledge_status(kb(rule("A", validated=True))).status == "validated"
