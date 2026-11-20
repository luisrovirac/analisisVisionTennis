package service

import (
	"context"
	"errors"
	"testing"

	"tennisvision/api/internal/domain"
)

type fakePrefsRepo struct {
	byUser  map[string]domain.Preferences
	saveErr error
}

func (r *fakePrefsRepo) GetPreferences(_ context.Context, userID string) (domain.Preferences, error) {
	return r.byUser[userID], nil
}

func (r *fakePrefsRepo) SavePreferences(_ context.Context, userID string, p domain.Preferences) error {
	if r.saveErr != nil {
		return r.saveErr
	}
	r.byUser[userID] = p
	return nil
}

func newPrefsFixture() (*PreferencesService, *fakePrefsRepo) {
	repo := &fakePrefsRepo{byUser: map[string]domain.Preferences{
		alice: {Handedness: domain.HandRight}, bob: {Handedness: domain.HandRight},
	}}
	return NewPreferences(repo), repo
}

func TestUpdatePreferencesPersistsPerUser(t *testing.T) {
	svc, _ := newPrefsFixture()
	ctx := context.Background()
	if _, err := svc.Update(ctx, alice, domain.Preferences{Handedness: domain.HandLeft}); err != nil {
		t.Fatal(err)
	}
	if p, _ := svc.Get(ctx, alice); p.Handedness != domain.HandLeft {
		t.Fatalf("alice: %+v", p)
	}
	if p, _ := svc.Get(ctx, bob); p.Handedness != domain.HandRight {
		t.Fatalf("las preferencias de bob no deben cambiar: %+v", p)
	}
}

func TestUpdatePreferencesRejectsInvalidHandedness(t *testing.T) {
	svc, repo := newPrefsFixture()
	for _, h := range []domain.Handedness{"", "ambidextro"} {
		if _, err := svc.Update(context.Background(), alice, domain.Preferences{Handedness: h}); !errors.Is(err, domain.ErrInvalidInput) {
			t.Fatalf("%q: se esperaba ErrInvalidInput, got %v", h, err)
		}
	}
	if repo.byUser[alice].Handedness != domain.HandRight {
		t.Fatal("no debe guardar valores inválidos")
	}
}

func TestUpdatePreferencesPropagatesStorageError(t *testing.T) {
	svc, repo := newPrefsFixture()
	repo.saveErr = errors.New("db caída")
	if _, err := svc.Update(context.Background(), alice, domain.Preferences{Handedness: domain.HandLeft}); err == nil {
		t.Fatal("se esperaba error")
	}
}
