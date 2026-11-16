package queue

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"tennisvision/api/internal/domain"
)

// El mismo ejemplo lo valida el worker contra el JSON Schema (worker/tests/test_contracts.py):
// así el contrato se comprueba desde los dos lados con un único fixture.
const jobFixture = "../../../contracts/fixtures/job_message.example.json"

func TestJobMessageMatchesContractFixture(t *testing.T) {
	const id = "3f1c9a52-6b1e-4c7d-9a0e-2d5b8f4e7a10"
	a := domain.Analysis{
		ID:       id,
		VideoKey: "videos/" + id,
		Params:   domain.AnalysisParams{StrokeType: domain.StrokeForehand, Handedness: domain.HandLeft, CameraView: domain.ViewSide},
	}
	raw, err := json.Marshal(NewJobMessage(a))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := decode(t, raw), decode(t, readFile(t, jobFixture)); !reflect.DeepEqual(got, want) {
		t.Errorf("JobMessage no coincide con el contrato:\n got=%v\nwant=%v", got, want)
	}
}

func TestStreamValuesCarryTraceContextOutsidePayload(t *testing.T) {
	otel.SetTextMapPropagator(propagation.TraceContext{})
	ctx, span := sdktrace.NewTracerProvider().Tracer("test").Start(context.Background(), "submit")
	defer span.End()

	values, err := streamValues(ctx, domain.Analysis{ID: "id-1"})
	if err != nil {
		t.Fatal(err)
	}
	tp, _ := values["traceparent"].(string)
	if !strings.Contains(tp, span.SpanContext().TraceID().String()) {
		t.Fatalf("traceparent=%q no contiene la traza activa", tp)
	}
	if bytes.Contains(values["payload"].([]byte), []byte("traceparent")) {
		t.Fatal("la traza no debe ir dentro del payload: cambiaría el contrato")
	}
}

func TestStreamValuesWithoutSpanHaveOnlyPayload(t *testing.T) {
	values, err := streamValues(context.Background(), domain.Analysis{ID: "id-1"})
	if err != nil || len(values) != 1 {
		t.Fatalf("values=%v err=%v", values, err)
	}
}

func TestAutoStrokeMatchesContractFixture(t *testing.T) {
	const id = "7b2d4e61-0c3a-4f58-8d9e-1a6b5c4f3e20"
	a := domain.Analysis{ID: id, VideoKey: "videos/" + id,
		Params: domain.AnalysisParams{Handedness: domain.HandRight, CameraView: domain.ViewSide}}
	raw, err := json.Marshal(NewJobMessage(a))
	if err != nil {
		t.Fatal(err)
	}
	want := decode(t, readFile(t, "../../../contracts/fixtures/job_message.auto.example.json"))
	if got := decode(t, raw); !reflect.DeepEqual(got, want) {
		t.Errorf("JobMessage automático no coincide con el contrato:\n got=%v\nwant=%v", got, want)
	}
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func decode(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	return m
}
