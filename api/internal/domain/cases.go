package domain

import (
	"fmt"
	"slices"
)

// CaseFilter pide una página de "casos": análisis completados cuyo dueño permitió la revisión
// experta. Los expertos los usan para comprobar sobre el video si una regla acierta.
type CaseFilter struct {
	Stroke StrokeType // vacío = todos los golpes
	RuleID string     // vacío = todos; si no, casos donde la regla disparó (error o punto fuerte)
	Query  ListQuery
}

const maxRuleIDLength = 100

// NewCaseFilter valida los parámetros crudos de la petición.
func NewCaseFilter(stroke, ruleID, cursor string, limit int) (CaseFilter, error) {
	q, err := NewListQuery(cursor, limit)
	if err != nil {
		return CaseFilter{}, err
	}
	s := StrokeType(stroke)
	if s != StrokeAuto && !slices.Contains(strokeTypes, s) {
		return CaseFilter{}, invalid("stroke", strokeTypes)
	}
	if len(ruleID) > maxRuleIDLength {
		return CaseFilter{}, fmt.Errorf("%w: rule_id demasiado largo", ErrInvalidInput)
	}
	return CaseFilter{Stroke: s, RuleID: ruleID, Query: q}, nil
}
