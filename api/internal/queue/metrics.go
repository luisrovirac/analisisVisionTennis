package queue

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel/metric"
)

// StreamNames identifica la cola principal, su grupo de consumidores y su DLQ.
type StreamNames struct {
	Stream string
	Group  string
	DLQ    string
}

// RegisterMetrics publica dos gauges que observa la API (no el worker): así siguen midiendo
// aunque no quede ningún worker vivo, que es justo cuando más importan.
//   - queue.pending: mensajes entregados y sin confirmar (XPENDING). Si crece sostenidamente hay
//     un worker caído o un mensaje que siempre falla.
//   - queue.dlq.length: mensajes en la DLQ (XLEN) a la espera de inspección manual.
func RegisterMetrics(meter metric.Meter, client *redis.Client, names StreamNames) error {
	pending, err := meter.Int64ObservableGauge("queue.pending",
		metric.WithDescription("Mensajes entregados al grupo y sin confirmar"))
	if err != nil {
		return fmt.Errorf("gauge queue.pending: %w", err)
	}
	dlq, err := meter.Int64ObservableGauge("queue.dlq.length",
		metric.WithDescription("Mensajes en la dead letter queue"))
	if err != nil {
		return fmt.Errorf("gauge queue.dlq.length: %w", err)
	}
	_, err = meter.RegisterCallback(func(ctx context.Context, o metric.Observer) error {
		p, errP := pendingCount(ctx, client, names)
		d, errD := client.XLen(ctx, names.DLQ).Result()
		o.ObserveInt64(pending, p)
		o.ObserveInt64(dlq, d)
		return errors.Join(errP, errD)
	}, pending, dlq)
	return err
}

// pendingCount trata como cero un stream o grupo que aún no existe (nadie ha encolado nada).
func pendingCount(ctx context.Context, client *redis.Client, names StreamNames) (int64, error) {
	res, err := client.XPending(ctx, names.Stream, names.Group).Result()
	if err != nil && strings.HasPrefix(err.Error(), "NOGROUP") {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	return res.Count, nil
}
