//go:build integration

// Ejecutar con la infraestructura de docker-compose levantada:
//
//	TEST_DATABASE_URL=postgres://tennis:...@localhost:5432/tennis?sslmode=disable go test -tags integration ./...
package repository

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
	"tennisvision/api/migrations"
)

func newTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TEST_DATABASE_URL no definida")
	}
	if err := migrations.Up(url); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func newTestRepo(t *testing.T) *PostgresRepository {
	t.Helper()
	return NewPostgres(newTestPool(t))
}

// newUser da de alta un usuario con un subject único para aislar los tests entre sí.
func newUser(t *testing.T) string {
	t.Helper()
	id, err := NewPostgresUsers(newTestPool(t)).Resolve(context.Background(),
		domain.Principal{Issuer: "https://idp.test", Subject: uuid.NewString(), Email: "u@test"})
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func TestPostgresRoundTripAndTransition(t *testing.T) {
	repo := newTestRepo(t)
	ctx := context.Background()
	params := domain.AnalysisParams{StrokeType: domain.StrokeForehand, Handedness: domain.HandRight, CameraView: domain.ViewSide}
	owner := newUser(t)
	a := domain.NewAnalysis(uuid.NewString(), owner, params, time.Now().UTC().Truncate(time.Microsecond))
	if err := repo.Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.Get(ctx, newUser(t), a.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("otro usuario no debe ver el análisis, err=%v", err)
	}
	got, err := repo.Get(ctx, owner, a.ID)
	if err != nil || got.Status != domain.StatusAwaitingUpload || got.Report != nil {
		t.Fatalf("got=%+v err=%v", got, err)
	}
	moved, err := repo.Transition(ctx, a.ID, domain.StatusAwaitingUpload, domain.StatusQueued)
	if err != nil || !moved {
		t.Fatalf("primera transición: moved=%v err=%v", moved, err)
	}
	moved, _ = repo.Transition(ctx, a.ID, domain.StatusAwaitingUpload, domain.StatusQueued)
	if moved {
		t.Fatal("la segunda transición desde el mismo estado no debe aplicarse")
	}
}

func TestPostgresGetMissing(t *testing.T) {
	repo := newTestRepo(t)
	if _, err := repo.Get(context.Background(), newUser(t), uuid.NewString()); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("se esperaba ErrNotFound, got %v", err)
	}
}

func TestUsersResolveIsStable(t *testing.T) {
	users := NewPostgresUsers(newTestPool(t))
	p := domain.Principal{Issuer: "https://idp.test", Subject: uuid.NewString()}
	first, err := users.Resolve(context.Background(), p)
	if err != nil {
		t.Fatal(err)
	}
	again, err := users.Resolve(context.Background(), p)
	if err != nil || again != first {
		t.Fatalf("el mismo (issuer, subject) debe dar el mismo id: %s vs %s (%v)", first, again, err)
	}
	p.Issuer = "https://otro-idp.test"
	if other, _ := users.Resolve(context.Background(), p); other == first {
		t.Fatal("el mismo sub en otro emisor es otro usuario")
	}
}

func TestPostgresListPaginatesByOwner(t *testing.T) {
	repo := newTestRepo(t)
	ctx := context.Background()
	owner, stranger := newUser(t), newUser(t)
	base := time.Now().UTC().Truncate(time.Microsecond)
	var ids []string
	for i := range 3 {
		a := domain.NewAnalysis(uuid.NewString(), owner, domain.AnalysisParams{
			StrokeType: domain.StrokeForehand, Handedness: domain.HandRight, CameraView: domain.ViewSide,
		}, base.Add(time.Duration(i)*time.Second))
		if err := repo.Create(ctx, a); err != nil {
			t.Fatal(err)
		}
		ids = append(ids, a.ID)
	}
	first, err := repo.List(ctx, owner, nil, 2)
	if err != nil || len(first) != 2 || first[0].ID != ids[2] || first[1].ID != ids[1] {
		t.Fatalf("primera página: %v err=%v", first, err)
	}
	cursor := first[1].Cursor()
	rest, err := repo.List(ctx, owner, &cursor, 2)
	if err != nil || len(rest) != 1 || rest[0].ID != ids[0] {
		t.Fatalf("segunda página: %v err=%v", rest, err)
	}
	if none, _ := repo.List(ctx, stranger, nil, 10); len(none) != 0 {
		t.Fatalf("un usuario sin análisis no debe ver nada, got %d", len(none))
	}
}

func TestPostgresAutoStrokeIsStoredAsNull(t *testing.T) {
	repo := newTestRepo(t)
	ctx := context.Background()
	owner := newUser(t)
	a := domain.NewAnalysis(uuid.NewString(), owner, domain.AnalysisParams{
		StrokeType: domain.StrokeAuto, Handedness: domain.HandRight, CameraView: domain.ViewSide,
	}, time.Now().UTC().Truncate(time.Microsecond))
	if err := repo.Create(ctx, a); err != nil {
		t.Fatal(err)
	}
	got, err := repo.Get(ctx, owner, a.ID)
	if err != nil || got.Params.StrokeType != domain.StrokeAuto {
		t.Fatalf("got=%+v err=%v", got.Params, err)
	}
}
