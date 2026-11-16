package domain

import (
	"encoding/base64"
	"fmt"
	"strings"
	"time"
)

const (
	DefaultPageSize = 20
	MaxPageSize     = 100
)

// Cursor apunta al último elemento visto de un listado ordenado por (created_at, id) descendente.
// Paginar por cursor (y no por offset) mantiene las páginas estables mientras se crean análisis.
type Cursor struct {
	CreatedAt time.Time
	ID        string
}

// ListQuery pide una página de análisis. After nil significa "desde el principio".
type ListQuery struct {
	After *Cursor
	Limit int
}

// Encode produce un token opaco: los clientes no deben interpretarlo.
func (c Cursor) Encode() string {
	raw := c.CreatedAt.UTC().Format(time.RFC3339Nano) + "|" + c.ID
	return base64.RawURLEncoding.EncodeToString([]byte(raw))
}

func DecodeCursor(token string) (Cursor, error) {
	raw, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil {
		return Cursor{}, errInvalidCursor
	}
	ts, id, ok := strings.Cut(string(raw), "|")
	createdAt, err := time.Parse(time.RFC3339Nano, ts)
	if !ok || err != nil || !IsValidID(id) {
		return Cursor{}, errInvalidCursor
	}
	return Cursor{CreatedAt: createdAt, ID: id}, nil
}

// NewListQuery valida los parámetros crudos de la petición y aplica los límites.
func NewListQuery(cursor string, limit int) (ListQuery, error) {
	q := ListQuery{Limit: limit}
	if q.Limit == 0 {
		q.Limit = DefaultPageSize
	}
	if q.Limit < 1 || q.Limit > MaxPageSize {
		return ListQuery{}, fmt.Errorf("%w: limit debe estar entre 1 y %d", ErrInvalidInput, MaxPageSize)
	}
	if cursor != "" {
		c, err := DecodeCursor(cursor)
		if err != nil {
			return ListQuery{}, err
		}
		q.After = &c
	}
	return q, nil
}

var errInvalidCursor = fmt.Errorf("%w: cursor inválido", ErrInvalidInput)
