package domain

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

// Base de conocimiento: espejo de contracts/knowledge_base.schema.json y de
// worker/src/tennis_worker/pipeline/knowledge/models.py. La API valida al guardar, al enviar y al
// aprobar; el worker vuelve a validar al cargar (defensa en profundidad).

// Threshold es un número o un rango [min, max] (solo para el operador `between`).
type Threshold struct {
	Value   float64
	Low     float64
	High    float64
	IsRange bool
}

func (t *Threshold) UnmarshalJSON(b []byte) error {
	b = bytes.TrimSpace(b)
	if len(b) > 0 && b[0] == '[' {
		var pair []float64
		if err := json.Unmarshal(b, &pair); err != nil || len(pair) != 2 {
			return errors.New("value debe ser un número o [min, max]")
		}
		*t = Threshold{Low: pair[0], High: pair[1], IsRange: true}
		return nil
	}
	var v float64
	if err := json.Unmarshal(b, &v); err != nil {
		return errors.New("value debe ser un número o [min, max]")
	}
	*t = Threshold{Value: v}
	return nil
}

func (t Threshold) MarshalJSON() ([]byte, error) {
	if t.IsRange {
		return json.Marshal([2]float64{t.Low, t.High})
	}
	return json.Marshal(t.Value)
}

type Condition struct {
	Metric string    `json:"metric"`
	Op     string    `json:"op"`
	Value  Threshold `json:"value"`
}

type ConditionGroup struct {
	All []Condition `json:"all,omitempty"`
	Any []Condition `json:"any,omitempty"`
}

func (g ConditionGroup) Conditions() []Condition {
	if len(g.All) > 0 {
		return g.All
	}
	return g.Any
}

const (
	PolarityError    = "error"
	PolarityStrength = "strength"
)

// Rule es una regla de error o de punto fuerte. Validated solo lo fija la API al aprobar.
type Rule struct {
	ID            string         `json:"id"`
	Title         string         `json:"title"`
	Category      string         `json:"category"`
	Polarity      string         `json:"polarity,omitempty"`
	Impact        int            `json:"impact"`
	Difficulty    int            `json:"difficulty"`
	When          ConditionGroup `json:"when"`
	Explanation   string         `json:"explanation"`
	Correction    string         `json:"correction,omitempty"`
	WhyItMatters  string         `json:"why_it_matters,omitempty"`
	HowToMaintain string         `json:"how_to_maintain,omitempty"`
	Drills        []string       `json:"drills,omitempty"`
	CausedBy      []string       `json:"caused_by,omitempty"`
	SuppressedBy  []string       `json:"suppressed_by,omitempty"`
	References    []string       `json:"references,omitempty"`
	Validated     bool           `json:"validated,omitempty"`
}

func (r Rule) IsStrength() bool { return r.Polarity == PolarityStrength }

type KnowledgeBase struct {
	Stroke      StrokeType `json:"stroke"`
	Version     string     `json:"version"`
	ValidatedBy *string    `json:"validated_by,omitempty"`
	Rules       []Rule     `json:"rules"`
}

// MetricInfo es una entrada del catálogo que publica el worker: lo único que una regla puede citar.
type MetricInfo struct {
	Stroke StrokeType `json:"stroke"`
	ID     string     `json:"id"`
	Label  string     `json:"label"`
	Unit   string     `json:"unit"`
}

var (
	ruleIDPattern = regexp.MustCompile(`^[A-Z0-9][A-Z0-9-]{1,39}$`)
	categories    = []string{"fundamental", "sincronizacion"}
	polarities    = []string{PolarityError, PolarityStrength}
	operators     = []string{"lt", "lte", "gt", "gte", "eq", "between"}
)

const (
	maxTextLen   = 2000
	maxListItems = 20
)

// Validate comprueba la forma de la regla (sin mirar el resto de la base ni el catálogo).
func (r Rule) Validate() error {
	if errs := r.problems(); len(errs) > 0 {
		return fmt.Errorf("%w: %w", ErrInvalidInput, errors.Join(errs...))
	}
	return nil
}

func (r Rule) problems() []error {
	var errs []error
	add := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	if !ruleIDPattern.MatchString(r.ID) {
		add("id %q: usa mayúsculas, números y guiones (p. ej. FH-PREP-002)", r.ID)
	}
	if strings.TrimSpace(r.Title) == "" || len(r.Title) > 200 {
		add("%s: title es obligatorio (máx. 200)", r.ID)
	}
	if !slices.Contains(categories, r.Category) {
		add("%s: category debe ser uno de %v", r.ID, categories)
	}
	if !slices.Contains(polarities, r.Polarity) {
		add("%s: polarity debe ser uno de %v", r.ID, polarities)
	}
	if r.Impact < 1 || r.Impact > 5 || r.Difficulty < 1 || r.Difficulty > 5 {
		add("%s: impact y difficulty van de 1 a 5", r.ID)
	}
	errs = append(errs, r.validateWhen()...)
	return append(errs, r.validateTexts()...)
}

func (r Rule) validateWhen() []error {
	if (len(r.When.All) > 0) == (len(r.When.Any) > 0) {
		return []error{fmt.Errorf("%s: when necesita exactamente uno de 'all' o 'any', con al menos una condición", r.ID)}
	}
	var errs []error
	for _, c := range r.When.Conditions() {
		switch {
		case strings.TrimSpace(c.Metric) == "":
			errs = append(errs, fmt.Errorf("%s: condición sin métrica", r.ID))
		case !slices.Contains(operators, c.Op):
			errs = append(errs, fmt.Errorf("%s: op %q no es uno de %v", r.ID, c.Op, operators))
		case (c.Op == "between") != c.Value.IsRange:
			errs = append(errs, fmt.Errorf("%s: %q requiere %s", r.ID, c.Op, map[bool]string{true: "[min, max]", false: "un número"}[c.Op == "between"]))
		case c.Value.IsRange && c.Value.Low > c.Value.High:
			errs = append(errs, fmt.Errorf("%s: between requiere min <= max", r.ID))
		}
	}
	return errs
}

func (r Rule) validateTexts() []error {
	var errs []error
	if strings.TrimSpace(r.Explanation) == "" {
		errs = append(errs, fmt.Errorf("%s: explanation es obligatorio", r.ID))
	}
	if r.IsStrength() {
		if strings.TrimSpace(r.WhyItMatters) == "" {
			errs = append(errs, fmt.Errorf("%s: una fortaleza necesita why_it_matters", r.ID))
		}
		if len(r.CausedBy) > 0 {
			errs = append(errs, fmt.Errorf("%s: caused_by solo aplica a errores", r.ID))
		}
	} else {
		if strings.TrimSpace(r.Correction) == "" {
			errs = append(errs, fmt.Errorf("%s: un error necesita correction", r.ID))
		}
		if len(r.SuppressedBy) > 0 {
			errs = append(errs, fmt.Errorf("%s: suppressed_by solo aplica a fortalezas", r.ID))
		}
	}
	for _, text := range []string{r.Explanation, r.Correction, r.WhyItMatters, r.HowToMaintain} {
		if len(text) > maxTextLen {
			errs = append(errs, fmt.Errorf("%s: los textos tienen un máximo de %d caracteres", r.ID, maxTextLen))
			break
		}
	}
	for _, list := range [][]string{r.Drills, r.CausedBy, r.SuppressedBy, r.References} {
		if len(list) > maxListItems {
			errs = append(errs, fmt.Errorf("%s: máximo %d elementos por lista", r.ID, maxListItems))
			break
		}
	}
	return errs
}

// Normalize aplica los valores por defecto del esquema (Postel: tolerante en la entrada).
func (r Rule) Normalize() Rule {
	if r.Polarity == "" {
		r.Polarity = PolarityError
	}
	r.Title, r.Explanation = strings.TrimSpace(r.Title), strings.TrimSpace(r.Explanation)
	r.Correction, r.WhyItMatters = strings.TrimSpace(r.Correction), strings.TrimSpace(r.WhyItMatters)
	r.HowToMaintain = strings.TrimSpace(r.HowToMaintain)
	return r
}

// Validate comprueba la base completa: cada regla, ids únicos, referencias entre reglas y que cada
// métrica exista en el catálogo del golpe (si no, el worker la rechazaría al cargarla).
func (kb KnowledgeBase) Validate(catalog []MetricInfo) error {
	known := map[string]bool{}
	for _, m := range catalog {
		if m.Stroke == kb.Stroke {
			known[m.ID] = true
		}
	}
	var errs []error
	ids, errorIDs := map[string]bool{}, map[string]bool{}
	for _, r := range kb.Rules {
		if ids[r.ID] {
			errs = append(errs, fmt.Errorf("id duplicado %s", r.ID))
		}
		ids[r.ID] = true
		if !r.IsStrength() {
			errorIDs[r.ID] = true
		}
	}
	for _, r := range kb.Rules {
		errs = append(errs, r.problems()...)
		for _, c := range r.When.Conditions() {
			if !known[c.Metric] {
				errs = append(errs, fmt.Errorf("%s: la métrica %q no existe para %s", r.ID, c.Metric, kb.Stroke))
			}
		}
		for _, ref := range slices.Concat(r.CausedBy, r.SuppressedBy) {
			if ref == r.ID || !errorIDs[ref] {
				errs = append(errs, fmt.Errorf("%s: referencia %q, que no es otro error de esta base", r.ID, ref))
			}
		}
	}
	if len(errs) == 0 {
		return nil
	}
	return fmt.Errorf("%w: %w", ErrInvalidInput, errors.Join(errs...))
}

// NextVersion sube el minor (0.2.0 → 0.3.0): cada aprobación publica una versión nueva.
func NextVersion(version string) (string, error) {
	parts := strings.Split(version, ".")
	if len(parts) != 3 {
		return "", fmt.Errorf("versión %q no es semver", version)
	}
	major, err1 := strconv.Atoi(parts[0])
	minor, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil {
		return "", fmt.Errorf("versión %q no es semver", version)
	}
	return fmt.Sprintf("%d.%d.0", major, minor+1), nil
}

// --- Propuestas ---

type ChangeOp string

const (
	OpUpsertRule ChangeOp = "upsert_rule" // crea la regla o reemplaza la que tiene ese id
	OpRemoveRule ChangeOp = "remove_rule"
)

type Change struct {
	Op     ChangeOp `json:"op"`
	Rule   *Rule    `json:"rule,omitempty"`
	RuleID string   `json:"rule_id,omitempty"`
}

// TargetID es el id de la regla afectada por el cambio.
func (c Change) TargetID() string {
	if c.Op == OpUpsertRule && c.Rule != nil {
		return c.Rule.ID
	}
	return c.RuleID
}

const maxChanges = 30

// ApplyChanges devuelve la base resultante sin modificar la original. Con validated=true (al aprobar)
// las reglas creadas o editadas quedan marcadas como validadas; el experto nunca puede marcarlas.
func ApplyChanges(kb KnowledgeBase, changes []Change, validated bool) (KnowledgeBase, error) {
	out := kb
	out.Rules = slices.Clone(kb.Rules)
	for _, c := range changes {
		switch c.Op {
		case OpUpsertRule:
			rule := c.Rule.Normalize()
			rule.Validated = validated
			if i := slices.IndexFunc(out.Rules, func(r Rule) bool { return r.ID == rule.ID }); i >= 0 {
				out.Rules[i] = rule
			} else {
				out.Rules = append(out.Rules, rule)
			}
		case OpRemoveRule:
			i := slices.IndexFunc(out.Rules, func(r Rule) bool { return r.ID == c.RuleID })
			if i < 0 {
				return KnowledgeBase{}, fmt.Errorf("%w: no existe la regla %s para retirarla", ErrInvalidInput, c.RuleID)
			}
			out.Rules = slices.Delete(out.Rules, i, i+1)
		}
	}
	return out, nil
}

type ProposalStatus string

const (
	ProposalDraft            ProposalStatus = "draft"
	ProposalSubmitted        ProposalStatus = "submitted"
	ProposalChangesRequested ProposalStatus = "changes_requested"
	ProposalApproved         ProposalStatus = "approved"
	ProposalRejected         ProposalStatus = "rejected"
	ProposalWithdrawn        ProposalStatus = "withdrawn"
)

// ProposalInput es lo que escribe el experto.
type ProposalInput struct {
	Stroke    StrokeType `json:"stroke"`
	Title     string     `json:"title"`
	Rationale string     `json:"rationale"` // por qué: el validador decide con esto
	Evidence  string     `json:"evidence"`  // referencias, observaciones, casos
	Changes   []Change   `json:"changes"`
}

// Validate comprueba la forma de la propuesta. Contra la base activa se valida al enviar y al aprobar.
func (in ProposalInput) Validate() error {
	if !slices.Contains(strokeTypes, in.Stroke) {
		return invalid("stroke", strokeTypes)
	}
	if strings.TrimSpace(in.Title) == "" || len(in.Title) > 200 {
		return fmt.Errorf("%w: title es obligatorio (máx. 200)", ErrInvalidInput)
	}
	if strings.TrimSpace(in.Rationale) == "" || len(in.Rationale) > 4000 || len(in.Evidence) > 4000 {
		return fmt.Errorf("%w: rationale es obligatorio (máx. 4000); evidence máx. 4000", ErrInvalidInput)
	}
	if len(in.Changes) == 0 || len(in.Changes) > maxChanges {
		return fmt.Errorf("%w: una propuesta tiene entre 1 y %d cambios", ErrInvalidInput, maxChanges)
	}
	seen := map[string]bool{}
	for _, c := range in.Changes {
		if err := c.validate(); err != nil {
			return err
		}
		if seen[c.TargetID()] {
			return fmt.Errorf("%w: la regla %s aparece dos veces en la propuesta", ErrInvalidInput, c.TargetID())
		}
		seen[c.TargetID()] = true
	}
	return nil
}

func (c Change) validate() error {
	switch c.Op {
	case OpUpsertRule:
		if c.Rule == nil {
			return fmt.Errorf("%w: upsert_rule necesita rule", ErrInvalidInput)
		}
		return c.Rule.Normalize().Validate()
	case OpRemoveRule:
		if !ruleIDPattern.MatchString(c.RuleID) {
			return fmt.Errorf("%w: remove_rule necesita rule_id", ErrInvalidInput)
		}
		return nil
	default:
		return fmt.Errorf("%w: op debe ser upsert_rule o remove_rule", ErrInvalidInput)
	}
}

// Sanitized normaliza las reglas y borra cualquier `validated` que el experto haya intentado fijar.
func (in ProposalInput) Sanitized() ProposalInput {
	out := in
	out.Title, out.Rationale, out.Evidence = strings.TrimSpace(in.Title), strings.TrimSpace(in.Rationale), strings.TrimSpace(in.Evidence)
	out.Changes = make([]Change, len(in.Changes))
	for i, c := range in.Changes {
		if c.Rule != nil {
			rule := c.Rule.Normalize()
			rule.Validated = false
			c.Rule = &rule
		}
		out.Changes[i] = c
	}
	return out
}

type Proposal struct {
	ID         string
	AuthorID   string
	AuthorName string // nombre profesional o email; solo dentro del portal
	ProposalInput
	Status        ProposalStatus
	BaseReleaseID *string // versión activa sobre la que se envió
	DecidedBy     *string
	DeciderName   string
	DecidedAt     *time.Time
	DecisionNote  string
	ReleaseID     *string // versión publicada al aprobar
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

// Editable: el autor puede cambiarla mientras no esté en revisión ni decidida.
func (p Proposal) Editable() bool {
	return p.Status == ProposalDraft || p.Status == ProposalChangesRequested
}

type Comment struct {
	ID         string
	ProposalID string
	AuthorID   string
	AuthorName string
	Body       string
	CreatedAt  time.Time
}

const (
	ReleaseValid   = "valid"
	ReleaseInvalid = "invalid" // el worker la rechazó al cargarla
)

// Release es una versión publicada e inmutable de la base de un golpe.
type Release struct {
	ID            string
	Stroke        StrokeType
	Version       string
	KB            KnowledgeBase
	Changelog     string
	ProposalID    *string
	ProposedBy    *string
	ValidatedBy   *string
	Active        bool
	Status        string
	InvalidReason *string
	CreatedAt     time.Time
}

type MetricRequest struct {
	ID          string
	AuthorID    string
	AuthorName  string
	Stroke      StrokeType
	Name        string
	Description string
	Status      string // open | planned | done | declined
	CreatedAt   time.Time
}

func (m MetricRequest) Validate() error {
	if !slices.Contains(strokeTypes, m.Stroke) {
		return invalid("stroke", strokeTypes)
	}
	if strings.TrimSpace(m.Name) == "" || len(m.Name) > 200 || strings.TrimSpace(m.Description) == "" || len(m.Description) > 4000 {
		return fmt.Errorf("%w: name (máx. 200) y description (máx. 4000) son obligatorios", ErrInvalidInput)
	}
	return nil
}

// --- Previsualización de impacto: qué haría una regla sobre análisis reales ---

// MetricSample son las métricas de un análisis ya completado (de report.metrics).
type MetricSample map[string]struct{ Value, Confidence float64 }

const eqTolerance = 1e-6

// Evaluate replica al motor del worker: la regla solo cuenta si todas sus métricas son fiables.
func (r Rule) Evaluate(m MetricSample, minConfidence float64) (measurable, fired bool) {
	conds := r.When.Conditions()
	results := make([]bool, 0, len(conds))
	for _, c := range conds {
		v, ok := m[c.Metric]
		if !ok || v.Confidence < minConfidence {
			return false, false
		}
		results = append(results, holds(c, v.Value))
	}
	if len(r.When.All) > 0 {
		return true, !slices.Contains(results, false)
	}
	return true, slices.Contains(results, true)
}

func holds(c Condition, v float64) bool {
	t := c.Value
	switch c.Op {
	case "lt":
		return v < t.Value
	case "lte":
		return v <= t.Value
	case "gt":
		return v > t.Value
	case "gte":
		return v >= t.Value
	case "eq":
		return math.Abs(v-t.Value) <= eqTolerance
	case "between":
		return t.Low <= v && v <= t.High
	}
	return false
}

// RuleCount: en cuántos de los análisis medibles se dispararía la regla.
type RuleCount struct {
	Fired      int `json:"fired"`
	Measurable int `json:"measurable"`
}

func CountRule(r Rule, samples []MetricSample, minConfidence float64) RuleCount {
	var c RuleCount
	for _, s := range samples {
		measurable, fired := r.Evaluate(s, minConfidence)
		if measurable {
			c.Measurable++
		}
		if fired {
			c.Fired++
		}
	}
	return c
}
