package domain

import "errors"

var (
	ErrInvalidInput     = errors.New("entrada inválida")
	ErrNotFound         = errors.New("análisis no encontrado")
	ErrObjectNotFound   = errors.New("objeto no encontrado en el almacenamiento")
	ErrUploadMissing    = errors.New("el video aún no se ha subido")
	ErrUploadTooLarge   = errors.New("el video supera el tamaño máximo")
	ErrUnsupportedMedia = errors.New("el archivo subido no es un video")
)

// ErrUnauthenticated envuelve cualquier fallo al verificar el token. El motivo concreto
// (firma, expiración, audiencia) se registra en logs, pero no se revela al cliente.
var ErrUnauthenticated = errors.New("no autenticado")
