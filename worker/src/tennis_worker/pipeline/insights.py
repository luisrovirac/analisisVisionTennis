"""Del listado de errores a un informe de nivel entrenador (contrato 1.1): puntos fuertes, causa raíz,
plan de ejercicios, fiabilidad del informe y estado de validación de las reglas. Todo es determinista y
sale de la base de conocimiento; aquí no se inventa ninguna métrica."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tennis_worker.contracts import (
    Diagnosis,
    DrillPlan,
    DrillPlanItem,
    Finding,
    KnowledgeStatus,
    MetricValue,
    Reliability,
    Strength,
)
from tennis_worker.pipeline.knowledge.engine import FiredRule
from tennis_worker.pipeline.knowledge.models import KnowledgeBase, Rule

MAX_STRENGTHS = 5
PLAN_WEEKS = 4
MAX_FOCUSES = 2  # un foco a la vez: como mucho dos en todo el plan


def rank_strengths(strengths: Sequence[FiredRule], limit: int = MAX_STRENGTHS) -> list[Strength]:
    """Mayor aporte primero y, a igual aporte, la medición más fiable. Nunca más de `limit`:
    un informe con diez elogios no ayuda más que uno con tres."""
    ranked = sorted(strengths, key=lambda s: (-s.rule.impact, -s.confidence, s.rule.id))[:limit]
    return [
        Strength(rule_id=s.rule.id, rank=rank, category=s.rule.category, title=s.rule.title,
                 why_it_matters=s.rule.why_it_matters, how_to_maintain=s.rule.how_to_maintain,
                 evidence=s.evidence, confidence=round(s.confidence, 3), validated=s.rule.is_validated,
                 validation_status=s.rule.status)
        for rank, s in enumerate(ranked, start=1)
    ]


@dataclass(frozen=True)
class _CausalView:
    root: Finding
    contributing: list[Finding]


def _causal_view(findings: Sequence[Finding], kb: KnowledgeBase) -> _CausalView:
    """Causa raíz = el error mejor priorizado que no es consecuencia de otro error presente.
    Contribuyentes = los errores presentes que esa causa explica (directa o indirectamente)."""
    rules = {r.id: r for r in kb.rules}
    present = {f.rule_id for f in findings}

    def causes(rule_id: str) -> set[str]:
        rule = rules.get(rule_id)
        return set(rule.caused_by) & present if rule else set()

    root = next((f for f in findings if not causes(f.rule_id)), findings[0])  # ciclo → el de rank 1
    explained = {root.rule_id}
    changed = True
    while changed:  # cierre transitivo: si A causa B y B causa C, A explica C
        changed = False
        for f in findings:
            if f.rule_id not in explained and causes(f.rule_id) & explained:
                explained.add(f.rule_id)
                changed = True
    return _CausalView(root=root, contributing=[f for f in findings if f.rule_id in explained - {root.rule_id}])


def diagnose(findings: Sequence[Finding], strengths: Sequence[Strength], kb: KnowledgeBase,
             unmeasured_labels: Sequence[str], warnings: Sequence[str]) -> Diagnosis:
    lead = f"Tu punto fuerte: {strengths[0].title.lower()}. " if strengths else ""
    if not findings:
        return Diagnosis(
            summary=lead + "No detectamos errores con las reglas actuales.", root_cause_rule_id=None,
            contributing_factors=[], strengths_to_leverage=[s.rule_id for s in strengths[:2]],
            limitations=list(warnings), unmeasurable=sorted(unmeasured_labels))
    view = _causal_view(findings, kb)
    summary = f"{lead}Lo principal a trabajar: {view.root.title.lower()}."
    if view.contributing:
        names = ", ".join(f.title.lower() for f in view.contributing)
        summary += f" Corregirlo debería ayudarte también con: {names}."
    return Diagnosis(
        summary=summary, root_cause_rule_id=view.root.rule_id,
        contributing_factors=[f.rule_id for f in view.contributing],
        strengths_to_leverage=[s.rule_id for s in strengths[:2]],
        limitations=list(warnings), unmeasurable=sorted(unmeasured_labels))


def plan_drills(findings: Sequence[Finding], diagnosis: Diagnosis | None) -> DrillPlan | None:
    """Plan de 4 semanas: primero la causa raíz; después el siguiente error que ella no explica.
    Si solo hay un foco, se trabaja las cuatro semanas."""
    if not findings or diagnosis is None:
        return None
    by_id = {f.rule_id: f for f in findings}
    explained = {diagnosis.root_cause_rule_id, *diagnosis.contributing_factors}
    root = by_id.get(diagnosis.root_cause_rule_id or "", findings[0])
    focuses = [root, *[f for f in findings if f.rule_id not in explained]][:MAX_FOCUSES]
    weeks_per_focus = PLAN_WEEKS // len(focuses)
    items = [
        DrillPlanItem(week=week, focus_rule_id=focus.rule_id, focus=focus.title, drills=list(focus.drills))
        for index, focus in enumerate(focuses)
        for week in range(index * weeks_per_focus + 1, (index + 1) * weeks_per_focus + 1)
    ]
    return DrillPlan(weeks=PLAN_WEEKS, reanalyze_after_weeks=weeks_per_focus, items=items)


def assess_reliability(detection_rate: float, metrics: Mapping[str, MetricValue], min_confidence: float,
                       warnings: Sequence[str], notes: Sequence[str] = ()) -> Reliability:
    """Puntuación = detección de pose × confianza media de las métricas. Con avisos de calidad
    (vista, fps, métricas no medidas) el nivel nunca pasa de 'medium'."""
    confidences = [m.confidence for m in metrics.values()]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    score = round(max(0.0, min(1.0, detection_rate * mean_conf)), 3)
    level = "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"
    if warnings and level == "high":
        level = "medium"
    low = sorted(m.label for m in metrics.values() if m.confidence < min_confidence)
    reasons = [f"Jugador detectado en el {detection_rate:.0%} de los fotogramas.",
               f"Confianza media de las métricas: {mean_conf:.0%}."]
    if low:
        reasons.append("Medidas con poca fiabilidad (no se usan para conclusiones): " + ", ".join(low) + ".")
    reasons.extend(notes)
    return Reliability(score=score, level=level, reasons=reasons)


def knowledge_status(kb: KnowledgeBase) -> KnowledgeStatus:
    total = len(kb.rules)
    validated = sum(1 for rule in kb.rules if _validated(rule))
    status = "validated" if total and validated == total else "partially_validated" if validated else "provisional"
    return KnowledgeStatus(status=status, validated_rules=validated, total_rules=total)


def _validated(rule: Rule) -> bool:
    return rule.is_validated
