package httpapi

import (
	"context"
	"errors"
	"log/slog"
	"strings"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
)

// TokenVerifier comprueba el token y devuelve la identidad; los errores envuelven domain.ErrUnauthenticated.
type TokenVerifier interface {
	Verify(ctx context.Context, rawToken string) (domain.Principal, error)
}

// UserResolver traduce la identidad del IdP al id interno del usuario (alta en el primer acceso).
type UserResolver interface {
	Resolve(ctx context.Context, p domain.Principal) (string, error)
}

const ownerKey = "owner_id"

// authenticate rechaza la petición antes de llegar al handler si el token no es válido.
func authenticate(verifier TokenVerifier, users UserResolver, logger *slog.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		ctx := c.Request.Context()
		principal, err := verifier.Verify(ctx, bearerToken(c.GetHeader("Authorization")))
		if err != nil {
			logger.InfoContext(ctx, "token rechazado", "reason", err.Error(), "path", c.FullPath())
			c.Header("WWW-Authenticate", `Bearer realm="tennis-api"`)
			writeError(c, logger, err)
			c.Abort()
			return
		}
		ownerID, err := users.Resolve(ctx, principal)
		if err != nil {
			writeError(c, logger, err)
			c.Abort()
			return
		}
		c.Set(ownerKey, ownerID)
		c.Next()
	}
}

func bearerToken(header string) string {
	scheme, token, ok := strings.Cut(header, " ")
	if !ok || !strings.EqualFold(scheme, "Bearer") {
		return ""
	}
	return strings.TrimSpace(token)
}

// ownerID solo se llama en rutas protegidas por authenticate; si falta es un error de cableado.
func ownerID(c *gin.Context) string {
	id := c.GetString(ownerKey)
	if id == "" {
		panic(errors.New("ruta protegida sin middleware de autenticación"))
	}
	return id
}
