package domain

import (
	"errors"
	"testing"
	"time"
)

func TestCursorRoundTrip(t *testing.T) {
	c := Cursor{CreatedAt: time.Date(2026, 11, 15, 10, 0, 0, 123456000, time.UTC), ID: "3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10"}
	got, err := DecodeCursor(c.Encode())
	if err != nil || !got.CreatedAt.Equal(c.CreatedAt) || got.ID != c.ID {
		t.Fatalf("got=%+v err=%v", got, err)
	}
}

func TestDecodeCursorRejectsGarbage(t *testing.T) {
	for _, token := range []string{"%%%", "bm9wZQ", Cursor{CreatedAt: time.Now(), ID: "no-uuid"}.Encode()} {
		if _, err := DecodeCursor(token); !errors.Is(err, ErrInvalidInput) {
			t.Errorf("%q: se esperaba ErrInvalidInput, got %v", token, err)
		}
	}
}

func TestNewListQuery(t *testing.T) {
	q, err := NewListQuery("", 0)
	if err != nil || q.Limit != DefaultPageSize || q.After != nil {
		t.Fatalf("defaults: %+v %v", q, err)
	}
	for _, limit := range []int{-1, MaxPageSize + 1} {
		if _, err := NewListQuery("", limit); !errors.Is(err, ErrInvalidInput) {
			t.Errorf("limit=%d debería rechazarse", limit)
		}
	}
	c := Cursor{CreatedAt: time.Now().UTC(), ID: "3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10"}
	if q, err := NewListQuery(c.Encode(), 5); err != nil || q.After == nil || q.After.ID != c.ID || q.Limit != 5 {
		t.Fatalf("con cursor: %+v %v", q, err)
	}
}
