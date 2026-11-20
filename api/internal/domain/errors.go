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

	// ErrResourceNotFound es el "no existe" genérico (propuestas, versiones, usuarios...).
	ErrResourceNotFound = errors.New("recurso no encontrado")
	// ErrForbidden: autenticado, pero sin el rol o el derecho necesario para esta acción.
	ErrForbidden = errors.New("acción no permitida")
	// ErrAccountSuspended: el admin suspendió la cuenta.
	ErrAccountSuspended = errors.New("cuenta suspendida")
	// ErrConflict: el estado cambió entretanto (p. ej. la base de conocimiento activa ya no es la
	// versión sobre la que se escribió la propuesta).
	ErrConflict = errors.New("conflicto de estado")
)

// ErrUnauthenticated envuelve cualquier fallo al verificar el token. El motivo concreto
// (firma, expiración, audiencia) se registra en logs, pero no se revela al cliente.
var ErrUnauthenticated = errors.New("no autenticado")
