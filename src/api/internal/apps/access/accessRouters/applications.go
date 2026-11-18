package accessRouters

import (
	"regexp"
	"slices"
	"unicode/utf8"

	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authRouters"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

var identifier = regexp.MustCompile("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

func nameOK(name string) bool { return name != "" && utf8.RuneCountInString(name) <= 255 }
func Applications(c echo.Context) error {
	v, e := accessSvc.List(c.Request().Context(), authRouters.Actor(c))
	return response.Result(c, v, e)
}

func Users(c echo.Context) error {
	names := append(
		slices.Clone(config.Config.OIDC.AdminUsernames),
		config.Config.OIDC.AllowedUsernames...)
	slices.Sort(names)
	return response.Send(c, 200, slices.Compact(names))
}

func Create(c echo.Context) error {
	var body struct{ Identifier, Name string }
	if e := response.Body(c, &body); e != nil {
		return e
	}
	if !nameOK(body.Name) || !identifier.MatchString(body.Identifier) {
		return response.Fail(422, "Invalid application name or identifier.")
	}
	v, e := accessSvc.Create(c.Request().Context(), body.Identifier, body.Name)
	if e != nil {
		return e
	}
	return response.Send(c, 201, v)
}

func Update(c echo.Context) error {
	var b struct {
		Name    *string
		Enabled *bool
	}
	if e := response.Body(c, &b); e != nil {
		return e
	}
	if b.Name != nil && !nameOK(*b.Name) {
		return response.Fail(422, "Invalid application name.")
	}
	v, e := accessSvc.Update(c.Request().Context(), c.Param("application_id"), b.Name, b.Enabled)
	return response.Result(c, v, e)
}

func Grants(c echo.Context) error {
	v, e := accessSvc.Grants(c.Request().Context(), c.Param("application_id"))
	return response.Result(c, v, e)
}

func SetGrants(c echo.Context) error {
	var b struct{ Usernames []string }
	if e := response.Body(c, &b); e != nil {
		return e
	}
	if b.Usernames == nil || len(b.Usernames) > 1000 {
		return response.Fail(422, "Invalid user list.")
	}
	for _, u := range b.Usernames {
		if !config.Config.Allowed(u) {
			return response.Fail(422, "Users must be on the login allowlist or administrator list.")
		}
	}
	return response.Result(
		c,
		nil,
		accessSvc.Replace(c.Request().Context(), c.Param("application_id"), b.Usernames),
	)
}

func Keys(c echo.Context) error {
	v, e := accessSvc.Keys(c.Request().Context(), c.Param("application_id"))
	return response.Result(c, v, e)
}

func CreateKey(c echo.Context) error {
	var b struct{ Name string }
	if e := response.Body(c, &b); e != nil {
		return e
	}
	if !nameOK(b.Name) {
		return response.Fail(422, "Invalid key name.")
	}
	k, t, e := accessSvc.CreateKey(c.Request().Context(), c.Param("application_id"), b.Name)
	if e != nil {
		return e
	}
	return response.Send(c, 201, echo.Map{"key": k, "token": t})
}

func Revoke(c echo.Context) error {
	return response.Result(
		c,
		nil,
		accessSvc.Revoke(c.Request().Context(), c.Param("application_id"), c.Param("key_id")),
	)
}
