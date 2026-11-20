package domain

import (
	"encoding/json"
	"fmt"
	"net/mail"
	"slices"
	"strings"
	"time"
)

// Principal es la identidad verificada que llega en el token. (Issuer, Subject) es la clave
// estable del usuario: `sub` solo es único dentro de su emisor.
type Principal struct {
	Issuer        string
	Subject       string
	Email         string
	EmailVerified bool // solo un email verificado puede recibir roles (invitación o bootstrap)
}

// Role es un rol adicional al de usuario. Todo usuario autenticado puede subir videos y ver sus
// análisis; los roles se acumulan (una persona puede ser experto y validador a la vez).
type Role string

const (
	RoleAdmin     Role = "admin"     // gestiona usuarios, roles e invitaciones; ve la auditoría
	RoleExpert    Role = "expert"    // propone cambios a la base de conocimiento
	RoleValidator Role = "validator" // aprueba o rechaza propuestas (nunca las suyas)
)

var assignableRoles = []Role{RoleAdmin, RoleExpert, RoleValidator}

// ParseRoles valida, elimina duplicados y ordena: el resultado es estable para comparar y guardar.
func ParseRoles(raw []string) ([]Role, error) {
	roles := make([]Role, 0, len(raw))
	for _, r := range raw {
		role := Role(strings.TrimSpace(r))
		if !slices.Contains(assignableRoles, role) {
			return nil, invalid("roles", assignableRoles)
		}
		if !slices.Contains(roles, role) {
			roles = append(roles, role)
		}
	}
	slices.Sort(roles)
	return roles, nil
}

type UserStatus string

const (
	UserActive    UserStatus = "active"
	UserSuspended UserStatus = "suspended"
)

var userStatuses = []UserStatus{UserActive, UserSuspended}

func (s UserStatus) Validate() error {
	if !slices.Contains(userStatuses, s) {
		return invalid("status", userStatuses)
	}
	return nil
}

// User es el usuario interno (users.id) con sus roles.
type User struct {
	ID            string
	Email         string
	EmailVerified bool
	Roles         []Role
	Status        UserStatus
	CreatedAt     time.Time
}

// Has indica si el usuario tiene al menos uno de los roles indicados.
func (u User) Has(roles ...Role) bool {
	for _, r := range roles {
		if slices.Contains(u.Roles, r) {
			return true
		}
	}
	return false
}

// Specialty es la disciplina profesional de un experto o validador.
type Specialty string

var specialties = []Specialty{"tennis_coach", "biomechanics", "physio", "fitness"}

// Profile describe al profesional (experto o validador). Se ve solo dentro del portal.
type Profile struct {
	DisplayName    string
	Specialty      Specialty
	Certifications string
	Bio            string
	UpdatedAt      time.Time
}

func (p Profile) Validate() error {
	if strings.TrimSpace(p.DisplayName) == "" || len(p.DisplayName) > 120 {
		return fmt.Errorf("%w: display_name es obligatorio (máx. 120 caracteres)", ErrInvalidInput)
	}
	if !slices.Contains(specialties, p.Specialty) {
		return invalid("specialty", specialties)
	}
	if len(p.Certifications) > 2000 || len(p.Bio) > 4000 {
		return fmt.Errorf("%w: certifications (máx. 2000) o bio (máx. 4000) demasiado largos", ErrInvalidInput)
	}
	return nil
}

// InvitationTTL es lo que dura una invitación sin aceptar.
const InvitationTTL = 14 * 24 * time.Hour

// Invitation reserva roles para un email. Se acepta sola cuando esa persona inicia sesión con el
// email VERIFICADO por el proveedor de identidad.
type Invitation struct {
	ID         string
	Email      string
	Roles      []Role
	InvitedBy  string
	CreatedAt  time.Time
	ExpiresAt  time.Time
	AcceptedAt *time.Time
	RevokedAt  *time.Time
}

// NormalizeEmail valida el formato y lo pasa a minúsculas (la comparación es sin mayúsculas).
func NormalizeEmail(raw string) (string, error) {
	addr, err := mail.ParseAddress(strings.TrimSpace(raw))
	if err != nil || addr.Address != strings.TrimSpace(raw) {
		return "", fmt.Errorf("%w: email inválido", ErrInvalidInput)
	}
	return strings.ToLower(addr.Address), nil
}

// AuditEntry registra quién hizo qué. ActorID nil = el sistema.
type AuditEntry struct {
	ID        int64
	ActorID   *string
	Action    string
	Target    string
	Details   json.RawMessage
	CreatedAt time.Time
}
