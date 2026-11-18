package authRouters

import (
	"net/http"

	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func Actor(c echo.Context) authModels.Principal { return c.Get("principal").(authModels.Principal) }
func cookieName(state string) string {
	name := config.Config.Web.CookieName
	if state != "" {
		return name + "_oidc_" + authUtils.Digest(state)
	}
	return name
}

func cookie(c echo.Context, state string) string {
	v, e := c.Cookie(cookieName(state))
	if e != nil {
		return ""
	}
	return v.Value
}

func setCookie(c echo.Context, state, value string) {
	cfg := config.Config
	ttl := cfg.Web.SessionTTL
	mode := http.SameSiteLaxMode
	if state != "" {
		ttl = cfg.OIDC.LoginTTL
	} else if cfg.Web.CookieSameSite == "none" {
		mode = http.SameSiteNoneMode
	}
	age := int(ttl.Seconds())
	if value == "" {
		age = -1
	}
	c.SetCookie(
		&http.Cookie{ // #nosec G124 -- HttpOnly 始终开启，Secure 和 SameSite 由已验证的部署配置决定。
			Name:     cookieName(state),
			Value:    value,
			Path:     "/",
			Domain:   cfg.Web.CookieDomain,
			MaxAge:   age,
			Secure:   cfg.Web.CookieSecure,
			HttpOnly: true,
			SameSite: mode,
		},
	)
}

func Login(c echo.Context) error {
	url, state, browser, err := authSvc.Start(c.Request().Context())
	if err != nil {
		return response.Fail(502, "Identity provider is unavailable. Please try again.")
	}
	setCookie(c, state, browser)
	return c.Redirect(302, url)
}

func Callback(c echo.Context) error {
	state := c.QueryParam("state")
	token, reason := authSvc.Finish(
		c.Request().Context(),
		state,
		cookie(c, state),
		c.QueryParam("code"),
		cookie(c, ""),
	)
	if state != "" && len(state) <= 256 {
		setCookie(c, state, "")
	}
	base := config.Config.Web.FrontendURL
	if reason != "" {
		return c.Redirect(303, base+"/login?error="+reason)
	}
	setCookie(c, "", token)
	return c.Redirect(303, base+"/jobs")
}

func Logout(c echo.Context) error {
	if err := authSvc.Logout(c.Request().Context(), Actor(c).SessionHash); err != nil {
		return err
	}
	setCookie(c, "", "")
	return response.Send(c, 200, nil)
}

func Me(c echo.Context) error {
	p := Actor(c)
	return response.Send(
		c,
		200,
		echo.Map{
			"username":         p.Username,
			"is_admin":         p.Admin,
			"csrf_token":       p.CSRFToken,
			"max_upload_bytes": config.Config.Signing.MaxUploadBytes,
		},
	)
}
