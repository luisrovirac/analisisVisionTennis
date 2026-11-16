// Command api es la raíz de composición: carga config, construye dependencias y sirve HTTP.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/exaring/otelpgx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/extra/redisotel/v9"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"

	"tennisvision/api/internal/auth"
	"tennisvision/api/internal/config"
	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/httpapi"
	"tennisvision/api/internal/queue"
	"tennisvision/api/internal/repository"
	"tennisvision/api/internal/service"
	"tennisvision/api/internal/storage"
	"tennisvision/api/internal/telemetry"
	"tennisvision/api/migrations"
	"tennisvision/api/web"
)

func main() {
	if err := run(); err != nil {
		slog.Error("la API terminó con error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load(os.LookupEnv)
	if err != nil {
		return err
	}
	logger := slog.New(telemetry.TraceHandler{Handler: slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel})})
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	shutdownTelemetry, err := telemetry.Setup(ctx, telemetry.Config{
		Endpoint: cfg.Telemetry.OTLPEndpoint, ServiceName: cfg.Telemetry.ServiceName,
	})
	if err != nil {
		return err
	}
	defer flushTelemetry(shutdownTelemetry, logger)

	if err := migrations.Up(cfg.DatabaseURL); err != nil {
		return err
	}
	inf, err := openInfra(ctx, cfg)
	if err != nil {
		return err
	}
	defer inf.close()
	verifier, err := newVerifier(ctx, cfg.Auth, logger)
	if err != nil {
		return err
	}
	if err := instrument(inf, cfg); err != nil {
		return err
	}
	handler, err := wire(inf, verifier, cfg, logger)
	if err != nil {
		return err
	}
	return serve(ctx, handler, cfg.Port, logger)
}

// infra agrupa los clientes de infraestructura que necesitan cierre ordenado.
type infra struct {
	pool  *pgxpool.Pool
	redis *redis.Client
	store *storage.S3Store
}

func (i infra) close() {
	_ = i.redis.Close()
	i.pool.Close()
}

func openInfra(ctx context.Context, cfg config.Config) (infra, error) {
	store, err := storage.NewS3Store(cfg.S3)
	if err != nil {
		return infra{}, err
	}
	opts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		return infra{}, fmt.Errorf("REDIS_URL inválida: %w", err)
	}
	poolCfg, err := pgxpool.ParseConfig(cfg.DatabaseURL)
	if err != nil {
		return infra{}, fmt.Errorf("DATABASE_URL inválida: %w", err)
	}
	poolCfg.ConnConfig.Tracer = otelpgx.NewTracer() // un span por consulta SQL
	pool, err := pgxpool.NewWithConfig(ctx, poolCfg)
	if err != nil {
		return infra{}, fmt.Errorf("pool postgres: %w", err)
	}
	return infra{pool: pool, redis: redis.NewClient(opts), store: store}, nil
}

// instrument añade spans a Redis y registra las métricas de la cola (pendientes y DLQ).
func instrument(inf infra, cfg config.Config) error {
	if err := redisotel.InstrumentTracing(inf.redis); err != nil {
		return fmt.Errorf("instrumentar redis: %w", err)
	}
	return queue.RegisterMetrics(otel.Meter("tennisvision/api/queue"), inf.redis, queue.StreamNames{
		Stream: cfg.JobStream, Group: cfg.JobGroup, DLQ: cfg.JobDLQStream,
	})
}

func flushTelemetry(shutdown telemetry.Shutdown, logger *slog.Logger) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := shutdown(ctx); err != nil {
		logger.Warn("no se pudo vaciar la telemetría", "error", err)
	}
}

// newVerifier recibe el ctx de vida del proceso: go-oidc lo usa para refrescar el JWKS.
func newVerifier(ctx context.Context, cfg config.AuthConfig, logger *slog.Logger) (httpapi.TokenVerifier, error) {
	if cfg.Disabled {
		logger.Warn("AUTH_DISABLED: todas las peticiones se atribuyen al usuario de desarrollo")
		return auth.StaticVerifier{Principal: domain.Principal{Issuer: "dev", Subject: "dev-user"}}, nil
	}
	return auth.NewOIDCVerifier(ctx, auth.OIDCConfig{IssuerURL: cfg.IssuerURL, Audience: cfg.Audience, JWKSURL: cfg.JWKSURL})
}

func clientAuthConfig(cfg config.AuthConfig) httpapi.ClientAuthConfig {
	if cfg.Disabled {
		return httpapi.ClientAuthConfig{}
	}
	return httpapi.ClientAuthConfig{Enabled: true, Issuer: cfg.IssuerURL, ClientID: cfg.WebClientID}
}

func wire(inf infra, verifier httpapi.TokenVerifier, cfg config.Config, logger *slog.Logger) (http.Handler, error) {
	repo := repository.NewPostgres(inf.pool)
	publisher := queue.NewRedisPublisher(inf.redis, cfg.JobStream)
	svc := service.New(repo, inf.store, publisher, time.Now, uuid.NewString,
		service.Config{UploadURLTTL: cfg.UploadURLTTL, MaxUploadBytes: cfg.MaxUploadBytes})
	static, err := web.Static()
	if err != nil {
		return nil, fmt.Errorf("cliente web: %w", err)
	}
	checks := []httpapi.HealthCheck{
		{Name: "postgres", Ping: repo.Ping},
		{Name: "redis", Ping: publisher.Ping},
		{Name: "storage", Ping: inf.store.Ping},
	}
	return httpapi.NewRouter(httpapi.RouterDeps{
		Analyses:    svc,
		Verifier:    verifier,
		Users:       repository.NewPostgresUsers(inf.pool),
		AuthConfig:  clientAuthConfig(cfg.Auth),
		Checks:      checks,
		Web:         static,
		Logger:      logger,
		ServiceName: cfg.Telemetry.ServiceName,
	}), nil
}

func serve(ctx context.Context, handler http.Handler, port int, logger *slog.Logger) error {
	srv := &http.Server{Addr: fmt.Sprintf(":%d", port), Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()
	logger.Info("API escuchando", "port", port)
	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdownCtx); err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("apagado: %w", err)
		}
		return nil
	}
}
