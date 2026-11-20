package auth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"errors"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/coreos/go-oidc/v3/oidc/oidctest"

	"tennisvision/api/internal/domain"
)

const (
	keyID    = "test-key"
	audience = "tennis-api"
)

type idp struct {
	url  string
	priv *rsa.PrivateKey
}

func newIdP(t *testing.T) idp {
	t.Helper()
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	s := &oidctest.Server{PublicKeys: []oidctest.PublicKey{{PublicKey: priv.Public(), KeyID: keyID, Algorithm: oidc.RS256}}}
	srv := httptest.NewServer(s)
	t.Cleanup(srv.Close)
	s.SetIssuer(srv.URL)
	return idp{url: srv.URL, priv: priv}
}

func (p idp) token(iss, aud, sub string, exp time.Time) string {
	claims := fmt.Sprintf(`{"iss":%q,"aud":%q,"sub":%q,"email":"a@example.com","email_verified":true,"exp":%d}`, iss, aud, sub, exp.Unix())
	return oidctest.SignIDToken(p.priv, keyID, oidc.RS256, claims)
}

func newVerifiers(t *testing.T, p idp) map[string]*OIDCVerifier {
	t.Helper()
	ctx := context.Background()
	discovery, err := NewOIDCVerifier(ctx, OIDCConfig{IssuerURL: p.url, Audience: audience})
	if err != nil {
		t.Fatal(err)
	}
	direct, err := NewOIDCVerifier(ctx, OIDCConfig{IssuerURL: p.url, Audience: audience, JWKSURL: p.url + "/keys"})
	if err != nil {
		t.Fatal(err)
	}
	return map[string]*OIDCVerifier{"discovery": discovery, "jwks": direct}
}

func TestVerifyAcceptsValidToken(t *testing.T) {
	p := newIdP(t)
	for mode, v := range newVerifiers(t, p) {
		got, err := v.Verify(context.Background(), p.token(p.url, audience, "user-1", time.Now().Add(time.Hour)))
		want := domain.Principal{Issuer: p.url, Subject: "user-1", Email: "a@example.com", EmailVerified: true}
		if err != nil || got != want {
			t.Errorf("%s: got=%+v err=%v", mode, got, err)
		}
	}
}

func TestVerifyRejectsInvalidTokens(t *testing.T) {
	p := newIdP(t)
	valid := p.token(p.url, audience, "user-1", time.Now().Add(time.Hour))
	parts := strings.Split(valid, ".")
	tampered := parts[0] + "." + parts[1] + "x." + parts[2] // payload alterado: la firma ya no cuadra
	other := newIdP(t)
	cases := map[string]string{
		"vacío":       "",
		"basura":      "no-es-un-jwt",
		"expirado":    p.token(p.url, audience, "user-1", time.Now().Add(-time.Minute)),
		"audiencia":   p.token(p.url, "otra-api", "user-1", time.Now().Add(time.Hour)),
		"emisor":      p.token("https://evil.example", audience, "user-1", time.Now().Add(time.Hour)),
		"manipulado":  tampered,
		"otra clave":  other.token(p.url, audience, "user-1", time.Now().Add(time.Hour)),
		"sin subject": p.token(p.url, audience, "", time.Now().Add(time.Hour)),
	}
	for mode, v := range newVerifiers(t, p) {
		for name, raw := range cases {
			if _, err := v.Verify(context.Background(), raw); !errors.Is(err, domain.ErrUnauthenticated) {
				t.Errorf("%s/%s: se esperaba ErrUnauthenticated, got %v", mode, name, err)
			}
		}
	}
}

func TestDiscoveryFailsFastWhenIssuerUnreachable(t *testing.T) {
	if _, err := NewOIDCVerifier(context.Background(), OIDCConfig{IssuerURL: "http://127.0.0.1:1", Audience: audience}); err == nil {
		t.Fatal("se esperaba error de discovery")
	}
}
