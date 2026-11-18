package httpd

import (
	"context"
	"errors"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/labstack/echo/v4"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authRouters"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
	"github.com/OVINC-CN/AppleSign/src/api/internal/tracing"
)

func traceRequests(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		start := time.Now()
		req := c.Request()
		parent := otel.GetTextMapPropagator().
			Extract(req.Context(), propagation.HeaderCarrier(req.Header))
		ctx, span := tracing.Tracer.Start(
			parent,
			req.Method+" "+c.Path(),
			trace.WithSpanKind(trace.SpanKindServer),
		)
		defer span.End()
		c.SetRequest(req.WithContext(ctx))
		h := c.Response().Header()
		h.Set("Cache-Control", "no-store")
		h.Set("X-Request-ID", uuid.NewString())
		h.Set("X-Trace-ID", tracing.TraceID(ctx))
		e := next(c)
		if e != nil {
			c.Error(e)
		}
		status := c.Response().Status
		span.SetAttributes(
			attribute.String("http.route", c.Path()),
			attribute.String("http.request.method", req.Method),
			attribute.Int("http.response.status_code", status),
		)
		if status >= 500 {
			span.SetStatus(codes.Error, http.StatusText(status))
		}
		logger.Logger.WithContext(ctx).
			Infof("%s %s %d %s ip=%s bytes=%d", req.Method, req.URL.EscapedPath(), status, time.Since(start), c.RealIP(), c.Response().Size)
		return nil
	}
}

func identify(machine bool) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			r := c.Request()
			ctx := r.Context()
			var p authModels.Principal
			var e error
			if machine {
				scheme, t, ok := strings.Cut(r.Header.Get("Authorization"), " ")
				if !ok || !strings.EqualFold(scheme, "Bearer") || len(t) > 256 || t == "" {
					e = response.Fail(401, "Invalid or missing Bearer token.")
				} else {
					p, e = authSvc.Key(ctx, t)
				}
			} else {
				v, err := r.Cookie(config.Config.Web.CookieName)
				if err != nil || v.Value == "" || len(v.Value) > 256 {
					e = response.Fail(401, "Please sign in.")
				} else {
					p, e = authSvc.Session(ctx, v.Value)
				}
			}
			if e != nil {
				var f *response.Error
				if errors.As(e, &f) {
					if machine && f.Status == 401 {
						c.Response().Header().Set("WWW-Authenticate", "Bearer")
					}
					return f
				}
				return response.Fail(503, "Authentication service unavailable.")
			}
			if !machine && !slices.Contains([]string{"GET", "HEAD", "OPTIONS"}, r.Method) &&
				(!slices.Contains(config.Config.Origins(), r.Header.Get("Origin")) || p.CSRFToken == "" || !authUtils.Equal(p.CSRFToken, r.Header.Get("X-CSRF-Token"))) {
				return response.Fail(403, "Invalid request origin or CSRF token.")
			}
			c.Set("principal", p)
			return next(c)
		}
	}
}

func administrator(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		if !authRouters.Actor(c).Admin {
			return response.Fail(403, "Administrator access required.")
		}
		return next(c)
	}
}

func authorize(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		p := authRouters.Actor(c)
		ctx := c.Request().Context()
		vals := c.ParamValues()
		for i, k := range c.ParamNames() {
			id, e := uuid.Parse(vals[i])
			if e != nil {
				return response.Fail(422, "Invalid UUID.")
			}
			vals[i] = id.String()
			switch k {
			case "job_id":
				j, e := jobsSvc.Require(ctx, p, vals[i])
				if e != nil {
					return e
				}
				c.Set("job", &j)
			case "application_id":
				if _, e = accessSvc.Require(
					ctx,
					p,
					vals[i],
					strings.HasSuffix(c.Path(), "/jobs"),
				); e != nil {
					return e
				}
			}
		}
		return next(c)
	}
}

func health(c echo.Context) error { return c.JSON(200, echo.Map{"status": "ok"}) }
func readiness(c echo.Context) error {
	ctx, cancel := context.WithTimeout(c.Request().Context(), config.Config.Redis.Timeout)
	defer cancel()
	if !ready.Load() || db.Ping(ctx) != nil {
		return c.JSON(503, echo.Map{"status": "unavailable"})
	}
	return health(c)
}
