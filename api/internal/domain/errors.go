package domain

import "errors"

var (
	ErrInvalidInput     = errors.New("entrada inválida")
	ErrNotFound         = errors.New("análisis no encontrado")
	ErrObjectNotFound   = errors.New("objeto no encontrado en el almacenamiento")
	ErrUploadMissing    = errors.New("el video aún no se ha subido")
	ErrUploadTooLarge   = errors.New("el video supera el tamaño máximo")
	ErrUnsupportedMedia = errors.New("el archivo subido no es un video")
	ErrAnalysisBusy     = errors.New("el análisis se está procesando")
	// ErrNotReady: el análisis existe pero aún no tiene resultados (video y pose) que mostrar.
	ErrNotReady = errors.New("el análisis aún no ha terminado")

	// ErrResourceNotFound es el "no existe" genérico (propuestas, versiones, usuarios...).
	ErrResourceNotFound = errors.New("recurso no encontrado")
	// ErrForbidden: autenticado, pero sin el rol o el derecho necesario para esta acción.
	ErrForbidden = errors.New("acción no permitida")
	// ErrAccountSuspended: el admin suspendió la cuenta.
	ErrAccountSuspended = errors.New("cuenta suspendida")
	// ErrConflict: el estado cambió entretanto (p. ej. la base de conocimiento activa ya no es la
	// versión sobre la que se escribió la propuesta).
	ErrConflict = errors.New("conflicto de estado")

	// ErrCampaignClosed: la campaña de revisión ya cerró; sus calificaciones son definitivas.
	ErrCampaignClosed = errors.New("la campaña está cerrada")
	// ErrNotARater: el usuario no es evaluador de esa campaña.
	ErrNotARater = errors.New("no eres evaluador de esta campaña")
	// ErrEvidenceInsufficient: los datos no alcanzan para marcar la regla como validada empíricamente.
	ErrEvidenceInsufficient = errors.New("evidencia insuficiente")
)

// ErrUnauthenticated envuelve cualquier fallo al verificar el token. El motivo concreto
// (firma, expiración, audiencia) se registra en logs, pero no se revela al cliente.
var ErrUnauthenticated = errors.New("no autenticado")
