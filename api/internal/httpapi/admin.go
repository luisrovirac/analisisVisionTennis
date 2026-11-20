package httpapi

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"tennisvision/api/internal/domain"
	"tennisvision/api/internal/service"
)

type AdminUseCases interface {
	ListUsers(ctx context.Context, query string) ([]domain.User, error)
	SetRoles(ctx context.Context, actor domain.User, userID string, roles []string) (domain.User, error)
	SetStatus(ctx context.Context, actor domain.User, userID string, status domain.UserStatus) (domain.User, error)
	Invite(ctx context.Context, actor domain.User, email string, roles []string) (service.InviteResult, error)
	Invitations(ctx context.Context) ([]domain.Invitation, error)
	RevokeInvitation(ctx context.Context, actor domain.User, id string) error
	Audit(ctx context.Context) ([]domain.AuditEntry, error)
}

type adminHandler struct {
	svc    AdminUseCases
	logger *slog.Logger
}

func (h *adminHandler) listUsers(c *gin.Context) {
	users, err := h.svc.ListUsers(c.Request.Context(), c.Query("q"))
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	items := make([]userDTO, 0, len(users))
	for _, u := range users {
		items = append(items, toUserDTO(u))
	}
	c.JSON(http.StatusOK, gin.H{"items": items})
}

func (h *adminHandler) setRoles(c *gin.Context) {
	var req struct {
		Roles []string `json:"roles"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	u, err := h.svc.SetRoles(c.Request.Context(), currentUser(c), c.Param("id"), req.Roles)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toUserDTO(u))
}

func (h *adminHandler) setStatus(c *gin.Context) {
	var req struct {
		Status domain.UserStatus `json:"status"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	u, err := h.svc.SetStatus(c.Request.Context(), currentUser(c), c.Param("id"), req.Status)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.JSON(http.StatusOK, toUserDTO(u))
}

type invitationDTO struct {
	ID         string        `json:"id"`
	Email      string        `json:"email"`
	Roles      []domain.Role `json:"roles"`
	Status     string        `json:"status"` // pending | accepted | revoked | expired
	CreatedAt  time.Time     `json:"created_at"`
	ExpiresAt  time.Time     `json:"expires_at"`
	AcceptedAt *time.Time    `json:"accepted_at"`
}

func toInvitationDTO(inv domain.Invitation, now time.Time) invitationDTO {
	status := "pending"
	switch {
	case inv.AcceptedAt != nil:
		status = "accepted"
	case inv.RevokedAt != nil:
		status = "revoked"
	case now.After(inv.ExpiresAt):
		status = "expired"
	}
	return invitationDTO{ID: inv.ID, Email: inv.Email, Roles: inv.Roles, Status: status, CreatedAt: inv.CreatedAt,
		ExpiresAt: inv.ExpiresAt, AcceptedAt: inv.AcceptedAt}
}

func (h *adminHandler) invite(c *gin.Context) {
	var req struct {
		Email string   `json:"email"`
		Roles []string `json:"roles"`
	}
	if err := decodeStrict(c, &req); err != nil {
		writeError(c, h.logger, err)
		return
	}
	res, err := h.svc.Invite(c.Request.Context(), currentUser(c), req.Email, req.Roles)
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	if res.AppliedUser != nil {
		c.JSON(http.StatusOK, gin.H{"applied_to_existing_user": true, "user": toUserDTO(*res.AppliedUser)})
		return
	}
	c.JSON(http.StatusCreated, gin.H{"applied_to_existing_user": false, "invitation": toInvitationDTO(*res.Invitation, time.Now())})
}

func (h *adminHandler) invitations(c *gin.Context) {
	items, err := h.svc.Invitations(c.Request.Context())
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	now := time.Now()
	out := make([]invitationDTO, 0, len(items))
	for _, inv := range items {
		out = append(out, toInvitationDTO(inv, now))
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}

func (h *adminHandler) revokeInvitation(c *gin.Context) {
	if err := h.svc.RevokeInvitation(c.Request.Context(), currentUser(c), c.Param("id")); err != nil {
		writeError(c, h.logger, err)
		return
	}
	c.Status(http.StatusNoContent)
}

type auditDTO struct {
	ID        int64           `json:"id"`
	ActorID   *string         `json:"actor_id"`
	Action    string          `json:"action"`
	Target    string          `json:"target"`
	Details   json.RawMessage `json:"details"`
	CreatedAt time.Time       `json:"created_at"`
}

func (h *adminHandler) audit(c *gin.Context) {
	items, err := h.svc.Audit(c.Request.Context())
	if err != nil {
		writeError(c, h.logger, err)
		return
	}
	out := make([]auditDTO, 0, len(items))
	for _, e := range items {
		out = append(out, auditDTO{ID: e.ID, ActorID: e.ActorID, Action: e.Action, Target: e.Target,
			Details: rawOrNull(e.Details), CreatedAt: e.CreatedAt})
	}
	c.JSON(http.StatusOK, gin.H{"items": out})
}
