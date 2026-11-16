// Package queue publica trabajos de análisis en un Redis Stream consumido por el worker.
package queue

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"

	"tennisvision/api/internal/domain"
)

// 1.1: stroke_type opcional. El worker acepta 1.0 y 1.1.
const schemaVersion = "1.1"

// JobMessage sigue contracts/job_message.schema.json.
type JobMessage struct {
	SchemaVersion string `json:"schema_version"`
	AnalysisID    string `json:"analysis_id"`
	VideoKey      string `json:"video_key"`
	StrokeType    string `json:"stroke_type,omitempty"` // vacío = detección automática
	Handedness    string `json:"handedness"`
	CameraView    string `json:"camera_view"`
}

func NewJobMessage(a domain.Analysis) JobMessage {
	return JobMessage{
		SchemaVersion: schemaVersion,
		AnalysisID:    a.ID,
		VideoKey:      a.VideoKey,
		StrokeType:    string(a.Params.StrokeType),
		Handedness:    string(a.Params.Handedness),
		CameraView:    string(a.Params.CameraView),
	}
}

// RedisPublisher se apoya en los reintentos con backoff integrados en go-redis (MaxRetries).
type RedisPublisher struct {
	client *redis.Client
	stream string
}

func NewRedisPublisher(client *redis.Client, stream string) *RedisPublisher {
	return &RedisPublisher{client: client, stream: stream}
}

func (p *RedisPublisher) Publish(ctx context.Context, a domain.Analysis) error {
	values, err := streamValues(ctx, a)
	if err != nil {
		return err
	}
	if err := p.client.XAdd(ctx, &redis.XAddArgs{Stream: p.stream, Values: values}).Err(); err != nil {
		return fmt.Errorf("XADD %s: %w", p.stream, err)
	}
	return nil
}

// streamValues arma la entrada del stream: `payload` (el contrato) y, si hay un span activo, el
// contexto W3C (`traceparent`) como campos aparte. Así el contrato JSON no cambia y los workers
// que no conocen la traza simplemente ignoran esos campos.
func streamValues(ctx context.Context, a domain.Analysis) (map[string]any, error) {
	payload, err := json.Marshal(NewJobMessage(a))
	if err != nil {
		return nil, fmt.Errorf("serializar trabajo: %w", err)
	}
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	values := map[string]any{"payload": payload}
	for k, v := range carrier {
		values[k] = v
	}
	return values, nil
}

func (p *RedisPublisher) Ping(ctx context.Context) error {
	return p.client.Ping(ctx).Err()
}
