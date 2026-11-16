// Package migrations embebe el esquema SQL. La API es la dueña del esquema; el worker solo
// actualiza columnas de estado/resultados de la tabla analyses.
package migrations

import (
	"embed"
	"errors"
	"fmt"
	"strings"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/pgx/v5" // registra el driver pgx5://
	"github.com/golang-migrate/migrate/v4/source/iofs"
)

//go:embed *.sql
var files embed.FS

// Up aplica las migraciones pendientes. Es idempotente.
func Up(databaseURL string) error {
	src, err := iofs.New(files, ".")
	if err != nil {
		return fmt.Errorf("leer migraciones: %w", err)
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, toPgx5URL(databaseURL))
	if err != nil {
		return fmt.Errorf("conectar para migrar: %w", err)
	}
	defer m.Close()
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return fmt.Errorf("aplicar migraciones: %w", err)
	}
	return nil
}

func toPgx5URL(url string) string {
	for _, prefix := range []string{"postgresql://", "postgres://"} {
		if rest, ok := strings.CutPrefix(url, prefix); ok {
			return "pgx5://" + rest
		}
	}
	return url
}
