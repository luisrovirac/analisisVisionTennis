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

// UserResolver traduce la identidad del IdP al usuario interno con sus roles (alta en el primer acceso).
type UserResolver interface {
	Resolve(ctx context.Context, p domain.Principal) (domain.User, error)
}

const userKey = "user"

// authenticate rechaza la petición antes de llegar al handler si el token no es válido o la cuenta
// está suspendida.
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
		user, err := users.Resolve(ctx, principal)
		if err == nil && user.Status == domain.UserSuspended {
			err = domain.ErrAccountSuspended
		}
		if err != nil {
			writeError(c, logger, err)
			c.Abort()
			return
		}
		c.Set(userKey, user)
		c.Next()
	}
}

// requireRole deja pasar si el usuario tiene al menos uno de los roles. La UI solo oculta opciones:
// la autorización real es esta.
func requireRole(logger *slog.Logger, roles ...domain.Role) gin.HandlerFunc {
	return func(c *gin.Context) {
		if !currentUser(c).Has(roles...) {
			writeError(c, logger, domain.ErrForbidden)
			c.Abort()
			return
		}
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

// currentUser solo se llama en rutas protegidas por authenticate; si falta es un error de cableado.
func currentUser(c *gin.Context) domain.User {
	u, ok := c.Get(userKey)
	if !ok {
		panic(errors.New("ruta protegida sin middleware de autenticación"))
	}
	return u.(domain.User)
}

func ownerID(c *gin.Context) string {
	return currentUser(c).ID
}
