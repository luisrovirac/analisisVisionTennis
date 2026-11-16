package service

import (
	"context"
	"errors"
	"slices"
	"testing"
	"time"

	"tennisvision/api/internal/domain"
)

const (
	testID = "3f1c2b8e-6d0a-4c7e-9a51-2a8f0c7d9b10"
	alice  = "aaaaaaaa-0000-4000-8000-000000000001"
	bob    = "bbbbbbbb-0000-4000-8000-000000000002"
)

type fakeRepo struct {
	items         map[string]domain.Analysis
	transitionErr error
	loseRace      bool
}

func newFakeRepo() *fakeRepo { return &fakeRepo{items: map[string]domain.Analysis{}} }

func (r *fakeRepo) Create(_ context.Context, a domain.Analysis) error {
	r.items[a.ID] = a
	return nil
}

func (r *fakeRepo) Get(_ context.Context, ownerID, id string) (domain.Analysis, error) {
	a, ok := r.items[id]
	if !ok || a.UserID == nil || *a.UserID != ownerID {
		return domain.Analysis{}, domain.ErrNotFound
	}
	return a, nil
}

// List reproduce el orden y el filtro del SQL real: (created_at, id) descendente, solo del dueño.
func (r *fakeRepo) List(_ context.Context, ownerID string, after *domain.Cursor, limit int) ([]domain.Analysis, error) {
	var out []domain.Analysis
	for _, a := range r.items {
		if *a.UserID == ownerID && (after == nil || before(a.Cursor(), *after)) {
			out = append(out, a)
		}
	}
	slices.SortFunc(out, func(x, y domain.Analysis) int {
		if before(x.Cursor(), y.Cursor()) {
			return 1
		}
		return -1
	})
	return out[:min(limit, len(out))], nil
}

func before(a, b domain.Cursor) bool {
	return a.CreatedAt.Before(b.CreatedAt) || (a.CreatedAt.Equal(b.CreatedAt) && a.ID < b.ID)
}

func (r *fakeRepo) Transition(_ context.Context, id string, from, to domain.Status) (bool, error) {
	if r.transitionErr != nil {
		return false, r.transitionErr
	}
	a := r.items[id]
	if r.loseRace {
		a.Status = domain.StatusProcessing
		r.items[id] = a
		return false, nil
	}
	if a.Status != from {
		return false, nil
	}
	a.Status = to
	r.items[id] = a
	return true, nil
}

type fakeStore struct {
	info       domain.ObjectInfo
	statErr    error
	presignErr error
}

func (s *fakeStore) PresignPut(_ context.Context, key string, _ time.Duration) (string, error) {
	return "http://storage/" + key, s.presignErr
}

func (s *fakeStore) Stat(_ context.Context, _ string) (domain.ObjectInfo, error) {
	return s.info, s.statErr
}

type fakePublisher struct {
	published []domain.Analysis
	err       error
}

func (p *fakePublisher) Publish(_ context.Context, a domain.Analysis) error {
	if p.err != nil {
		return p.err
	}
	p.published = append(p.published, a)
	return nil
}

var fixedNow = time.Date(2026, 11, 15, 10, 0, 0, 0, time.UTC)

type fixture struct {
	svc   *AnalysisService
	repo  *fakeRepo
	store *fakeStore
	pub   *fakePublisher
}

func newFixture() fixture {
	f := fixture{
		repo:  newFakeRepo(),
		store: &fakeStore{info: domain.ObjectInfo{Size: 1 << 20, ContentType: "video/mp4"}},
		pub:   &fakePublisher{},
	}
	cfg := Config{UploadURLTTL: 15 * time.Minute, MaxUploadBytes: 10 << 20}
	f.svc = New(f.repo, f.store, f.pub, func() time.Time { return fixedNow }, func() string { return testID }, cfg)
	return f
}

func (f fixture) createAnalysis(t *testing.T) {
	t.Helper()
	if _, err := f.svc.Create(context.Background(), alice, domain.AnalysisParams{StrokeType: domain.StrokeForehand}); err != nil {
		t.Fatal(err)
	}
}

func TestCreateReturnsTicketAndPersists(t *testing.T) {
	f := newFixture()
	ticket, err := f.svc.Create(context.Background(), alice, domain.AnalysisParams{StrokeType: domain.StrokeForehand})
	if err != nil {
		t.Fatal(err)
	}
	if ticket.UploadURL != "http://storage/videos/"+testID || !ticket.ExpiresAt.Equal(fixedNow.Add(15*time.Minute)) {
		t.Fatalf("ticket inesperado: %+v", ticket)
	}
	stored := f.repo.items[testID]
	if stored.Status != domain.StatusAwaitingUpload || stored.Params.Handedness != domain.HandRight || *stored.UserID != alice {
		t.Fatalf("análisis guardado inesperado: %+v", stored)
	}
}

func TestCreateRejectsInvalidParams(t *testing.T) {
	f := newFixture()
	_, err := f.svc.Create(context.Background(), alice, domain.AnalysisParams{StrokeType: "smash"})
	if !errors.Is(err, domain.ErrInvalidInput) {
		t.Fatalf("se esperaba ErrInvalidInput, got %v", err)
	}
	if len(f.repo.items) != 0 {
		t.Fatal("no debe persistir un análisis inválido")
	}
}

func TestCreateFailsWhenPresignFails(t *testing.T) {
	f := newFixture()
	f.store.presignErr = errors.New("s3 caído")
	_, err := f.svc.Create(context.Background(), alice, domain.AnalysisParams{StrokeType: domain.StrokeForehand})
	if err == nil || len(f.repo.items) != 0 {
		t.Fatalf("se esperaba error sin persistencia, err=%v", err)
	}
}

func TestSubmitQueuesAndPublishes(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	a, err := f.svc.Submit(context.Background(), alice, testID)
	if err != nil {
		t.Fatal(err)
	}
	if a.Status != domain.StatusQueued || len(f.pub.published) != 1 {
		t.Fatalf("status=%s publicados=%d", a.Status, len(f.pub.published))
	}
}

func TestSubmitIsIdempotent(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	for range 3 {
		if _, err := f.svc.Submit(context.Background(), alice, testID); err != nil {
			t.Fatal(err)
		}
	}
	if len(f.pub.published) != 1 {
		t.Fatalf("se publicó %d veces, se esperaba 1", len(f.pub.published))
	}
}

func TestSubmitUploadErrors(t *testing.T) {
	cases := map[string]struct {
		store fakeStore
		want  error
	}{
		"sin subir":   {fakeStore{statErr: domain.ErrObjectNotFound}, domain.ErrUploadMissing},
		"muy grande":  {fakeStore{info: domain.ObjectInfo{Size: 11 << 20, ContentType: "video/mp4"}}, domain.ErrUploadTooLarge},
		"no es video": {fakeStore{info: domain.ObjectInfo{Size: 10, ContentType: "image/png"}}, domain.ErrUnsupportedMedia},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFixture()
			*f.store = tc.store
			f.createAnalysis(t)
			_, err := f.svc.Submit(context.Background(), alice, testID)
			if !errors.Is(err, tc.want) {
				t.Fatalf("got %v, want %v", err, tc.want)
			}
			if f.repo.items[testID].Status != domain.StatusAwaitingUpload {
				t.Fatal("el estado no debe cambiar si la subida no es válida")
			}
		})
	}
}

func TestSubmitRollsBackWhenPublishFails(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	f.pub.err = errors.New("redis caído")
	if _, err := f.svc.Submit(context.Background(), alice, testID); err == nil {
		t.Fatal("se esperaba error")
	}
	if f.repo.items[testID].Status != domain.StatusAwaitingUpload {
		t.Fatalf("debe revertir a awaiting_upload, está en %s", f.repo.items[testID].Status)
	}
}

func TestSubmitReturnsCurrentStateWhenRaceLost(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	f.repo.loseRace = true
	a, err := f.svc.Submit(context.Background(), alice, testID)
	if err != nil {
		t.Fatal(err)
	}
	if a.Status != domain.StatusProcessing || len(f.pub.published) != 0 {
		t.Fatalf("status=%s publicados=%d", a.Status, len(f.pub.published))
	}
}

func TestSubmitPropagatesTransitionError(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	f.repo.transitionErr = errors.New("db caída")
	if _, err := f.svc.Submit(context.Background(), alice, testID); err == nil {
		t.Fatal("se esperaba error")
	}
}

func TestGetRejectsMalformedID(t *testing.T) {
	f := newFixture()
	if _, err := f.svc.Get(context.Background(), alice, "xyz"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("se esperaba ErrNotFound, got %v", err)
	}
}

func TestOtherUsersCannotSeeOrSubmit(t *testing.T) {
	f := newFixture()
	f.createAnalysis(t)
	if _, err := f.svc.Get(context.Background(), bob, testID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("Get ajeno: se esperaba ErrNotFound, got %v", err)
	}
	if _, err := f.svc.Submit(context.Background(), bob, testID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("Submit ajeno: se esperaba ErrNotFound, got %v", err)
	}
	if len(f.pub.published) != 0 || f.repo.items[testID].Status != domain.StatusAwaitingUpload {
		t.Fatal("un usuario ajeno no debe poder encolar el análisis")
	}
}

func TestListPaginatesOwnAnalysesNewestFirst(t *testing.T) {
	f := newFixture()
	ids := []string{
		"00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
		"00000000-0000-4000-8000-000000000003",
	}
	for i, id := range ids {
		f.repo.items[id] = domain.NewAnalysis(id, alice, domain.AnalysisParams{}, fixedNow.Add(time.Duration(i)*time.Minute))
	}
	f.repo.items[testID] = domain.NewAnalysis(testID, bob, domain.AnalysisParams{}, fixedNow)

	first, err := f.svc.List(context.Background(), alice, domain.ListQuery{Limit: 2})
	if err != nil || len(first.Items) != 2 || first.Items[0].ID != ids[2] || first.NextCursor == "" {
		t.Fatalf("primera página: %+v err=%v", first, err)
	}
	after, _ := domain.DecodeCursor(first.NextCursor)
	second, err := f.svc.List(context.Background(), alice, domain.ListQuery{After: &after, Limit: 2})
	if err != nil || len(second.Items) != 1 || second.Items[0].ID != ids[0] || second.NextCursor != "" {
		t.Fatalf("segunda página: %+v err=%v", second, err)
	}
}
