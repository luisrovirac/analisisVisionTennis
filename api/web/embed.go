// Package web embebe el cliente web mínimo para servirlo desde la API.
package web

import (
	"embed"
	"io/fs"
)

//go:embed static
var files embed.FS

// Static devuelve el contenido de static/ como raíz.
func Static() (fs.FS, error) {
	return fs.Sub(files, "static")
}
