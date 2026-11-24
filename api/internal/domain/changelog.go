package domain

import (
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
)

// Bump es el tipo de salto semver de una versión publicada.
type Bump string

const (
	BumpMajor Bump = "major" // se retira una regla o cambia su polaridad: cambia qué significa un hallazgo
	BumpMinor Bump = "minor" // regla nueva o cambian condiciones/umbrales/vistas: cambia cuándo dispara
	BumpPatch Bump = "patch" // solo textos, drills, referencias, evidencia o estado de validación
)

var bumpRank = map[Bump]int{BumpPatch: 0, BumpMinor: 1, BumpMajor: 2}

// ThresholdChange describe cómo cambió una condición (texto legible: "knee_angle_min gt 160 → gt 165").
type ThresholdChange struct {
	Metric string `json:"metric"`
	Before string `json:"before,omitempty"`
	After  string `json:"after,omitempty"`
}

// ChangelogEntry es un cambio de una versión: qué regla, qué campos y cómo cambiaron los umbrales.
// La autoría (propuesta, autor, validador, motivo) está en la propia versión.
type ChangelogEntry struct {
	RuleID     string            `json:"rule_id"`
	Action     string            `json:"action"` // added | modified | removed
	Fields     []string          `json:"fields,omitempty"`
	Thresholds []ThresholdChange `json:"thresholds,omitempty"`
	Bump       Bump              `json:"bump"`
}

// whenFields cambian cuándo dispara la regla (minor); el resto son de contenido (patch).
var whenFields = []string{"when", "views"}

// Changelog compara la base activa con los cambios de la propuesta y decide el salto semver.
func Changelog(active KnowledgeBase, changes []Change) ([]ChangelogEntry, Bump) {
	entries := make([]ChangelogEntry, 0, len(changes))
	overall := BumpPatch
	for _, c := range changes {
		var before *Rule
		if i := slices.IndexFunc(active.Rules, func(r Rule) bool { return r.ID == c.TargetID() }); i >= 0 {
			before = &active.Rules[i]
		}
		e := entryFor(before, c)
		if bumpRank[e.Bump] > bumpRank[overall] {
			overall = e.Bump
		}
		entries = append(entries, e)
	}
	return entries, overall
}

func entryFor(before *Rule, c Change) ChangelogEntry {
	switch {
	case c.Op == OpRemoveRule:
		return ChangelogEntry{RuleID: c.RuleID, Action: "removed", Bump: BumpMajor,
			Thresholds: conditionDiff(before, nil)}
	case before == nil:
		after := c.Rule.Normalize()
		return ChangelogEntry{RuleID: after.ID, Action: "added", Bump: BumpMinor, Thresholds: conditionDiff(nil, &after)}
	}
	after := c.Rule.Normalize()
	prev := before.Normalize()
	fields := changedFields(prev, after)
	e := ChangelogEntry{RuleID: after.ID, Action: "modified", Fields: fields, Bump: BumpPatch,
		Thresholds: conditionDiff(&prev, &after)}
	switch {
	case slices.Contains(fields, "polarity"):
		e.Bump = BumpMajor
	case slices.ContainsFunc(fields, func(f string) bool { return slices.Contains(whenFields, f) }):
		e.Bump = BumpMinor
	}
	return e
}

// changedFields lista los campos JSON que difieren (se ignoran los que fija la aprobación).
func changedFields(a, b Rule) []string {
	am, bm := asMap(a), asMap(b)
	var out []string
	for _, k := range slices.Sorted(keys(am, bm)) {
		if k == "validated" || k == "validation" || k == "validation_status" {
			continue
		}
		if !reflect.DeepEqual(am[k], bm[k]) {
			out = append(out, k)
		}
	}
	return out
}

func asMap(r Rule) map[string]any {
	raw, _ := json.Marshal(r)
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	return m
}

func keys(maps ...map[string]any) func(func(string) bool) {
	return func(yield func(string) bool) {
		seen := map[string]bool{}
		for _, m := range maps {
			for k := range m {
				if !seen[k] {
					seen[k] = true
					if !yield(k) {
						return
					}
				}
			}
		}
	}
}

func conditionDiff(before, after *Rule) []ThresholdChange {
	describe := func(r *Rule) map[string]string {
		out := map[string]string{}
		if r == nil {
			return out
		}
		for _, c := range r.When.Conditions() {
			out[c.Metric] = fmt.Sprintf("%s %s", c.Op, thresholdText(c.Value))
		}
		return out
	}
	b, a := describe(before), describe(after)
	var out []ThresholdChange
	for _, m := range slices.Sorted(func(yield func(string) bool) {
		for k := range b {
			if !yield(k) {
				return
			}
		}
		for k := range a {
			if _, dup := b[k]; !dup && !yield(k) {
				return
			}
		}
	}) {
		if b[m] != a[m] {
			out = append(out, ThresholdChange{Metric: m, Before: b[m], After: a[m]})
		}
	}
	return out
}

func thresholdText(t Threshold) string {
	if t.IsRange {
		return fmt.Sprintf("[%g, %g]", t.Low, t.High)
	}
	return fmt.Sprintf("%g", t.Value)
}
