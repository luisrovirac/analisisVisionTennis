package httpapi

import (
	"context"
	"io/fs"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"go.opentelemetry.io/contrib/instrumentation/github.com/gin-gonic/gin/otelgin"
)

// HealthCheck representa una dependencia que /readyz debe verificar.
type HealthCheck struct {
	Name string
	Ping func(ctx context.Context) error
}

type RouterDeps struct {
	Analyses   AnalysisUseCases
	Verifier   TokenVerifier
	Users      UserResolver
	AuthConfig ClientAuthConfig
	Checks     []HealthCheck
	Web        fs.FS
	Logger     *slog.Logger
	// ServiceName nombra los spans HTTP; sin provider global de OTel no cuesta nada.
	ServiceName string
}

func NewRouter(d RouterDeps) *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery(), otelgin.Middleware(d.ServiceName, otelgin.WithGinFilter(notProbe)), requestLogger(d.Logger))

	h := &analysisHandler{svc: d.Analyses, logger: d.Logger}
	v1 := r.Group("/v1", authenticate(d.Verifier, d.Users, d.Logger))
	v1.GET("/analyses", h.list)
	v1.POST("/analyses", h.create)
	v1.GET("/analyses/:id", h.get)
	v1.POST("/analyses/:id/submit", h.submit)

	r.GET("/healthz", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) })
	r.GET("/readyz", readiness(d.Checks))
	r.GET("/auth/config", func(c *gin.Context) { c.JSON(http.StatusOK, d.AuthConfig) })
	r.NoRoute(gin.WrapH(http.FileServer(http.FS(d.Web))))
	return r
}

// notProbe evita llenar el backend de trazas con las sondas de salud (cada pocos segundos).
func notProbe(c *gin.Context) bool {
	return c.Request.URL.Path != "/healthz" && c.Request.URL.Path != "/readyz"
}

func readiness(checks []HealthCheck) gin.HandlerFunc {
	return func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		defer cancel()
		results, healthy := map[string]string{}, true
		for _, check := range checks {
			results[check.Name] = "ok"
			if err := check.Ping(ctx); err != nil {
				results[check.Name], healthy = err.Error(), false
			}
		}
		status := http.StatusOK
		if !healthy {
			status = http.StatusServiceUnavailable
		}
		c.JSON(status, gin.H{"checks": results})
	}
}

func requestLogger(logger *slog.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		logger.InfoContext(c.Request.Context(), "request",
			"method", c.Request.Method, "path", c.Request.URL.Path,
			"status", c.Writer.Status(), "duration_ms", time.Since(start).Milliseconds())
	}
}
