package domain

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"
)

// Validación formal de las reglas: revisiones de expertos sobre casos reales, campañas Delphi ciegas
// con varios evaluadores, ground truth de consenso y estado de validación de cada regla.

// --- Estado de validación de una regla (KB 2.1) ---

const (
	// ValidationHeuristic: regla escrita por alguien, sin revisión ni datos (así entran las semillas).
	ValidationHeuristic = "heuristic_unvalidated"
	// ValidationExpertReviewed: un validador aprobó su lógica y su contenido.
	ValidationExpertReviewed = "expert_reviewed"
	// ValidationEmpirical: además acierta contra el ground truth de consenso en el conjunto de test.
	ValidationEmpirical = "empirically_validated"
)

var validationStatuses = []string{ValidationHeuristic, ValidationExpertReviewed, ValidationEmpirical}

var evidenceLevels = []string{"provisional", "literature", "expert_validated"}

// RuleValidation es la traza de la validación: quién, cuándo y con qué datos. Solo ids de usuario:
// los nombres no salen nunca al reporte del usuario.
type RuleValidation struct {
	ValidatedBy []string   `json:"validated_by,omitempty"`
	ValidatedAt *time.Time `json:"validated_at,omitempty"`
	N           int        `json:"n,omitempty"` // casos de test con ground truth y medibles
	Precision   *float64   `json:"precision,omitempty"`
	Recall      *float64   `json:"recall,omitempty"`
	Alpha       *float64   `json:"alpha,omitempty"` // acuerdo entre expertos de la última campaña
	CampaignID  string     `json:"campaign_id,omitempty"`
}

// Requisitos mínimos para marcar una regla como empirically_validated (planIII F4.2).
const (
	EmpiricalMinN         = 30
	EmpiricalMinPrecision = 0.8
	EmpiricalMinRecall    = 0.8
)

// Evidence es lo que el sistema midió de una regla candidata contra el ground truth de test.
type Evidence struct {
	Confusion Confusion `json:"confusion"`
	Alpha     *float64  `json:"alpha,omitempty"`
	Campaign  string    `json:"campaign_id,omitempty"`
}

// Sufficient explica, si no alcanza, por qué la evidencia no basta para empirically_validated.
func (e Evidence) Sufficient() error {
	n := e.Confusion.N()
	p, r := e.Confusion.Precision(), e.Confusion.Recall()
	switch {
	case n < EmpiricalMinN:
		return fmt.Errorf("%w: hacen falta al menos %d casos de test con ground truth (hay %d)", ErrEvidenceInsufficient, EmpiricalMinN, n)
	case p == nil || p.Value < EmpiricalMinPrecision:
		return fmt.Errorf("%w: la precisión en test debe ser ≥ %.2f", ErrEvidenceInsufficient, EmpiricalMinPrecision)
	case r == nil || r.Value < EmpiricalMinRecall:
		return fmt.Errorf("%w: el recall en test debe ser ≥ %.2f", ErrEvidenceInsufficient, EmpiricalMinRecall)
	case e.Alpha != nil && *e.Alpha < AlphaTentative:
		return fmt.Errorf("%w: el acuerdo entre expertos (α=%.2f) es demasiado bajo para usarlo como referencia", ErrEvidenceInsufficient, *e.Alpha)
	}
	return nil
}

// --- Muestras para evaluar reglas sin reprocesar video ---

// Sample son las métricas guardadas de un análisis completado y la vista de cámara con que se grabó.
type Sample struct {
	ID      string
	View    CameraView
	Metrics MetricSample
}

// LabeledSample es una muestra del corpus con el ground truth de consenso para una regla.
type LabeledSample struct {
	Sample
	RuleID  string
	Present bool
}

// --- Corpus ---

const (
	SplitCalibration = "calibration"
	SplitTest        = "test"
)

// SplitFor asigna cada caso a calibración (≈70 %) o test (≈30 %) de forma determinista: el mismo
// caso cae siempre en el mismo lado, así los umbrales nunca se ajustan con los casos de test.
func SplitFor(analysisID string) string {
	sum := sha256.Sum256([]byte(analysisID))
	if int(sum[0])%10 < 7 {
		return SplitCalibration
	}
	return SplitTest
}

func ValidSplit(s string) bool { return s == SplitCalibration || s == SplitTest }

type CorpusEntry struct {
	AnalysisID  string
	Stroke      StrokeType
	Split       string
	Note        string
	AddedBy     string
	AddedAt     time.Time
	GroundTruth int // reglas con ground truth de consenso para este caso
}

// --- Revisiones ---

type Verdict string

const (
	VerdictCorrect       Verdict = "correct"
	VerdictFalsePositive Verdict = "false_positive"
	VerdictMissed        Verdict = "missed"
	VerdictUnsure        Verdict = "unsure"
)

var verdicts = []Verdict{VerdictCorrect, VerdictFalsePositive, VerdictMissed, VerdictUnsure}

var playerLevels = []string{"beginner", "intermediate", "advanced", "pro"}

// ReviewItem es la opinión de un experto sobre una regla en un caso.
type ReviewItem struct {
	RuleID       string   `json:"rule_id"`
	Rating       *int     `json:"rating"` // 1 muy deficiente … 7 excelente (el aspecto, no la regla)
	NotEvaluable bool     `json:"not_evaluable"`
	Verdict      *Verdict `json:"verdict"` // sobre lo que dijo el sistema (solo en revisión libre)
	SystemFired  *bool    `json:"system_fired"`
	Comment      string   `json:"comment"`
}

type CaseReview struct {
	ID          string
	AnalysisID  string
	ReviewerID  string
	CampaignID  *string
	Round       *int
	PlayerLevel *string
	Notes       string
	KBVersion   *string
	Items       []ReviewItem
	SubmittedAt time.Time
}

// ReviewInput es lo que envía el experto.
type ReviewInput struct {
	PlayerLevel *string      `json:"player_level"`
	Notes       string       `json:"notes"`
	Items       []ReviewItem `json:"items"`
}

const maxReviewItems = 100

// Validate comprueba la forma. blind = campaña: el experto no ve al sistema, así que no da veredicto.
func (in ReviewInput) Validate(blind bool) error {
	if in.PlayerLevel != nil && !slices.Contains(playerLevels, *in.PlayerLevel) {
		return invalid("player_level", playerLevels)
	}
	if len(in.Notes) > 4000 {
		return fmt.Errorf("%w: notes máx. 4000", ErrInvalidInput)
	}
	if len(in.Items) == 0 || len(in.Items) > maxReviewItems {
		return fmt.Errorf("%w: califica al menos una regla (máx. %d)", ErrInvalidInput, maxReviewItems)
	}
	seen := map[string]bool{}
	for _, it := range in.Items {
		switch {
		case seen[it.RuleID]:
			return fmt.Errorf("%w: la regla %s aparece dos veces", ErrInvalidInput, it.RuleID)
		case !ruleIDPattern.MatchString(it.RuleID):
			return fmt.Errorf("%w: rule_id %q inválido", ErrInvalidInput, it.RuleID)
		case it.Rating != nil && (*it.Rating < 1 || *it.Rating > 7):
			return fmt.Errorf("%w: %s: rating va de 1 a 7", ErrInvalidInput, it.RuleID)
		case (it.Rating == nil) != it.NotEvaluable:
			return fmt.Errorf("%w: %s: da una calificación 1-7 o marca «no evaluable»", ErrInvalidInput, it.RuleID)
		case blind && it.Verdict != nil:
			return fmt.Errorf("%w: en una campaña ciega no se da veredicto sobre el sistema", ErrInvalidInput)
		case it.Verdict != nil && !slices.Contains(verdicts, *it.Verdict):
			return fmt.Errorf("%w: verdict debe ser uno de %v", ErrInvalidInput, verdicts)
		case len(it.Comment) > 1000:
			return fmt.Errorf("%w: %s: comentario máx. 1000", ErrInvalidInput, it.RuleID)
		}
		seen[it.RuleID] = true
	}
	return nil
}

// --- Campañas Delphi ---

const (
	CampaignOpen   = "open"
	CampaignClosed = "closed"
)

type CampaignInput struct {
	Stroke       StrokeType `json:"stroke"`
	Title        string     `json:"title"`
	RuleIDs      []string   `json:"rule_ids"` // vacío = todas las reglas activas del golpe
	CaseIDs      []string   `json:"case_ids"` // deben estar en el corpus del golpe
	RaterIDs     []string   `json:"rater_ids"`
	MaxRounds    int        `json:"max_rounds"`
	ConsensusPct float64    `json:"consensus_pct"`
	MinRaters    int        `json:"min_raters"`
}

// WithDefaults aplica la definición de consenso por defecto: 70 % a ±1 de la mediana, 3 evaluadores,
// máximo 3 rondas.
func (in CampaignInput) WithDefaults() CampaignInput {
	if in.MaxRounds == 0 {
		in.MaxRounds = 3
	}
	if in.ConsensusPct == 0 {
		in.ConsensusPct = 0.7
	}
	if in.MinRaters == 0 {
		in.MinRaters = 3
	}
	in.Title = strings.TrimSpace(in.Title)
	return in
}

func (in CampaignInput) Validate() error {
	switch {
	case !slices.Contains(strokeTypes, in.Stroke):
		return invalid("stroke", strokeTypes)
	case in.Title == "" || len(in.Title) > 200:
		return fmt.Errorf("%w: title es obligatorio (máx. 200)", ErrInvalidInput)
	case len(in.CaseIDs) < 2 || len(in.CaseIDs) > 500:
		return fmt.Errorf("%w: una campaña tiene entre 2 y 500 casos", ErrInvalidInput)
	case in.MinRaters < 2 || len(in.RaterIDs) < in.MinRaters || len(in.RaterIDs) > 50:
		return fmt.Errorf("%w: hacen falta al menos %d evaluadores (mín. 2, máx. 50)", ErrInvalidInput, max(in.MinRaters, 2))
	case in.MaxRounds < 1 || in.MaxRounds > 5:
		return fmt.Errorf("%w: max_rounds va de 1 a 5", ErrInvalidInput)
	case in.ConsensusPct <= 0.5 || in.ConsensusPct > 1:
		return fmt.Errorf("%w: consensus_pct va de 0.5 a 1", ErrInvalidInput)
	case len(in.RuleIDs) > maxReviewItems:
		return fmt.Errorf("%w: máximo %d reglas", ErrInvalidInput, maxReviewItems)
	}
	for _, id := range slices.Concat(in.CaseIDs, in.RaterIDs) {
		if !IsValidID(id) {
			return fmt.Errorf("%w: id %q inválido", ErrInvalidInput, id)
		}
	}
	for _, id := range in.RuleIDs {
		if !ruleIDPattern.MatchString(id) {
			return fmt.Errorf("%w: rule_id %q inválido", ErrInvalidInput, id)
		}
	}
	if hasDuplicates(in.CaseIDs) || hasDuplicates(in.RaterIDs) || hasDuplicates(in.RuleIDs) {
		return fmt.Errorf("%w: hay ids repetidos", ErrInvalidInput)
	}
	return nil
}

func hasDuplicates(ids []string) bool {
	sorted := slices.Clone(ids)
	slices.Sort(sorted)
	return len(slices.Compact(sorted)) != len(ids)
}

type Campaign struct {
	ID           string
	Stroke       StrokeType
	Title        string
	RuleIDs      []string
	Status       string
	CurrentRound int
	MaxRounds    int
	ConsensusPct float64
	MinRaters    int
	Results      *CampaignResults
	CreatedBy    string
	CreatedAt    time.Time
	ClosedAt     *time.Time
	CaseIDs      []string
	RaterIDs     []string
}

func (c Campaign) IsRater(userID string) bool { return slices.Contains(c.RaterIDs, userID) }

// RuleAgreement es el acuerdo de los evaluadores sobre una regla en una ronda.
type RuleAgreement struct {
	RuleID        string   `json:"rule_id"`
	Alpha         *float64 `json:"alpha"` // nil = indefinido (sin pares o sin variación)
	Band          string   `json:"band,omitempty"`
	Units         int      `json:"units"`          // casos con ≥ 2 calificaciones
	ConsensusRate float64  `json:"consensus_rate"` // fracción de casos con consenso
	NotEvaluable  int      `json:"not_evaluable"`
}

// CampaignResults se congela al cerrar la campaña.
type CampaignResults struct {
	Round int             `json:"round"`
	Rules []RuleAgreement `json:"rules"`
}

// GroundTruth es el patrón oro de un caso para una regla: la mediana de consenso de la última ronda.
type GroundTruth struct {
	AnalysisID string
	RuleID     string
	CampaignID string
	Median     float64
	Label      string
	Consensus  bool
	Raters     int
}

// ReportSummary es lo que las revisiones necesitan del reporte guardado de un análisis.
type ReportSummary struct {
	Stroke    StrokeType
	Fired     map[string]bool // reglas que dispararon (errores y fortalezas)
	Segment   json.RawMessage // fases del golpe, para marcar el video en la vista ciega
	KBVersion string
}

// SummarizeReport lee el reporte de forma tolerante: un reporte raro da un resumen vacío.
func SummarizeReport(a Analysis) ReportSummary {
	var r struct {
		StrokeType string          `json:"stroke_type"`
		Segment    json.RawMessage `json:"segment"`
		Findings   []struct {
			RuleID string `json:"rule_id"`
		} `json:"findings"`
		Strengths []struct {
			RuleID string `json:"rule_id"`
		} `json:"strengths"`
		KBVersion string `json:"knowledge_base_version"`
	}
	_ = json.Unmarshal(a.Report, &r)
	out := ReportSummary{Stroke: StrokeType(r.StrokeType), Fired: map[string]bool{}, Segment: r.Segment, KBVersion: r.KBVersion}
	if out.Stroke == "" {
		out.Stroke = a.Params.StrokeType
	}
	for _, f := range r.Findings {
		out.Fired[f.RuleID] = true
	}
	for _, s := range r.Strengths {
		out.Fired[s.RuleID] = true
	}
	return out
}

// Agreement calcula, para una regla, α ordinal y consenso sobre las revisiones de una ronda.
func Agreement(ruleID string, reviews []CaseReview, pct float64, minRaters int) (RuleAgreement, map[string]Consensus) {
	byCase := map[string][]int{}
	ra := RuleAgreement{RuleID: ruleID}
	for _, r := range reviews {
		for _, it := range r.Items {
			if it.RuleID != ruleID {
				continue
			}
			if it.Rating == nil {
				ra.NotEvaluable++
				continue
			}
			byCase[r.AnalysisID] = append(byCase[r.AnalysisID], *it.Rating)
		}
	}
	units := make([][]float64, 0, len(byCase))
	consensus := make(map[string]Consensus, len(byCase))
	reached := 0
	for caseID, ratings := range byCase {
		vals := make([]float64, len(ratings))
		for i, v := range ratings {
			vals[i] = float64(v)
		}
		units = append(units, vals)
		if len(ratings) >= 2 {
			ra.Units++
		}
		c := ConsensusOf(ratings, pct, minRaters)
		consensus[caseID] = c
		if c.Reached {
			reached++
		}
	}
	if a, ok := KrippendorffAlpha(units, AlphaOrdinal); ok {
		ra.Alpha, ra.Band = &a, AlphaBand(a)
	}
	if len(byCase) > 0 {
		ra.ConsensusRate = float64(reached) / float64(len(byCase))
	}
	return ra, consensus
}
