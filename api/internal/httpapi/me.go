package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
)

type ProfileUseCases interface {
	Get(ctx context.Context, userID string) (*domain.Profile, error)
	Save(ctx context.Context, user domain.User, p domain.Profile) (domain.Profile, error)
}

type profileDTO struct {
	DisplayName    string           `json:"display_name"`
	Specialty      domain.Specialty `json:"specialty"`
	Certifications string           `json:"certifications"`
	Bio            string           `json:"bio"`
	UpdatedAt      *time.Time       `json:"updated_at,omitempty"`
}

func toProfileDTO(p *domain.Profile) *profileDTO {
	if p == nil {
		return nil
	}
	updated := p.UpdatedAt
	return &profileDTO{DisplayName: p.DisplayName, Specialty: p.Specialty, Certifications: p.Certifications,
		Bio: p.Bio, UpdatedAt: &updated}
}

type userDTO struct {
	ID            string            `json:"id"`
	Email         string            `json:"email"`
	EmailVerified bool              `json:"email_verified"`
	Roles         []domain.Role     `json:"roles"`
	Status        domain.UserStatus `json:"status"`
	CreatedAt     time.Time         `json:"created_at"`
}

func toUserDTO(u domain.User) userDTO {
	roles := u.Roles
	if roles == nil {
		roles = []domain.Role{}
	}
	return userDTO{ID: u.ID, Email: u.Email, EmailVerified: u.EmailVerified, Roles: roles, Status: u.Status, CreatedAt: u.CreatedAt}
}

type meResponse struct {
	userDTO
	Profile *profileDTO `json:"profile"`
}

type meHandler struct {
	profiles ProfileUseCases
	logger   *slog.Logger
}

// get devuelve quién soy y qué roles tengo: los clientes deciden qué navegación mostrar con esto.
func (h *meHandler) get(c *gin.Context) {
	user := currentUser(c)
	var profile *domain.Profile
	if user.Has(domain.RoleExpert, domain.RoleValidator) {
		p, err := h.profiles.Get(c.Request.Context(), user.ID)
		if err != nil {
			writeError(c, h.logger, err)
			return
		}
		profile = p
	}
	c.JSON(http.StatusOK, meResponse{userDTO: toUserDTO(user), Profile: toProfileDTO(profile)})
}

func (h *meHandler) putProfile(c *gin.Context) {
	var req profileDTO
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	p, err := h.profiles.Save(c.Request.Context(), currentUser(c), domain.Profile{
		DisplayName: req.DisplayName, Specialty: req.Specialty, Certifications: req.Certifications, Bio: req.Bio,
	})
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toProfileDTO(&p))
}

const maxJSONBody = 1 << 20 // 1 MiB: una propuesta grande sigue cabiendo de sobra

// decodeStrict rechaza campos desconocidos: un error de tipeo en un umbral no debe ignorarse en silencio.
func decodeStrict(c *gin.Context, dst any) error {
	body, err := io.ReadAll(io.LimitReader(c.Request.Body, maxJSONBody+1))
	if err != nil || len(body) > maxJSONBody {
		return fmt.Errorf("%w: cuerpo ilegible o demasiado grande", domain.ErrInvalidInput)
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return fmt.Errorf("%w: JSON inválido: %s", domain.ErrInvalidInput, err.Error())
	}
	return nil
}
