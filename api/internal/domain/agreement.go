package domain

import (
	"math"
	"slices"
	"sort"
)

// Estadística de acuerdo entre expertos y de acierto del sistema. Funciones puras: el servicio las
// aplica sobre las revisiones guardadas; los tests las verifican con ejemplos publicados.

// AlphaMetric es la función de distancia del α de Krippendorff.
type AlphaMetric string

const (
	AlphaNominal  AlphaMetric = "nominal"
	AlphaOrdinal  AlphaMetric = "ordinal" // calificaciones 1-7: lo que usamos por defecto
	AlphaInterval AlphaMetric = "interval"
)

// Umbrales de Krippendorff (Content Analysis, 2004/2019): ≥ 0,800 para sacar conclusiones;
// 0,667-0,800 solo para conclusiones tentativas; por debajo, los datos no sirven para calibrar.
const (
	AlphaReliable  = 0.800
	AlphaTentative = 0.667
)

// AlphaBand clasifica un α según los umbrales anteriores.
func AlphaBand(alpha float64) string {
	switch {
	case alpha >= AlphaReliable:
		return "reliable"
	case alpha >= AlphaTentative:
		return "tentative"
	default:
		return "unusable"
	}
}

// KrippendorffAlpha calcula α sobre unidades (casos) con los valores que les dieron los evaluadores.
// Admite datos faltantes: cada unidad lleva solo los valores emitidos; las que tienen menos de dos se
// ignoran. ok = false si no hay datos emparejables o no hay variación (α indefinido).
func KrippendorffAlpha(units [][]float64, metric AlphaMetric) (alpha float64, ok bool) {
	values := distinctValues(units)
	if len(values) == 0 {
		return 0, false
	}
	index := make(map[float64]int, len(values))
	for i, v := range values {
		index[v] = i
	}
	k := len(values)
	// Matriz de coincidencias: cada par ordenado (i≠j) de valores de una unidad suma 1/(m_u−1).
	o := make([][]float64, k)
	for i := range o {
		o[i] = make([]float64, k)
	}
	for _, u := range units {
		m := len(u)
		if m < 2 {
			continue
		}
		w := 1 / float64(m-1)
		for i := range u {
			for j := range u {
				if i != j {
					o[index[u[i]]][index[u[j]]] += w
				}
			}
		}
	}
	nc := make([]float64, k)
	n := 0.0
	for c := range k {
		for kk := range k {
			nc[c] += o[c][kk]
		}
		n += nc[c]
	}
	if n <= 1 {
		return 0, false
	}
	var observed, expected float64
	for c := range k {
		for kk := range k {
			d := delta2(metric, values, nc, c, kk)
			observed += o[c][kk] * d
			expected += nc[c] * nc[kk] * d
		}
	}
	if expected == 0 {
		return 0, false
	}
	return 1 - (n-1)*observed/expected, true
}

func distinctValues(units [][]float64) []float64 {
	var vals []float64
	for _, u := range units {
		if len(u) < 2 {
			continue
		}
		vals = append(vals, u...)
	}
	slices.Sort(vals)
	return slices.Compact(vals)
}

func delta2(metric AlphaMetric, values, nc []float64, c, k int) float64 {
	if c == k {
		return 0
	}
	switch metric {
	case AlphaInterval:
		d := values[c] - values[k]
		return d * d
	case AlphaOrdinal:
		lo, hi := min(c, k), max(c, k)
		sum := 0.0
		for g := lo; g <= hi; g++ {
			sum += nc[g]
		}
		d := sum - (nc[c]+nc[k])/2
		return d * d
	default:
		return 1
	}
}

// --- Consenso Delphi ---

// Consensus resume las calificaciones (1-7) que recibió un ítem (caso × regla) en una ronda.
type Consensus struct {
	N         int     `json:"n"`
	Median    float64 `json:"median"`
	Q1        float64 `json:"q1"`
	Q3        float64 `json:"q3"`
	WithinOne float64 `json:"within_one"` // fracción de calificaciones a ±1 de la mediana
	Reached   bool    `json:"reached"`
	Counts    [7]int  `json:"counts"` // distribución 1..7 (feedback anónimo de la ronda siguiente)
}

// ConsensusOf aplica la definición fijada a priori: hay consenso si al menos `pct` de las
// calificaciones caen a ±1 de la mediana y hay al menos `minRaters` calificaciones.
func ConsensusOf(ratings []int, pct float64, minRaters int) Consensus {
	c := Consensus{N: len(ratings)}
	if len(ratings) == 0 {
		return c
	}
	sorted := slices.Clone(ratings)
	sort.Ints(sorted)
	c.Median = quantile(sorted, 0.5)
	c.Q1, c.Q3 = quantile(sorted, 0.25), quantile(sorted, 0.75)
	near := 0
	for _, r := range sorted {
		if r >= 1 && r <= 7 {
			c.Counts[r-1]++
		}
		if math.Abs(float64(r)-c.Median) <= 1 {
			near++
		}
	}
	c.WithinOne = float64(near) / float64(len(sorted))
	c.Reached = len(sorted) >= minRaters && c.WithinOne >= pct
	return c
}

// quantile con interpolación lineal (tipo 7, el de R y numpy por defecto).
func quantile(sorted []int, q float64) float64 {
	if len(sorted) == 1 {
		return float64(sorted[0])
	}
	pos := q * float64(len(sorted)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	frac := pos - float64(lo)
	return float64(sorted[lo]) + frac*float64(sorted[hi]-sorted[lo])
}

// --- De calificación a etiqueta ---

const (
	LabelPresent       = "present"
	LabelAbsent        = "absent"
	LabelIndeterminate = "indeterminate"
)

// LabelFor traduce una calificación (o mediana) 1-7 del aspecto a "la regla debería dispararse".
// En un error, ≤ 3 (deficiente) = presente y ≥ 5 = ausente; en una fortaleza, al revés. El 4 (y las
// medianas intermedias) es indeterminado y no cuenta para precisión/recall.
func LabelFor(score float64, polarity string) string {
	low, high := score <= 3, score >= 5
	switch {
	case !low && !high:
		return LabelIndeterminate
	case polarity == PolarityStrength && high, polarity != PolarityStrength && low:
		return LabelPresent
	default:
		return LabelAbsent
	}
}

// DerivedVerdict compara la etiqueta del experto con lo que hizo el sistema (campañas ciegas).
func DerivedVerdict(label string, systemFired bool) Verdict {
	switch {
	case label == LabelIndeterminate:
		return VerdictUnsure
	case (label == LabelPresent) == systemFired:
		return VerdictCorrect
	case systemFired:
		return VerdictFalsePositive
	default:
		return VerdictMissed
	}
}

// --- Acierto del sistema ---

type Confusion struct {
	TP int `json:"tp"`
	FP int `json:"fp"`
	FN int `json:"fn"`
	TN int `json:"tn"`
}

func (c *Confusion) Add(present, fired bool) {
	switch {
	case present && fired:
		c.TP++
	case !present && fired:
		c.FP++
	case present && !fired:
		c.FN++
	default:
		c.TN++
	}
}

func (c Confusion) N() int { return c.TP + c.FP + c.FN + c.TN }

// Rate es una proporción con su intervalo de Wilson al 95 % (nil si no hay datos).
type Rate struct {
	Value float64 `json:"value"`
	Low   float64 `json:"low"`
	High  float64 `json:"high"`
	N     int     `json:"n"`
}

func rateOf(k, n int) *Rate {
	if n == 0 {
		return nil
	}
	lo, hi := Wilson(k, n)
	return &Rate{Value: float64(k) / float64(n), Low: lo, High: hi, N: n}
}

func (c Confusion) Precision() *Rate { return rateOf(c.TP, c.TP+c.FP) }
func (c Confusion) Recall() *Rate    { return rateOf(c.TP, c.TP+c.FN) }

// F1 es nil si precisión o recall no están definidos.
func (c Confusion) F1() *float64 {
	p, r := c.Precision(), c.Recall()
	if p == nil || r == nil || p.Value+r.Value == 0 {
		return nil
	}
	f := 2 * p.Value * r.Value / (p.Value + r.Value)
	return &f
}

// Wilson devuelve el intervalo de confianza al 95 % de k éxitos en n intentos.
func Wilson(k, n int) (low, high float64) {
	if n == 0 {
		return 0, 0
	}
	const z = 1.959964
	nf := float64(n)
	p := float64(k) / nf
	den := 1 + z*z/nf
	center := (p + z*z/(2*nf)) / den
	half := z / den * math.Sqrt(p*(1-p)/nf+z*z/(4*nf*nf))
	return math.Max(0, center-half), math.Min(1, center+half)
}
