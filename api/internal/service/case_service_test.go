package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
)

type fakeCases struct {
	items    []domain.Analysis
	gotLimit int
}

func (f *fakeCases) Cases(_ context.Context, _ domain.CaseFilter, limit int) ([]domain.Analysis, error) {
	f.gotLimit = limit
	if len(f.items) > limit {
		return f.items[:limit], nil
	}
	return f.items, nil
}

func (f *fakeCases) Case(_ context.Context, id string) (domain.Analysis, error) {
	for _, a := range f.items {
		if a.ID == id {
			return a, nil
		}
	}
	return domain.Analysis{}, domain.ErrNotFound
}

func completedCase(id string, at time.Time) domain.Analysis {
	return domain.Analysis{ID: id, Status: domain.StatusCompleted, VideoKey: "videos/" + id, CreatedAt: at}
}

func TestCaseListPaginates(t *testing.T) {
	repo := &fakeCases{items: []domain.Analysis{
		completedCase(testID, fixedNow), completedCase(bob, fixedNow.Add(-time.Hour)),
	}}
	svc := NewCases(repo, &fakeStore{}, 10*time.Minute, func() time.Time { return fixedNow })
	page, err := svc.List(context.Background(), domain.CaseFilter{Query: domain.ListQuery{Limit: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if repo.gotLimit != 2 || len(page.Items) != 1 || page.NextCursor == "" {
		t.Fatalf("página inesperada: limit=%d %+v", repo.gotLimit, page)
	}
}

func TestCaseGetSignsMedia(t *testing.T) {
	repo := &fakeCases{items: []domain.Analysis{completedCase(testID, fixedNow)}}
	svc := NewCases(repo, &fakeStore{}, 10*time.Minute, func() time.Time { return fixedNow })
	d, err := svc.Get(context.Background(), testID)
	if err != nil {
		t.Fatal(err)
	}
	if d.Media.PoseURL != "http://storage/get/poses/"+testID+".json" || !d.Media.ExpiresAt.Equal(fixedNow.Add(10*time.Minute)) {
		t.Fatalf("medios inesperados: %+v", d.Media)
	}
	if _, err := svc.Get(context.Background(), "no-uuid"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("id mal formado: %v", err)
	}
	if _, err := svc.Get(context.Background(), bob); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("caso inexistente o revocado: %v", err)
	}
}
