// Package domain contiene las entidades y reglas del análisis, sin dependencias de infraestructura.
package domain

import (
	"fmt"
	"slices"
	"time"

	"github.com/google/uuid"
)

type StrokeType string

const (
	StrokeForehand StrokeType = "forehand"
	StrokeBackhand StrokeType = "backhand"
	StrokeServe    StrokeType = "serve"
	StrokeVolley   StrokeType = "volley"
)

type Handedness string

const (
	HandRight Handedness = "right"
	HandLeft  Handedness = "left"
)

type CameraView string

const (
	ViewSide  CameraView = "side"
	ViewFront CameraView = "front"
	ViewBack  CameraView = "back"
)

type Status string

const (
	StatusAwaitingUpload Status = "awaiting_upload"
	StatusQueued         Status = "queued"
	StatusProcessing     Status = "processing"
	StatusCompleted      Status = "completed"
	StatusFailed         Status = "failed"
)

var (
	strokeTypes = []StrokeType{StrokeForehand, StrokeBackhand, StrokeServe, StrokeVolley}
	handedness  = []Handedness{HandRight, HandLeft}
	cameraViews = []CameraView{ViewSide, ViewFront, ViewBack}
)

// AnalysisParams son los datos que aporta el usuario al crear un análisis.
type AnalysisParams struct {
	StrokeType StrokeType
	Handedness Handedness
	CameraView CameraView
	// ExpertReviewAllowed: checkbox "Permitir que expertos vean este video" (marcado por defecto).
	// Solo sirve para el control de calidad de las reglas; el análisis siempre es automático.
	ExpertReviewAllowed bool
}

// WithDefaults completa los campos opcionales (Postel: tolerante en la entrada).
func (p AnalysisParams) WithDefaults() AnalysisParams {
	if p.Handedness == "" {
		p.Handedness = HandRight
	}
	if p.CameraView == "" {
		p.CameraView = ViewSide
	}
	return p
}

// StrokeAuto (vacío) pide que el worker detecte el golpe con el clasificador automático.
const StrokeAuto StrokeType = ""

func (p AnalysisParams) Validate() error {
	if p.StrokeType != StrokeAuto && !slices.Contains(strokeTypes, p.StrokeType) {
		return invalid("stroke_type", strokeTypes)
	}
	if !slices.Contains(handedness, p.Handedness) {
		return invalid("handedness", handedness)
	}
	if !slices.Contains(cameraViews, p.CameraView) {
		return invalid("camera_view", cameraViews)
	}
	return nil
}

func invalid[T ~string](field string, allowed []T) error {
	return fmt.Errorf("%w: %s debe ser uno de %v", ErrInvalidInput, field, allowed)
}

// Analysis es el agregado principal: un video y el estado de su análisis.
type Analysis struct {
	ID           string
	UserID       *string
	Params       AnalysisParams
	Status       Status
	VideoKey     string
	Attempts     int
	ErrorCode    *string
	ErrorMessage *string
	Report       []byte
	KBVersion    *string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

// Cursor devuelve la posición de este análisis en un listado paginado.
func (a Analysis) Cursor() Cursor {
	return Cursor{CreatedAt: a.CreatedAt, ID: a.ID}
}

// Deletable es falso mientras el worker puede estar escribiendo resultados del análisis.
func (a Analysis) Deletable() bool {
	return a.Status != StatusQueued && a.Status != StatusProcessing
}

// ObjectKeys lista los objetos del almacenamiento que pertenecen al análisis: el video
// subido y la secuencia de pose que escribe el worker (contracts/pose_sequence.schema.json).
func (a Analysis) ObjectKeys() []string {
	return []string{a.VideoKey, "poses/" + a.ID + ".json"}
}

// NewAnalysis crea un análisis que pertenece a ownerID (users.id).
func NewAnalysis(id, ownerID string, p AnalysisParams, now time.Time) Analysis {
	return Analysis{
		ID:        id,
		UserID:    &ownerID,
		Params:    p,
		Status:    StatusAwaitingUpload,
		VideoKey:  "videos/" + id,
		CreatedAt: now,
		UpdatedAt: now,
	}
}

// IsValidID evita consultas a la base de datos con identificadores mal formados.
func IsValidID(id string) bool {
	_, err := uuid.Parse(id)
	return err == nil
}

// ObjectInfo describe un objeto subido al almacenamiento.
type ObjectInfo struct {
	Size        int64
	ContentType string
}
