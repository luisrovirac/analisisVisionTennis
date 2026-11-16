package config

import (
	"strings"
	"testing"
	"time"
)

func envFrom(m map[string]string) LookupFunc {
	return func(k string) (string, bool) {
		v, ok := m[k]
		return v, ok
	}
}

func validEnv() map[string]string {
	return map[string]string{
		"DATABASE_URL":       "postgres://u:p@localhost/db",
		"REDIS_URL":          "redis://localhost:6379/0",
		"S3_ENDPOINT":        "minio:9000",
		"S3_PUBLIC_ENDPOINT": "localhost:9000",
		"S3_ACCESS_KEY":      "key",
		"S3_SECRET_KEY":      "secret",
		"S3_BUCKET":          "videos",
		"OIDC_ISSUER_URL":    "http://localhost:8180/realms/tennis",
		"OIDC_AUDIENCE":      "tennis-api",
	}
}

func TestLoadAppliesDefaults(t *testing.T) {
	cfg, err := Load(envFrom(validEnv()))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 8080 || cfg.UploadURLTTL != 15*time.Minute || cfg.MaxUploadBytes != 200<<20 {
		t.Fatalf("defaults inesperados: %+v", cfg)
	}
	if cfg.S3.Region != "us-east-1" || cfg.JobStream != "analysis_jobs" || cfg.JobDLQStream != "analysis_jobs:dlq" ||
		cfg.JobGroup != "analysis_workers" || cfg.Telemetry.OTLPEndpoint != "" || cfg.Telemetry.ServiceName != "tennis-api" {
		t.Fatalf("defaults S3/stream inesperados: %+v", cfg)
	}
}

func TestLoadParsesOverrides(t *testing.T) {
	env := validEnv()
	env["API_PORT"] = "9090"
	env["MAX_UPLOAD_MB"] = "10"
	env["S3_USE_SSL"] = "true"
	env["UPLOAD_URL_TTL"] = "5m"
	cfg, err := Load(envFrom(env))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 9090 || cfg.MaxUploadBytes != 10<<20 || !cfg.S3.UseSSL || cfg.UploadURLTTL != 5*time.Minute {
		t.Fatalf("overrides no aplicados: %+v", cfg)
	}
}

func TestLoadReportsAllProblemsTogether(t *testing.T) {
	env := map[string]string{"API_PORT": "abc"}
	_, err := Load(envFrom(env))
	if err == nil {
		t.Fatal("se esperaba error")
	}
	for _, want := range []string{"DATABASE_URL", "REDIS_URL", "S3_BUCKET", "API_PORT", "OIDC_ISSUER_URL", "OIDC_AUDIENCE"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("el error no menciona %s: %v", want, err)
		}
	}
}

func TestLoadAuthDefaults(t *testing.T) {
	cfg, err := Load(envFrom(validEnv()))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Auth.Disabled || cfg.Auth.WebClientID != "tennis-web" || cfg.Auth.Audience != "tennis-api" {
		t.Fatalf("auth inesperada: %+v", cfg.Auth)
	}
}

func TestAuthDisabledOnlyInDev(t *testing.T) {
	env := validEnv()
	delete(env, "OIDC_ISSUER_URL")
	delete(env, "OIDC_AUDIENCE")
	env["AUTH_DISABLED"] = "true"
	if _, err := Load(envFrom(env)); err == nil || !strings.Contains(err.Error(), "APP_ENV") {
		t.Fatalf("fuera de dev debe rechazarse, err=%v", err)
	}
	env["APP_ENV"] = "dev"
	cfg, err := Load(envFrom(env))
	if err != nil || !cfg.Auth.Disabled {
		t.Fatalf("en dev debe aceptarse sin OIDC: %+v err=%v", cfg.Auth, err)
	}
}
