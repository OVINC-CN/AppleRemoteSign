package httpd

import (
	"context"
	"errors"
	"net/http"
	"sync/atomic"

	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsTask"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

var ready atomic.Bool

func New() *echo.Echo {
	e := echo.New()
	e.HideBanner = true
	e.HidePort = true
	e.HTTPErrorHandler = response.Handler
	e.Server.Addr = config.Config.App.Addr
	e.Server.ReadHeaderTimeout = config.Config.HTTP.ReadHeaderTimeout
	e.Server.IdleTimeout = config.Config.HTTP.IdleTimeout
	// 仅显式配置的代理网段有权转发客户端 IP。
	trustedProxies := []echo.TrustOption{
		echo.TrustLoopback(false),
		echo.TrustLinkLocal(false),
		echo.TrustPrivateNet(false),
	}
	for _, network := range config.Config.HTTP.TrustedProxyNets {
		trustedProxies = append(trustedProxies, echo.TrustIPRange(network))
	}
	e.IPExtractor = echo.ExtractIPFromXFFHeader(trustedProxies...)
	e.Use(
		traceRequests,
		middleware.Recover(),
		middleware.CORSWithConfig(
			middleware.CORSConfig{
				AllowOrigins:     config.Config.Origins(),
				AllowCredentials: true,
				AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "OPTIONS"},
				AllowHeaders: []string{
					"Content-Type",
					"X-CSRF-Token",
					"traceparent",
					"tracestate",
				},
				ExposeHeaders: []string{"Content-Disposition", "X-Request-ID", "X-Trace-ID"},
			},
		),
	)
	routes(e)
	return e
}

func Serve(ctx context.Context) error {
	e := New()
	worker, stop := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); jobsTask.Run(worker) }()
	defer func() { stop(); <-done }()
	result := make(chan error, 1)
	ready.Store(true)
	defer ready.Store(false)
	go func() { result <- e.StartServer(e.Server) }()
	select {
	case err := <-result:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
		ready.Store(false)
		drain, cancel := context.WithTimeout(
			context.Background(),
			config.Config.App.ShutdownTimeout,
		)
		defer cancel()
		if err := e.Shutdown(drain); err != nil {
			return errors.Join(err, e.Close())
		}
		return nil
	}
}
