package db

import (
	"context"
	"runtime/debug"
	"strings"

	"github.com/redis/go-redis/extra/redisotel/v9"
	"github.com/redis/go-redis/v9"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/tracing"
)

var rdb *redis.Client

func init() {
	cfg := config.Config.Redis
	if cfg.URL == "" {
		return
	}
	opts, err := redis.ParseURL(cfg.URL)
	if err != nil || opts.DB < 0 ||
		!strings.HasPrefix(cfg.URL, "redis://") && !strings.HasPrefix(cfg.URL, "rediss://") {
		logger.Logger.Fatal("Invalid Redis URL")
	}
	opts.MaxRetries = -1
	opts.DialTimeout = cfg.Timeout
	opts.ReadTimeout = cfg.Timeout
	opts.WriteTimeout = cfg.Timeout
	opts.ContextTimeoutEnabled = true
	rdb = redis.NewClient(opts)
	if err := redisotel.InstrumentTracing(
		rdb,
		redisotel.WithDBStatement(false),
		redisotel.WithTracerProvider(tracing.Provider),
	); err != nil {
		logger.Logger.Fatal("Redis tracing initialization failed")
	}
}

func PingRedis(ctx context.Context) error {
	err := rdb.Ping(ctx).Err()
	if err != nil {
		logger.Logger.WithContext(ctx).
			Errorf("redis.ping_failed: %T: %v\n%s", err, err, debug.Stack())
	}
	return err
}

func RDB() *redis.Client { return rdb }
