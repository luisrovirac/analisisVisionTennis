package domain

import (
	"errors"
	"slices"
	"testing"
)

func TestParseRolesDedupesSortsAndRejectsUnknown(t *testing.T) {
	roles, err := ParseRoles([]string{"validator", "expert", "validator"})
	if err != nil || !slices.Equal(roles, []Role{RoleExpert, RoleValidator}) {
		t.Fatalf("roles=%v err=%v", roles, err)
	}
	for _, bad := range [][]string{{"user"}, {"future"}, {"root"}} {
		if _, err := ParseRoles(bad); !errors.Is(err, ErrInvalidInput) {
			t.Errorf("%v debería rechazarse", bad)
		}
	}
}

func TestUserHasAnyRole(t *testing.T) {
	u := User{Roles: []Role{RoleExpert, RoleValidator}}
	if !u.Has(RoleAdmin, RoleValidator) || u.Has(RoleAdmin) || (User{}).Has(RoleExpert) {
		t.Fatal("Has incorrecto")
	}
}

func TestNormalizeEmail(t *testing.T) {
	if e, err := NormalizeEmail("Ana.Coach@Example.TEST"); err != nil || e != "ana.coach@example.test" {
		t.Fatalf("e=%s err=%v", e, err)
	}
	for _, bad := range []string{"", "no-email", "Ana <ana@x.test>"} {
		if _, err := NormalizeEmail(bad); err == nil {
			t.Errorf("%q debería rechazarse", bad)
		}
	}
}

func TestProfileValidate(t *testing.T) {
	ok := Profile{DisplayName: "Ana", Specialty: "biomechanics"}
	if err := ok.Validate(); err != nil {
		t.Fatal(err)
	}
	if err := (Profile{DisplayName: "Ana", Specialty: "astrology"}).Validate(); err == nil {
		t.Fatal("especialidad desconocida")
	}
}
