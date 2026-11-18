package httpd

import (
	_ "embed"

	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessRouters"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authRouters"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsRouters"
)

//go:embed docs/openapi.json
var schema []byte

func openapiSchema(c echo.Context) error { return c.Blob(200, "application/json", schema) }

func routes(e *echo.Echo) {
	e.GET("/health", health)
	e.GET("/ready", readiness)
	machine := e.Group("/openapi/v1", identify(true))
	machine.GET("/openapi.json", openapiSchema)
	machine.POST("/jobs", jobsRouters.Submit)
	machine.GET("/jobs/:job_id", jobsRouters.Query, authorize)
	machine.GET("/jobs/:job_id/artifact", jobsRouters.Artifact, authorize)
	auth := e.Group("/api/v1/auth")
	auth.GET("/login", authRouters.Login)
	auth.GET("/callback", authRouters.Callback)
	web := e.Group("/api/v1", identify(false))
	web.POST("/auth/logout", authRouters.Logout)
	web.GET("/me", authRouters.Me)
	web.GET("/applications", accessRouters.Applications)
	web.POST("/applications/:application_id/jobs", jobsRouters.Submit, authorize)
	web.GET("/jobs", jobsRouters.List)
	jobs := web.Group("/jobs/:job_id", authorize)
	jobs.GET("", jobsRouters.Detail)
	jobs.PATCH("", jobsRouters.Rename)
	jobs.GET("/notary-details", jobsRouters.Notary)
	jobs.POST("/refresh", jobsRouters.Query)
	jobs.GET("/artifact-url", jobsRouters.Artifact)
	admin := web.Group("/admin", administrator)
	admin.GET("/users", accessRouters.Users)
	admin.GET("/applications", accessRouters.Applications)
	admin.POST("/applications", accessRouters.Create)
	app := admin.Group("/applications/:application_id", authorize)
	app.PATCH("", accessRouters.Update)
	app.GET("/users", accessRouters.Grants)
	app.PUT("/users", accessRouters.SetGrants)
	app.GET("/keys", accessRouters.Keys)
	app.POST("/keys", accessRouters.CreateKey)
	app.POST("/keys/:key_id/revoke", accessRouters.Revoke)
}
