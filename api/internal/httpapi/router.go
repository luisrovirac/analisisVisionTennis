package httpapi

import (
	"context"
	"io/fs"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"go.opentelemetry.io/contrib/instrumentation/github.com/gin-gonic/gin/otelgin"

	"tennisvision/api/internal/domain"
)

// HealthCheck representa una dependencia que /readyz debe verificar.
type HealthCheck struct {
	Name string
	Ping func(ctx context.Context) error
}

type RouterDeps struct {
	Analyses    AnalysisUseCases
	Preferences PreferenceUseCases
	Profiles    ProfileUseCases
	Admin       AdminUseCases
	Knowledge   KnowledgeUseCases
	Verifier    TokenVerifier
	Users       UserResolver
	AuthConfig  ClientAuthConfig
	Checks      []HealthCheck
	Web         fs.FS
	Logger      *slog.Logger
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
	v1.DELETE("/analyses/:id", h.remove)
	v1.PATCH("/analyses/:id", h.update)
	v1.POST("/analyses/:id/submit", h.submit)

	prefs := &preferencesHandler{svc: d.Preferences, logger: d.Logger}
	v1.GET("/me/preferences", prefs.get)
	v1.PUT("/me/preferences", prefs.put)

	me := &meHandler{profiles: d.Profiles, logger: d.Logger}
	v1.GET("/me", me.get)
	v1.PUT("/me/profile", me.putProfile)

	registerKnowledge(v1, &knowledgeHandler{svc: d.Knowledge, logger: d.Logger}, d.Logger)
	registerAdmin(v1, &adminHandler{svc: d.Admin, logger: d.Logger}, d.Logger)

	r.GET("/healthz", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) })
	r.GET("/readyz", readiness(d.Checks))
	r.GET("/auth/config", func(c *gin.Context) { c.JSON(http.StatusOK, d.AuthConfig) })
	r.NoRoute(gin.WrapH(http.FileServer(http.FS(d.Web))))
	return r
}

// registerKnowledge: lectura para todo el equipo profesional; proponer solo expertos; decidir solo
// validadores (y nunca sobre lo propio, regla que aplica el servicio); rollback validadores o admins.
func registerKnowledge(v1 *gin.RouterGroup, h *knowledgeHandler, logger *slog.Logger) {
	kb := v1.Group("/kb", requireRole(logger, domain.RoleExpert, domain.RoleValidator, domain.RoleAdmin))
	kb.GET("/metrics", h.catalog)
	kb.GET("/strokes/:stroke", h.active)
	kb.GET("/releases", h.releases)
	kb.GET("/proposals", h.proposals)
	kb.GET("/proposals/:id", h.proposal)
	kb.POST("/proposals/:id/comments", h.comment)
	kb.POST("/preview", h.preview)
	kb.GET("/metric-requests", h.metricRequests)
	kb.POST("/releases/:id/activate", requireRole(logger, domain.RoleValidator, domain.RoleAdmin), h.activate)

	expert := v1.Group("/expert", requireRole(logger, domain.RoleExpert))
	expert.POST("/proposals", h.createProposal)
	expert.PUT("/proposals/:id", h.updateProposal)
	expert.POST("/proposals/:id/submit", h.submit)
	expert.POST("/proposals/:id/withdraw", h.withdraw)
	expert.POST("/metric-requests", h.createMetricRequest)

	validator := v1.Group("/validator", requireRole(logger, domain.RoleValidator))
	validator.POST("/proposals/:id/approve", h.approve)
	validator.POST("/proposals/:id/reject", h.reject)
	validator.POST("/proposals/:id/request-changes", h.requestChanges)
}

func registerAdmin(v1 *gin.RouterGroup, h *adminHandler, logger *slog.Logger) {
	admin := v1.Group("/admin", requireRole(logger, domain.RoleAdmin))
	admin.GET("/users", h.listUsers)
	admin.PUT("/users/:id/roles", h.setRoles)
	admin.PUT("/users/:id/status", h.setStatus)
	admin.GET("/invitations", h.invitations)
	admin.POST("/invitations", h.invite)
	admin.DELETE("/invitations/:id", h.revokeInvitation)
	admin.GET("/audit", h.audit)
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
