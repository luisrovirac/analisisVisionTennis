package domain

import (
	"errors"
	"testing"
	"time"
)

func TestParamsWithDefaultsFillsOptionalFields(t *testing.T) {
	p := AnalysisParams{StrokeType: StrokeForehand}.WithDefaults()
	if p.Handedness != HandRight || p.CameraView != ViewSide {
		t.Fatalf("defaults no aplicados: %+v", p)
	}
}

func TestParamsValidate(t *testing.T) {
	valid := AnalysisParams{StrokeType: StrokeForehand, Handedness: HandLeft, CameraView: ViewFront}
	cases := map[string]struct {
		params  AnalysisParams
		wantErr bool
	}{
		"válido":           {valid, false},
		"golpe inválido":   {AnalysisParams{StrokeType: "smash", Handedness: HandLeft, CameraView: ViewSide}, true},
		"mano inválida":    {AnalysisParams{StrokeType: StrokeServe, Handedness: "both", CameraView: ViewSide}, true},
		"vista inválida":   {AnalysisParams{StrokeType: StrokeServe, Handedness: HandLeft, CameraView: "top"}, true},
		"golpe automático": {AnalysisParams{Handedness: HandLeft, CameraView: ViewSide}, false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			err := tc.params.Validate()
			if tc.wantErr != (err != nil) {
				t.Fatalf("err=%v, wantErr=%v", err, tc.wantErr)
			}
			if err != nil && !errors.Is(err, ErrInvalidInput) {
				t.Fatalf("el error debe envolver ErrInvalidInput: %v", err)
			}
		})
	}
}

func TestNewAnalysisStartsAwaitingUpload(t *testing.T) {
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	a := NewAnalysis("abc", "owner-1", AnalysisParams{StrokeType: StrokeForehand}, now)
	if a.Status != StatusAwaitingUpload || a.VideoKey != "videos/abc" || !a.CreatedAt.Equal(now) ||
		a.UserID == nil || *a.UserID != "owner-1" {
		t.Fatalf("estado inicial inesperado: %+v", a)
	}
}

func TestIsValidID(t *testing.T) {
	if !IsValidID("3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10") {
		t.Fatal("uuid válido rechazado")
	}
	if IsValidID("no-es-uuid") {
		t.Fatal("id inválido aceptado")
	}
}
