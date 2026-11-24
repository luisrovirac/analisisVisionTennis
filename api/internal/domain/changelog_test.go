package domain

import (
	"encoding/json"
	"testing"
)

func TestChangelogBumps(t *testing.T) {
	kb := KnowledgeBase{Stroke: StrokeForehand, Version: "0.3.0", Rules: []Rule{errorRule("FH-A-001"), errorRule("FH-B-001")}}

	text := errorRule("FH-A-001")
	text.Correction = "otra corrección"
	entries, bump := Changelog(kb, []Change{{Op: OpUpsertRule, Rule: &text}})
	if bump != BumpPatch || entries[0].Action != "modified" || len(entries[0].Fields) != 1 || entries[0].Fields[0] != "correction" {
		t.Fatalf("solo texto = patch: %+v %s", entries, bump)
	}

	threshold := errorRule("FH-A-001")
	threshold.When.All[0].Value.Value = 165
	entries, bump = Changelog(kb, []Change{{Op: OpUpsertRule, Rule: &threshold}, {Op: OpUpsertRule, Rule: &text}})
	if bump != BumpMinor || len(entries[0].Thresholds) != 1 ||
		entries[0].Thresholds[0] != (ThresholdChange{Metric: "knee_angle_min", Before: "gt 160", After: "gt 165"}) {
		t.Fatalf("umbral = minor: %+v %s", entries, bump)
	}

	added := errorRule("FH-C-001")
	if _, bump = Changelog(kb, []Change{{Op: OpUpsertRule, Rule: &added}}); bump != BumpMinor {
		t.Fatalf("regla nueva = minor: %s", bump)
	}
	if entries, bump = Changelog(kb, []Change{{Op: OpRemoveRule, RuleID: "FH-B-001"}}); bump != BumpMajor || entries[0].Action != "removed" {
		t.Fatalf("retirar = major: %+v %s", entries, bump)
	}

	// Cambiar solo el estado de validación no es un cambio de contenido.
	reviewed := errorRule("FH-A-001")
	reviewed.ValidationStatus = ValidationEmpirical
	if entries, bump = Changelog(kb, []Change{{Op: OpUpsertRule, Rule: &reviewed}}); bump != BumpPatch || len(entries[0].Fields) != 0 {
		t.Fatalf("estado de validación: %+v %s", entries, bump)
	}
}

func TestBumpVersion(t *testing.T) {
	cases := map[Bump]string{BumpMajor: "1.0.0", BumpMinor: "0.4.0", BumpPatch: "0.3.3"}
	for b, want := range cases {
		if got, err := BumpVersion("0.3.2", b); err != nil || got != want {
			t.Errorf("%s: %s %v", b, got, err)
		}
	}
	if _, err := BumpVersion("1.x.0", BumpPatch); err == nil {
		t.Fatal("no semver")
	}
}

func TestKB21RuleFields(t *testing.T) {
	r := errorRule("FH-A-001")
	r.ValidationStatus, r.EvidenceLevel = "certified", "rumor"
	if errs := r.problems(); len(errs) != 2 {
		t.Fatalf("enums inválidos: %v", errs)
	}
	// Un snapshot 2.0 con validated=true se lee como expert_reviewed.
	var legacy Rule
	_ = json.Unmarshal([]byte(`{"validated": true}`), &legacy)
	if got := legacy.Normalize(); got.ValidationStatus != ValidationExpertReviewed || !got.Validated {
		t.Fatalf("legado: %+v", got)
	}
	// El experto no puede autovalidarse, pero sí pedir empirically_validated.
	asked := errorRule("FH-A-001")
	asked.ValidationStatus, asked.Validation = ValidationEmpirical, &RuleValidation{N: 999}
	self := errorRule("FH-B-001")
	self.ValidationStatus = ValidationExpertReviewed
	in := ProposalInput{Changes: []Change{{Op: OpUpsertRule, Rule: &asked}, {Op: OpUpsertRule, Rule: &self}}}.Sanitized()
	if in.Changes[0].Rule.ValidationStatus != ValidationEmpirical || in.Changes[0].Rule.Validation != nil {
		t.Fatalf("petición empírica: %+v", in.Changes[0].Rule)
	}
	if in.Changes[1].Rule.ValidationStatus != ValidationHeuristic || in.Changes[1].Rule.Validated {
		t.Fatalf("autovalidación: %+v", in.Changes[1].Rule)
	}
	kb := KnowledgeBase{Rules: []Rule{errorRule("FH-A-001"), errorRule("FH-B-001")}}
	approved, _ := ApplyChanges(kb, in.Changes, true)
	if approved.Rules[0].ValidationStatus != ValidationEmpirical || approved.Rules[1].ValidationStatus != ValidationExpertReviewed {
		t.Fatalf("aprobación: %+v", approved.Rules)
	}
}
