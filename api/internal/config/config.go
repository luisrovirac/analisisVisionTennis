// Package config carga y valida toda la configuración al arrancar (fail fast).
package config

import (
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"time"
)

type Config struct {
	Port           int
	DatabaseURL    string
	RedisURL       string
	JobStream      string
	JobGroup       string
	JobDLQStream   string
	S3             S3Config
	UploadURLTTL   time.Duration
	MaxUploadBytes int64
	LogLevel       slog.Level
	Auth           AuthConfig
	Telemetry      TelemetryConfig
}

type TelemetryConfig struct {
	OTLPEndpoint string // vacío = telemetría desactivada
	ServiceName  string
}

type AuthConfig struct {
	// Disabled sustituye la verificación OIDC por un usuario fijo. Solo se acepta con APP_ENV=dev.
	Disabled    bool
	IssuerURL   string
	Audience    string
	JWKSURL     string
	WebClientID string
}

type S3Config struct {
	Endpoint       string
	PublicEndpoint string
	AccessKey      string
	SecretKey      string
	Bucket         string
	Region         string
	UseSSL         bool
	PublicUseSSL   bool
}

// LookupFunc tiene la firma de os.LookupEnv; se inyecta para poder probar sin tocar el entorno real.
type LookupFunc func(key string) (string, bool)

// Load lee la configuración y devuelve todos los problemas encontrados a la vez.
func Load(lookup LookupFunc) (Config, error) {
	l := &loader{lookup: lookup}
	cfg := Config{
		Port:           l.integer("API_PORT", 8080),
		DatabaseURL:    l.required("DATABASE_URL"),
		RedisURL:       l.required("REDIS_URL"),
		JobStream:      l.optional("JOB_STREAM", "analysis_jobs"),
		JobGroup:       l.optional("JOB_GROUP", "analysis_workers"),
		S3:             loadS3(l),
		UploadURLTTL:   l.duration("UPLOAD_URL_TTL", 15*time.Minute),
		MaxUploadBytes: int64(l.integer("MAX_UPLOAD_MB", 200)) << 20,
		LogLevel:       l.logLevel("LOG_LEVEL", slog.LevelInfo),
		Auth:           loadAuth(l),
		Telemetry: TelemetryConfig{
			OTLPEndpoint: l.optional("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
			ServiceName:  l.optional("OTEL_SERVICE_NAME", "tennis-api"),
		},
	}
	cfg.JobDLQStream = l.optional("JOB_DLQ_STREAM", cfg.JobStream+":dlq")
	if err := errors.Join(l.errs...); err != nil {
		return Config{}, fmt.Errorf("configuración inválida:\n%w", err)
	}
	return cfg, nil
}

func loadS3(l *loader) S3Config {
	return S3Config{
		Endpoint:       l.required("S3_ENDPOINT"),
		PublicEndpoint: l.required("S3_PUBLIC_ENDPOINT"),
		AccessKey:      l.required("S3_ACCESS_KEY"),
		SecretKey:      l.required("S3_SECRET_KEY"),
		Bucket:         l.required("S3_BUCKET"),
		Region:         l.optional("S3_REGION", "us-east-1"),
		UseSSL:         l.boolean("S3_USE_SSL", false),
		PublicUseSSL:   l.boolean("S3_PUBLIC_USE_SSL", false),
	}
}

func loadAuth(l *loader) AuthConfig {
	env := l.optional("APP_ENV", "production")
	if l.boolean("AUTH_DISABLED", false) {
		if env != "dev" {
			l.errs = append(l.errs, fmt.Errorf("AUTH_DISABLED=true solo se permite con APP_ENV=dev (APP_ENV=%q)", env))
		}
		return AuthConfig{Disabled: true}
	}
	return AuthConfig{
		IssuerURL:   l.required("OIDC_ISSUER_URL"),
		Audience:    l.required("OIDC_AUDIENCE"),
		JWKSURL:     l.optional("OIDC_JWKS_URL", ""),
		WebClientID: l.optional("OIDC_WEB_CLIENT_ID", "tennis-web"),
	}
}

type loader struct {
	lookup LookupFunc
	errs   []error
}

func (l *loader) required(key string) string {
	v, ok := l.lookup(key)
	if !ok || v == "" {
		l.errs = append(l.errs, fmt.Errorf("falta la variable de entorno %s", key))
	}
	return v
}

func (l *loader) optional(key, def string) string {
	if v, ok := l.lookup(key); ok && v != "" {
		return v
	}
	return def
}

func (l *loader) integer(key string, def int) int {
	return parse(l, key, def, strconv.Atoi)
}

func (l *loader) boolean(key string, def bool) bool {
	return parse(l, key, def, strconv.ParseBool)
}

func (l *loader) duration(key string, def time.Duration) time.Duration {
	return parse(l, key, def, time.ParseDuration)
}

func (l *loader) logLevel(key string, def slog.Level) slog.Level {
	return parse(l, key, def, func(s string) (slog.Level, error) {
		var lvl slog.Level
		err := lvl.UnmarshalText([]byte(s))
		return lvl, err
	})
}

func parse[T any](l *loader, key string, def T, conv func(string) (T, error)) T {
	raw, ok := l.lookup(key)
	if !ok || raw == "" {
		return def
	}
	v, err := conv(raw)
	if err != nil {
		l.errs = append(l.errs, fmt.Errorf("%s=%q no es válido: %w", key, raw, err))
		return def
	}
	return v
}
