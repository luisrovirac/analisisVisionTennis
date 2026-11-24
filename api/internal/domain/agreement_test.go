package domain

import (
	"errors"
	"math"
	"testing"
)

// Ejemplo de Krippendorff (2011, «Computing Krippendorff's Alpha-Reliability»): 4 evaluadores,
// 12 unidades, con datos faltantes. Valores publicados: nominal 0,743; ordinal 0,815; intervalo 0,849.
func krippendorffExample() [][]float64 {
	const m = -1 // dato faltante
	coders := [][]float64{
		{1, 2, 3, 3, 2, 1, 4, 1, 2, m, m, m},
		{1, 2, 3, 3, 2, 2, 4, 1, 2, 5, m, 3},
		{m, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, m},
		{1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, m},
	}
	units := make([][]float64, 12)
	for _, row := range coders {
		for u, v := range row {
			if v != m {
				units[u] = append(units[u], v)
			}
		}
	}
	return units
}

func TestKrippendorffAlphaPublishedExample(t *testing.T) {
	units := krippendorffExample()
	for metric, want := range map[AlphaMetric]float64{AlphaNominal: 0.743, AlphaOrdinal: 0.815, AlphaInterval: 0.849} {
		got, ok := KrippendorffAlpha(units, metric)
		if !ok || math.Abs(got-want) > 0.0015 {
			t.Errorf("%s: α=%.4f ok=%v, esperado %.3f", metric, got, ok, want)
		}
	}
}

func TestKrippendorffAlphaEdgeCases(t *testing.T) {
	if a, ok := KrippendorffAlpha([][]float64{{3, 3}, {5, 5}, {1, 1}}, AlphaOrdinal); !ok || a != 1 {
		t.Fatalf("acuerdo perfecto: α=%v ok=%v", a, ok)
	}
	if _, ok := KrippendorffAlpha([][]float64{{4, 4}, {4, 4}}, AlphaOrdinal); ok {
		t.Fatal("sin variación α es indefinido")
	}
	if _, ok := KrippendorffAlpha([][]float64{{4}, {2}}, AlphaOrdinal); ok {
		t.Fatal("sin unidades emparejables α es indefinido")
	}
	if a, _ := KrippendorffAlpha([][]float64{{1, 7}, {7, 1}, {2, 6}}, AlphaOrdinal); a >= 0 {
		t.Fatalf("desacuerdo sistemático debe dar α < 0: %v", a)
	}
}

func TestAlphaBand(t *testing.T) {
	cases := map[float64]string{0.9: "reliable", 0.8: "reliable", 0.7: "tentative", 0.5: "unusable"}
	for a, want := range cases {
		if got := AlphaBand(a); got != want {
			t.Errorf("α=%v → %s, esperado %s", a, got, want)
		}
	}
}

func TestConsensusOf(t *testing.T) {
	c := ConsensusOf([]int{2, 3, 3, 4, 7}, 0.7, 3)
	if c.Median != 3 || c.WithinOne != 0.8 || !c.Reached || c.Counts[2] != 2 {
		t.Fatalf("consenso: %+v", c)
	}
	split := ConsensusOf([]int{1, 1, 7, 7}, 0.7, 3)
	if split.Reached || split.Median != 4 {
		t.Fatalf("opiniones polarizadas no son consenso: %+v", split)
	}
	if ConsensusOf([]int{3, 3}, 0.7, 3).Reached {
		t.Fatal("con menos evaluadores que el mínimo no hay consenso")
	}
	if q := ConsensusOf([]int{1, 2, 3, 4}, 0.7, 2); q.Q1 != 1.75 || q.Q3 != 3.25 {
		t.Fatalf("cuartiles tipo 7: %+v", q)
	}
}

func TestLabelForAndDerivedVerdict(t *testing.T) {
	cases := []struct {
		score    float64
		polarity string
		want     string
	}{
		{2, PolarityError, LabelPresent}, {3, PolarityError, LabelPresent}, {3.5, PolarityError, LabelIndeterminate},
		{4, PolarityError, LabelIndeterminate}, {5, PolarityError, LabelAbsent},
		{6, PolarityStrength, LabelPresent}, {2, PolarityStrength, LabelAbsent},
	}
	for _, c := range cases {
		if got := LabelFor(c.score, c.polarity); got != c.want {
			t.Errorf("LabelFor(%v, %s)=%s, esperado %s", c.score, c.polarity, got, c.want)
		}
	}
	verdicts := map[[2]any]Verdict{
		{LabelPresent, true}: VerdictCorrect, {LabelAbsent, false}: VerdictCorrect,
		{LabelAbsent, true}: VerdictFalsePositive, {LabelPresent, false}: VerdictMissed,
		{LabelIndeterminate, true}: VerdictUnsure,
	}
	for in, want := range verdicts {
		if got := DerivedVerdict(in[0].(string), in[1].(bool)); got != want {
			t.Errorf("%v → %s, esperado %s", in, got, want)
		}
	}
}

func TestConfusionAndWilson(t *testing.T) {
	var c Confusion
	for range 8 {
		c.Add(true, true)
	}
	c.Add(false, true)
	c.Add(true, false)
	c.Add(true, false)
	c.Add(false, false)
	if c.N() != 12 || c.Precision().Value != 8.0/9 || c.Recall().Value != 0.8 {
		t.Fatalf("confusión: %+v p=%+v r=%+v", c, c.Precision(), c.Recall())
	}
	if f := c.F1(); f == nil || math.Abs(*f-0.8421) > 0.001 {
		t.Fatalf("f1: %v", f)
	}
	// Wilson 95 % para 8/10 ≈ [0,490; 0,943] (valor de referencia de tablas estándar).
	lo, hi := Wilson(8, 10)
	if math.Abs(lo-0.490) > 0.002 || math.Abs(hi-0.943) > 0.002 {
		t.Fatalf("wilson: [%v, %v]", lo, hi)
	}
	if (Confusion{TN: 3}).Precision() != nil {
		t.Fatal("sin predicciones positivas la precisión es indefinida")
	}
}

func TestEvidenceSufficient(t *testing.T) {
	good := Evidence{Confusion: Confusion{TP: 18, FP: 2, FN: 2, TN: 10}}
	if err := good.Sufficient(); err != nil {
		t.Fatal(err)
	}
	small := Evidence{Confusion: Confusion{TP: 5, TN: 5}}
	if err := small.Sufficient(); !errors.Is(err, ErrEvidenceInsufficient) {
		t.Fatalf("muestra pequeña: %v", err)
	}
	imprecise := Evidence{Confusion: Confusion{TP: 10, FP: 10, TN: 12}}
	if err := imprecise.Sufficient(); !errors.Is(err, ErrEvidenceInsufficient) {
		t.Fatalf("precisión baja: %v", err)
	}
	low := 0.4
	unreliable := Evidence{Confusion: good.Confusion, Alpha: &low}
	if err := unreliable.Sufficient(); !errors.Is(err, ErrEvidenceInsufficient) {
		t.Fatalf("α bajo: %v", err)
	}
}

func TestAgreementPerRule(t *testing.T) {
	r := func(v int) *int { return &v }
	review := func(caseID string, rating *int) CaseReview {
		return CaseReview{AnalysisID: caseID, Items: []ReviewItem{{RuleID: "R-1", Rating: rating, NotEvaluable: rating == nil}}}
	}
	reviews := []CaseReview{
		review("a", r(2)), review("a", r(2)), review("a", r(3)),
		review("b", r(6)), review("b", r(6)), review("b", r(7)),
		review("c", nil),
	}
	ra, cons := Agreement("R-1", reviews, 0.7, 3)
	if ra.Units != 2 || ra.NotEvaluable != 1 || ra.Alpha == nil || *ra.Alpha < AlphaTentative || ra.ConsensusRate != 1 {
		t.Fatalf("acuerdo: %+v", ra)
	}
	if cons["a"].Median != 2 || !cons["b"].Reached {
		t.Fatalf("consenso por caso: %+v", cons)
	}
}

func TestSplitForIsDeterministicAndBalanced(t *testing.T) {
	const id = "7d0c3c39-3a64-4c3a-9a7e-0e1f0e7d9b11"
	if SplitFor(id) != SplitFor(id) {
		t.Fatal("el split debe ser determinista")
	}
	test := 0
	for i := range 1000 {
		if SplitFor(string(rune(i))+id) == SplitTest {
			test++
		}
	}
	if test < 230 || test > 370 {
		t.Fatalf("≈30 %% de test esperado, salió %d/1000", test)
	}
}

func TestReviewInputValidate(t *testing.T) {
	r, v := 5, VerdictCorrect
	ok := ReviewInput{Items: []ReviewItem{{RuleID: "FH-X-001", Rating: &r, Verdict: &v}}}
	if err := ok.Validate(false); err != nil {
		t.Fatal(err)
	}
	if err := ok.Validate(true); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("en campaña ciega no hay veredicto")
	}
	bad := []ReviewInput{
		{},
		{Items: []ReviewItem{{RuleID: "FH-X-001"}}},                                 // ni rating ni no evaluable
		{Items: []ReviewItem{{RuleID: "FH-X-001", Rating: &r, NotEvaluable: true}}}, // ambos
		{Items: []ReviewItem{{RuleID: "FH-X-001", Rating: new(int)}}},               // 0 fuera de rango
		{Items: []ReviewItem{{RuleID: "x", NotEvaluable: true}}},                    // id inválido
		{Items: []ReviewItem{{RuleID: "FH-X-001", NotEvaluable: true}, {RuleID: "FH-X-001", NotEvaluable: true}}},
	}
	for i, in := range bad {
		if err := in.Validate(false); !errors.Is(err, ErrInvalidInput) {
			t.Errorf("caso %d debería fallar", i)
		}
	}
}

func TestCampaignInputValidate(t *testing.T) {
	id := func(n int) string { return "00000000-0000-4000-8000-00000000000" + string(rune('0'+n)) }
	in := CampaignInput{Stroke: StrokeForehand, Title: "Ronda piloto", CaseIDs: []string{id(1), id(2)},
		RaterIDs: []string{id(3), id(4), id(5)}}.WithDefaults()
	if err := in.Validate(); err != nil {
		t.Fatal(err)
	}
	few := in
	few.RaterIDs = few.RaterIDs[:2]
	if err := few.Validate(); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("menos evaluadores que min_raters")
	}
	dup := in
	dup.CaseIDs = []string{id(1), id(1)}
	if err := dup.Validate(); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("casos repetidos")
	}
}
