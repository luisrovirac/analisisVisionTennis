package domain

import "slices"

// Preferences son los ajustes que el usuario conserva entre sesiones y dispositivos.
type Preferences struct {
	Handedness Handedness
}

func (p Preferences) Validate() error {
	if !slices.Contains(handedness, p.Handedness) {
		return invalid("handedness", handedness)
	}
	return nil
}
