package domain

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func loadFixture(t *testing.T, name string, dst any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "fixtures", name))
	if err != nil {
		t.Fatal(err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields() // el mismo rigor que con lo que envía un experto
	if err := dec.Decode(dst); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
}

func seed(t *testing.T) (KnowledgeBase, []MetricInfo) {
	t.Helper()
	var kb KnowledgeBase
	var catalog []MetricInfo
	loadFixture(t, "knowledge_base.example.json", &kb)
	loadFixture(t, "metric_catalog.example.json", &catalog)
	return kb, catalog
}

// La semilla que exporta el worker (contracts/fixtures) es válida para la API: mismo formato en ambos lados.
func TestSeedFixtureValidatesAndRoundTrips(t *testing.T) {
	kb, catalog := seed(t)
	if err := kb.Validate(catalog); err != nil {
		t.Fatal(err)
	}
	out, err := json.Marshal(kb)
	if err != nil {
		t.Fatal(err)
	}
	var again KnowledgeBase
	if err := json.Unmarshal(out, &again); err != nil || len(again.Rules) != len(kb.Rules) {
		t.Fatalf("round trip: %v", err)
	}
	if !strings.Contains(string(out), `"value":[110,145]`) {
		t.Fatalf("between debe serializarse como [min, max]: %s", out)
	}
}

func errorRule(id string) Rule {
	return Rule{ID: id, Title: "t", Category: "fundamental", Polarity: PolarityError, Impact: 3, Difficulty: 2,
		When:        ConditionGroup{All: []Condition{{Metric: "knee_angle_min", Op: "gt", Value: Threshold{Value: 160}}}},
		Explanation: "e", Correction: "c"}
}

func TestRuleValidate(t *testing.T) {
	strength := errorRule("FH-STR-100")
	strength.Polarity, strength.Correction, strength.WhyItMatters = PolarityStrength, "", "w"
	cases := map[string]struct {
		mutate func(*Rule)
		ok     bool
	}{
		"válida":                 {func(*Rule) {}, true},
		"fortaleza válida":       {func(r *Rule) { *r = strength }, true},
		"id en minúsculas":       {func(r *Rule) { r.ID = "fh-1" }, false},
		"impacto 6":              {func(r *Rule) { r.Impact = 6 }, false},
		"sin condiciones":        {func(r *Rule) { r.When = ConditionGroup{} }, false},
		"all y any":              {func(r *Rule) { r.When.Any = r.When.All }, false},
		"between escalar":        {func(r *Rule) { r.When.All[0].Op = "between" }, false},
		"rango invertido":        {func(r *Rule) { r.When.All[0] = Condition{"m", "between", Threshold{Low: 5, High: 1, IsRange: true}} }, false},
		"error sin corrección":   {func(r *Rule) { r.Correction = " " }, false},
		"fortaleza sin por qué":  {func(r *Rule) { *r = strength; r.WhyItMatters = "" }, false},
		"caused_by en fortaleza": {func(r *Rule) { *r = strength; r.CausedBy = []string{"X-1"} }, false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			r := errorRule("FH-TEST-001")
			r.When.All = append([]Condition(nil), r.When.All...)
			tc.mutate(&r)
			err := r.Validate()
			if tc.ok != (err == nil) {
				t.Fatalf("ok=%v err=%v", tc.ok, err)
			}
			if err != nil && !errors.Is(err, ErrInvalidInput) {
				t.Fatalf("debe envolver ErrInvalidInput: %v", err)
			}
		})
	}
}

func TestKnowledgeBaseValidateChecksCatalogAndReferences(t *testing.T) {
	kb, catalog := seed(t)
	unknown := errorRule("FH-NEW-001")
	unknown.When.All = []Condition{{Metric: "no_existe", Op: "lt", Value: Threshold{Value: 1}}}
	broken, _ := ApplyChanges(kb, []Change{{Op: OpUpsertRule, Rule: &unknown}}, false)
	if err := broken.Validate(catalog); err == nil || !strings.Contains(err.Error(), "no_existe") {
		t.Fatalf("debe rechazar métricas fuera del catálogo: %v", err)
	}
	// Retirar un error que otro cita en caused_by deja una referencia rota.
	orphan, err := ApplyChanges(kb, []Change{{Op: OpRemoveRule, RuleID: "FH-PREP-001"}}, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := orphan.Validate(catalog); err == nil || !strings.Contains(err.Error(), "FH-PREP-001") {
		t.Fatalf("debe rechazar referencias rotas: %v", err)
	}
}

func TestApplyChangesMarksValidatedOnlyWhenApproving(t *testing.T) {
	kb, _ := seed(t)
	edited := errorRule("FH-POST-001")
	edited.Validated = true // el experto lo intenta: se ignora salvo al aprobar
	draft, _ := ApplyChanges(kb, []Change{{Op: OpUpsertRule, Rule: &edited}}, false)
	approved, _ := ApplyChanges(kb, []Change{{Op: OpUpsertRule, Rule: &edited}}, true)
	find := func(k KnowledgeBase) Rule {
		for _, r := range k.Rules {
			if r.ID == "FH-POST-001" {
				return r
			}
		}
		t.Fatal("regla no encontrada")
		return Rule{}
	}
	if find(draft).Validated || !find(approved).Validated || len(approved.Rules) != len(kb.Rules) {
		t.Fatalf("validated mal aplicado")
	}
	if find(kb).When.All[0].Value.Value != 160 || kb.Rules[0].Validated {
		t.Fatal("ApplyChanges no debe modificar la base original")
	}
	if _, err := ApplyChanges(kb, []Change{{Op: OpRemoveRule, RuleID: "NOPE-1"}}, false); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("retirar una regla inexistente: %v", err)
	}
}

func TestProposalInputSanitizeAndValidate(t *testing.T) {
	r := errorRule("FH-NEW-001")
	r.Validated, r.Polarity = true, ""
	in := ProposalInput{Stroke: StrokeForehand, Title: " t ", Rationale: "r", Changes: []Change{{Op: OpUpsertRule, Rule: &r}}}.Sanitized()
	if in.Changes[0].Rule.Validated || in.Changes[0].Rule.Polarity != PolarityError || in.Title != "t" {
		t.Fatalf("sanitize: %+v", in.Changes[0].Rule)
	}
	if err := in.Validate(); err != nil {
		t.Fatal(err)
	}
	dup := in
	dup.Changes = append(dup.Changes, Change{Op: OpRemoveRule, RuleID: "FH-NEW-001"})
	if err := dup.Validate(); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("la misma regla dos veces debe rechazarse")
	}
	if err := (ProposalInput{Stroke: StrokeForehand, Title: "t", Rationale: "r"}).Validate(); err == nil {
		t.Fatal("sin cambios debe rechazarse")
	}
}

func TestNextVersion(t *testing.T) {
	if v, err := NextVersion("0.2.0"); err != nil || v != "0.3.0" {
		t.Fatalf("v=%s err=%v", v, err)
	}
	if _, err := NextVersion("dev"); err == nil {
		t.Fatal("una versión no semver debe fallar")
	}
}

func TestEvaluateMirrorsWorkerEngine(t *testing.T) {
	r := errorRule("FH-X-001")
	samples := []MetricSample{
		{"knee_angle_min": {Value: 170, Confidence: 0.9}}, // dispara
		{"knee_angle_min": {Value: 150, Confidence: 0.9}}, // medible, no dispara
		{"knee_angle_min": {Value: 175, Confidence: 0.2}}, // poco fiable: no cuenta
		{},
	}
	if got := CountRule(r, samples, 0.5); got != (RuleCount{Fired: 1, Measurable: 2}) {
		t.Fatalf("got %+v", got)
	}
	r.When = ConditionGroup{Any: []Condition{
		{Metric: "a", Op: "between", Value: Threshold{Low: 1, High: 2, IsRange: true}},
		{Metric: "b", Op: "eq", Value: Threshold{Value: 1}},
	}}
	if _, fired := r.Evaluate(MetricSample{"a": {5, 1}, "b": {1, 1}}, 0.5); !fired {
		t.Fatal("any debe disparar si una condición se cumple")
	}
}

func TestThresholdRejectsGarbage(t *testing.T) {
	var th Threshold
	for _, raw := range []string{`"10"`, `[1]`, `[1,2,3]`, `{}`} {
		if err := json.Unmarshal([]byte(raw), &th); err == nil {
			t.Errorf("%s debería rechazarse", raw)
		}
	}
}
