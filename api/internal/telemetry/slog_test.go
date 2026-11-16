package telemetry

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"testing"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

func TestTraceHandlerAddsIDsOnlyInsideSpans(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(TraceHandler{slog.NewJSONHandler(&buf, nil)}).With("svc", "api")

	ctx, span := sdktrace.NewTracerProvider().Tracer("test").Start(context.Background(), "op")
	logger.InfoContext(ctx, "dentro")
	span.End()
	logger.InfoContext(context.Background(), "fuera")

	lines := bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\n"))
	var inside, outside map[string]any
	if err := json.Unmarshal(lines[0], &inside); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(lines[1], &outside); err != nil {
		t.Fatal(err)
	}
	if inside["trace_id"] != span.SpanContext().TraceID().String() || inside["svc"] != "api" {
		t.Fatalf("faltan atributos dentro del span: %v", inside)
	}
	if _, ok := outside["trace_id"]; ok {
		t.Fatalf("no debe haber trace_id sin span: %v", outside)
	}
}

func TestSetupWithoutEndpointIsNoop(t *testing.T) {
	shutdown, err := Setup(context.Background(), Config{ServiceName: "tennis-api"})
	if err != nil {
		t.Fatal(err)
	}
	if err := shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
}
