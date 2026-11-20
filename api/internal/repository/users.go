package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"tennisvision/api/internal/domain"
)

// PostgresUsers persiste usuarios, roles, perfiles profesionales, invitaciones y auditoría.
type PostgresUsers struct {
	pool *pgxpool.Pool
}

func NewPostgresUsers(pool *pgxpool.Pool) *PostgresUsers {
	return &PostgresUsers{pool: pool}
}

// userColumns devuelve el usuario con sus roles en una sola consulta (sin escrituras por petición).
const userColumns = `
SELECT u.id::text AS id, COALESCE(u.email, '') AS email, u.email_verified, u.status, u.created_at,
       COALESCE((SELECT array_agg(r.role ORDER BY r.role) FROM user_roles r WHERE r.user_id = u.id), '{}') AS roles
FROM users u`

// findByIdentitySQL añade si hay una invitación vigente para el email del token.
const findByIdentitySQL = `
SELECT q.*, EXISTS (
    SELECT 1 FROM invitations i
    WHERE $3 <> '' AND lower(i.email) = lower($3)
      AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now())
FROM (` + userColumns + ` WHERE u.issuer = $1 AND u.subject = $2) q`

func scanUser(row pgx.Row, extra ...any) (domain.User, error) {
	var u domain.User
	var roles []string
	dest := append([]any{&u.ID, &u.Email, &u.EmailVerified, &u.Status, &u.CreatedAt, &roles}, extra...)
	if err := row.Scan(dest...); err != nil {
		return domain.User{}, err
	}
	for _, r := range roles {
		u.Roles = append(u.Roles, domain.Role(r))
	}
	return u, nil
}

// FindByIdentity busca al usuario del token y, de paso, si tiene una invitación pendiente.
func (u *PostgresUsers) FindByIdentity(ctx context.Context, p domain.Principal) (domain.User, bool, bool, error) {
	var pending bool
	user, err := scanUser(u.pool.QueryRow(ctx, findByIdentitySQL, p.Issuer, p.Subject, p.Email), &pending)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.User{}, false, false, nil
	}
	if err != nil {
		return domain.User{}, false, false, fmt.Errorf("buscar usuario: %w", err)
	}
	return user, pending, true, nil
}

// DO UPDATE (y no DO NOTHING) para que RETURNING devuelva el id también si otra petición
// concurrente del mismo usuario lo insertó entre la búsqueda y la inserción.
const insertUserSQL = `
INSERT INTO users (issuer, subject, email, email_verified) VALUES ($1, $2, NULLIF($3, ''), $4)
ON CONFLICT (issuer, subject) DO UPDATE SET email = COALESCE(EXCLUDED.email, users.email)
RETURNING id::text`

func (u *PostgresUsers) CreateUser(ctx context.Context, p domain.Principal) (domain.User, error) {
	var id string
	if err := u.pool.QueryRow(ctx, insertUserSQL, p.Issuer, p.Subject, p.Email, p.EmailVerified).Scan(&id); err != nil {
		return domain.User{}, fmt.Errorf("registrar usuario: %w", err)
	}
	return u.User(ctx, id)
}

const updateEmailSQL = `UPDATE users SET email = NULLIF($2, ''), email_verified = $3 WHERE id = $1::uuid`

func (u *PostgresUsers) UpdateEmail(ctx context.Context, userID, email string, verified bool) error {
	if _, err := u.pool.Exec(ctx, updateEmailSQL, userID, email, verified); err != nil {
		return fmt.Errorf("actualizar email: %w", err)
	}
	return nil
}

func (u *PostgresUsers) User(ctx context.Context, id string) (domain.User, error) {
	user, err := scanUser(u.pool.QueryRow(ctx, userColumns+` WHERE u.id = $1::uuid`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.User{}, domain.ErrResourceNotFound
	}
	if err != nil {
		return domain.User{}, fmt.Errorf("consultar usuario: %w", err)
	}
	return user, nil
}

const listUsersSQL = userColumns + `
WHERE $1 = '' OR u.email ILIKE '%' || $1 || '%'
ORDER BY u.created_at DESC
LIMIT $2`

func (u *PostgresUsers) ListUsers(ctx context.Context, query string, limit int) ([]domain.User, error) {
	rows, err := u.pool.Query(ctx, listUsersSQL, query, limit)
	if err != nil {
		return nil, fmt.Errorf("listar usuarios: %w", err)
	}
	users, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.User, error) { return scanUser(row) })
	if err != nil {
		return nil, fmt.Errorf("leer usuarios: %w", err)
	}
	return users, nil
}

// FindVerifiedByEmail busca a un usuario ya registrado con ese email verificado.
func (u *PostgresUsers) FindVerifiedByEmail(ctx context.Context, email string) (domain.User, bool, error) {
	user, err := scanUser(u.pool.QueryRow(ctx,
		userColumns+` WHERE lower(u.email) = lower($1) AND u.email_verified ORDER BY u.created_at LIMIT 1`, email))
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.User{}, false, nil
	}
	if err != nil {
		return domain.User{}, false, fmt.Errorf("buscar usuario por email: %w", err)
	}
	return user, true, nil
}

// SetRoles reemplaza el conjunto de roles y lo audita en la misma transacción.
func (u *PostgresUsers) SetRoles(ctx context.Context, actorID, userID string, roles []domain.Role) error {
	return pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `DELETE FROM user_roles WHERE user_id = $1::uuid`, userID); err != nil {
			return fmt.Errorf("quitar roles: %w", err)
		}
		if err := insertRoles(ctx, tx, userID, roles); err != nil {
			return err
		}
		return audit(ctx, tx, &actorID, "user.roles_set", "user:"+userID, map[string]any{"roles": roles})
	})
}

// GrantRoles añade roles sin quitar los existentes (bootstrap de admin).
func (u *PostgresUsers) GrantRoles(ctx context.Context, actorID *string, userID string, roles []domain.Role, reason string) error {
	return pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		if err := insertRoles(ctx, tx, userID, roles); err != nil {
			return err
		}
		return audit(ctx, tx, actorID, "user.roles_granted", "user:"+userID, map[string]any{"roles": roles, "reason": reason})
	})
}

func insertRoles(ctx context.Context, tx pgx.Tx, userID string, roles []domain.Role) error {
	for _, r := range roles {
		if _, err := tx.Exec(ctx, `INSERT INTO user_roles (user_id, role) VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING`,
			userID, r); err != nil {
			return fmt.Errorf("asignar rol: %w", err)
		}
	}
	return nil
}

func (u *PostgresUsers) SetStatus(ctx context.Context, actorID, userID string, status domain.UserStatus) error {
	return pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `UPDATE users SET status = $2 WHERE id = $1::uuid`, userID, status)
		if err != nil {
			return fmt.Errorf("cambiar estado: %w", err)
		}
		if tag.RowsAffected() != 1 {
			return domain.ErrResourceNotFound
		}
		return audit(ctx, tx, &actorID, "user.status_set", "user:"+userID, map[string]any{"status": status})
	})
}

// ClaimInvitations acepta las invitaciones vigentes del email y concede sus roles (atómico).
func (u *PostgresUsers) ClaimInvitations(ctx context.Context, userID, email string) ([]domain.Role, error) {
	var granted []domain.Role
	err := pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
UPDATE invitations SET accepted_at = now(), accepted_by = $1::uuid
WHERE lower(email) = lower($2) AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
RETURNING id::text, roles`, userID, email)
		if err != nil {
			return fmt.Errorf("aceptar invitaciones: %w", err)
		}
		type claimed struct {
			id    string
			roles []string
		}
		items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (claimed, error) {
			var c claimed
			return c, row.Scan(&c.id, &c.roles)
		})
		if err != nil {
			return fmt.Errorf("leer invitaciones: %w", err)
		}
		for _, c := range items {
			roles, err := domain.ParseRoles(c.roles)
			if err != nil {
				return err
			}
			if err := insertRoles(ctx, tx, userID, roles); err != nil {
				return err
			}
			if err := audit(ctx, tx, &userID, "invitation.accepted", "invitation:"+c.id, map[string]any{"roles": roles}); err != nil {
				return err
			}
			granted = append(granted, roles...)
		}
		return nil
	})
	return granted, err
}

func (u *PostgresUsers) CreateInvitation(ctx context.Context, inv domain.Invitation) (domain.Invitation, error) {
	err := pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		// Una invitación nueva al mismo email sustituye a la anterior pendiente.
		if _, err := tx.Exec(ctx, `
UPDATE invitations SET revoked_at = now()
WHERE lower(email) = lower($1) AND accepted_at IS NULL AND revoked_at IS NULL`, inv.Email); err != nil {
			return fmt.Errorf("revocar invitación previa: %w", err)
		}
		if err := tx.QueryRow(ctx, `
INSERT INTO invitations (email, roles, invited_by, expires_at) VALUES ($1, $2, $3::uuid, $4)
RETURNING id::text, created_at`, inv.Email, roleStrings(inv.Roles), inv.InvitedBy, inv.ExpiresAt).Scan(&inv.ID, &inv.CreatedAt); err != nil {
			return fmt.Errorf("crear invitación: %w", err)
		}
		return audit(ctx, tx, &inv.InvitedBy, "invitation.created", "invitation:"+inv.ID,
			map[string]any{"email": inv.Email, "roles": inv.Roles})
	})
	return inv, err
}

func (u *PostgresUsers) Invitations(ctx context.Context, limit int) ([]domain.Invitation, error) {
	rows, err := u.pool.Query(ctx, `
SELECT id::text, email, roles, COALESCE(invited_by::text, ''), created_at, expires_at, accepted_at, revoked_at
FROM invitations ORDER BY created_at DESC LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("listar invitaciones: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.Invitation, error) {
		var inv domain.Invitation
		var roles []string
		err := row.Scan(&inv.ID, &inv.Email, &roles, &inv.InvitedBy, &inv.CreatedAt, &inv.ExpiresAt, &inv.AcceptedAt, &inv.RevokedAt)
		for _, r := range roles {
			inv.Roles = append(inv.Roles, domain.Role(r))
		}
		return inv, err
	})
	if err != nil {
		return nil, fmt.Errorf("leer invitaciones: %w", err)
	}
	return items, nil
}

func (u *PostgresUsers) RevokeInvitation(ctx context.Context, actorID, id string) error {
	return pgx.BeginFunc(ctx, u.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
UPDATE invitations SET revoked_at = now()
WHERE id = $1::uuid AND accepted_at IS NULL AND revoked_at IS NULL`, id)
		if err != nil {
			return fmt.Errorf("revocar invitación: %w", err)
		}
		if tag.RowsAffected() != 1 {
			return domain.ErrResourceNotFound
		}
		return audit(ctx, tx, &actorID, "invitation.revoked", "invitation:"+id, nil)
	})
}

func (u *PostgresUsers) Profile(ctx context.Context, userID string) (domain.Profile, bool, error) {
	var p domain.Profile
	err := u.pool.QueryRow(ctx, `
SELECT display_name, specialty, certifications, bio, updated_at FROM professional_profiles WHERE user_id = $1::uuid`,
		userID).Scan(&p.DisplayName, &p.Specialty, &p.Certifications, &p.Bio, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Profile{}, false, nil
	}
	if err != nil {
		return domain.Profile{}, false, fmt.Errorf("consultar perfil: %w", err)
	}
	return p, true, nil
}

func (u *PostgresUsers) SaveProfile(ctx context.Context, userID string, p domain.Profile) error {
	_, err := u.pool.Exec(ctx, `
INSERT INTO professional_profiles (user_id, display_name, specialty, certifications, bio, updated_at)
VALUES ($1::uuid, $2, $3, $4, $5, now())
ON CONFLICT (user_id) DO UPDATE SET display_name = EXCLUDED.display_name, specialty = EXCLUDED.specialty,
    certifications = EXCLUDED.certifications, bio = EXCLUDED.bio, updated_at = now()`,
		userID, p.DisplayName, p.Specialty, p.Certifications, p.Bio)
	if err != nil {
		return fmt.Errorf("guardar perfil: %w", err)
	}
	return nil
}

func (u *PostgresUsers) Record(ctx context.Context, actorID *string, action, target string, details any) error {
	return audit(ctx, u.pool, actorID, action, target, details)
}

func (u *PostgresUsers) Audit(ctx context.Context, limit int) ([]domain.AuditEntry, error) {
	rows, err := u.pool.Query(ctx, `
SELECT a.id, a.actor_id::text, a.action, a.target, a.details, a.created_at
FROM audit_log a ORDER BY a.created_at DESC, a.id DESC LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("listar auditoría: %w", err)
	}
	items, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (domain.AuditEntry, error) {
		var e domain.AuditEntry
		return e, row.Scan(&e.ID, &e.ActorID, &e.Action, &e.Target, &e.Details, &e.CreatedAt)
	})
	if err != nil {
		return nil, fmt.Errorf("leer auditoría: %w", err)
	}
	return items, nil
}

// dbExecer lo cumplen el pool y una transacción: la auditoría puede ir dentro o fuera de una.
type dbExecer interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
}

func audit(ctx context.Context, db dbExecer, actorID *string, action, target string, details any) error {
	payload := []byte("{}")
	if details != nil {
		b, err := json.Marshal(details)
		if err != nil {
			return fmt.Errorf("serializar auditoría: %w", err)
		}
		payload = b
	}
	if _, err := db.Exec(ctx, `INSERT INTO audit_log (actor_id, action, target, details) VALUES ($1::uuid, $2, $3, $4)`,
		actorID, action, target, payload); err != nil {
		return fmt.Errorf("registrar auditoría: %w", err)
	}
	return nil
}

func roleStrings(roles []domain.Role) []string {
	out := make([]string, len(roles))
	for i, r := range roles {
		out[i] = string(r)
	}
	return out
}

// --- Preferencias (sin cambios de comportamiento) ---

const getPreferencesSQL = `SELECT handedness FROM users WHERE id = $1::uuid`

func (u *PostgresUsers) GetPreferences(ctx context.Context, userID string) (domain.Preferences, error) {
	var p domain.Preferences
	if err := u.pool.QueryRow(ctx, getPreferencesSQL, userID).Scan(&p.Handedness); err != nil {
		return domain.Preferences{}, fmt.Errorf("consultar preferencias: %w", err)
	}
	return p, nil
}

const savePreferencesSQL = `UPDATE users SET handedness = $2 WHERE id = $1::uuid`

func (u *PostgresUsers) SavePreferences(ctx context.Context, userID string, p domain.Preferences) error {
	tag, err := u.pool.Exec(ctx, savePreferencesSQL, userID, p.Handedness)
	if err != nil {
		return fmt.Errorf("actualizar preferencias: %w", err)
	}
	if tag.RowsAffected() != 1 { // el usuario se da de alta al autenticarse: no debería ocurrir
		return fmt.Errorf("actualizar preferencias: usuario %s inexistente", userID)
	}
	return nil
}
