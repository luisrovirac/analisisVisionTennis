// Package auth verifica tokens de acceso emitidos por un proveedor OIDC. La API es un
// resource server: no emite tokens, solo comprueba firma (JWKS), emisor, audiencia y expiración.
package auth

import (
	"context"
	"errors"
	"fmt"

	"github.com/coreos/go-oidc/v3/oidc"

	"tennisvision/api/internal/domain"
)

type OIDCConfig struct {
	IssuerURL string // valor exacto esperado en el claim `iss`
	Audience  string // debe aparecer en el claim `aud`
	// JWKSURL es opcional. Si se indica, las claves se descargan de aquí y no se usa discovery.
	// Útil cuando la API alcanza al IdP por una dirección interna distinta de la pública del `iss`.
	JWKSURL string
}

// OIDCVerifier rota las claves automáticamente: go-oidc recarga el JWKS cuando ve un `kid` nuevo.
type OIDCVerifier struct {
	verifier *oidc.IDTokenVerifier
}

// NewOIDCVerifier necesita un ctx de larga vida: se usa para las descargas del JWKS en segundo plano.
func NewOIDCVerifier(ctx context.Context, cfg OIDCConfig) (*OIDCVerifier, error) {
	oc := &oidc.Config{ClientID: cfg.Audience, SupportedSigningAlgs: []string{oidc.RS256, oidc.ES256}}
	if cfg.JWKSURL != "" {
		keys := oidc.NewRemoteKeySet(ctx, cfg.JWKSURL)
		return &OIDCVerifier{verifier: oidc.NewVerifier(cfg.IssuerURL, keys, oc)}, nil
	}
	provider, err := oidc.NewProvider(ctx, cfg.IssuerURL)
	if err != nil {
		return nil, fmt.Errorf("discovery OIDC de %s: %w", cfg.IssuerURL, err)
	}
	return &OIDCVerifier{verifier: provider.VerifierContext(ctx, oc)}, nil
}

var errMissingToken = errors.New("falta el token bearer")

func (v *OIDCVerifier) Verify(ctx context.Context, rawToken string) (domain.Principal, error) {
	if rawToken == "" {
		return domain.Principal{}, fmt.Errorf("%w: %w", domain.ErrUnauthenticated, errMissingToken)
	}
	token, err := v.verifier.Verify(ctx, rawToken)
	if err != nil {
		return domain.Principal{}, fmt.Errorf("%w: %w", domain.ErrUnauthenticated, err)
	}
	var claims struct {
		Email         string `json:"email"`
		EmailVerified bool   `json:"email_verified"`
	}
	if err := token.Claims(&claims); err != nil {
		return domain.Principal{}, fmt.Errorf("%w: claims ilegibles: %w", domain.ErrUnauthenticated, err)
	}
	if token.Subject == "" {
		return domain.Principal{}, fmt.Errorf("%w: el token no tiene `sub`", domain.ErrUnauthenticated)
	}
	return domain.Principal{Issuer: token.Issuer, Subject: token.Subject, Email: claims.Email,
		EmailVerified: claims.EmailVerified}, nil
}

// StaticVerifier acepta cualquier petición como un usuario fijo. Solo para desarrollo local
// (AUTH_DISABLED=true, que config solo permite con APP_ENV=dev).
type StaticVerifier struct {
	Principal domain.Principal
}

func (s StaticVerifier) Verify(context.Context, string) (domain.Principal, error) {
	return s.Principal, nil
}
