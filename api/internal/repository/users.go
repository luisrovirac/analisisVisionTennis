package repository

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
)

// PostgresUsers traduce la identidad OIDC (issuer, subject) al id interno del usuario.
type PostgresUsers struct {
	pool *pgxpool.Pool
}

func NewPostgresUsers(pool *pgxpool.Pool) *PostgresUsers {
	return &PostgresUsers{pool: pool}
}

const findUserSQL = `SELECT id::text FROM users WHERE issuer = $1 AND subject = $2`

// DO UPDATE (y no DO NOTHING) para que RETURNING devuelva el id también si otra petición
// concurrente del mismo usuario lo insertó entre la búsqueda y la inserción.
const insertUserSQL = `
INSERT INTO users (issuer, subject, email) VALUES ($1, $2, NULLIF($3, ''))
ON CONFLICT (issuer, subject) DO UPDATE SET email = COALESCE(EXCLUDED.email, users.email)
RETURNING id::text`

// Resolve devuelve el id del usuario y lo da de alta en su primera petición. El caso habitual
// (usuario ya conocido) es una sola lectura, sin escrituras por petición.
func (u *PostgresUsers) Resolve(ctx context.Context, p domain.Principal) (string, error) {
	var id string
	err := u.pool.QueryRow(ctx, findUserSQL, p.Issuer, p.Subject).Scan(&id)
	if err == nil {
		return id, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return "", fmt.Errorf("buscar usuario: %w", err)
	}
	if err := u.pool.QueryRow(ctx, insertUserSQL, p.Issuer, p.Subject, p.Email).Scan(&id); err != nil {
		return "", fmt.Errorf("registrar usuario: %w", err)
	}
	return id, nil
}
