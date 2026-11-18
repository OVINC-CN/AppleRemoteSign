package db

import (
	"context"
	"time"

	"github.com/go-sql-driver/mysql"
	"go.opentelemetry.io/otel/codes"
	semconv "go.opentelemetry.io/otel/semconv/v1.30.0"
	"go.opentelemetry.io/otel/trace"
	gormmysql "gorm.io/driver/mysql"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
	gormtracing "gorm.io/plugin/opentelemetry/tracing"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/tracing"
)

var db *gorm.DB

func init() {
	// 未配置 DSN 时不启用 MySQL。
	if config.Config.MySQL.DSN == "" {
		return
	}

	// 解析连接参数，所有超时均使用 YAML 中的配置。
	dsn, err := mysql.ParseDSN(config.Config.MySQL.DSN)
	if err != nil {
		logger.Logger.Fatalf("MySQL DSN parsing failed: %v", err)
	}
	dsn.ParseTime = true
	// 数据库自动时间与 Go 解析统一使用 UTC，不依赖服务器默认时区。
	dsn.Loc = time.UTC
	if dsn.Params == nil {
		dsn.Params = make(map[string]string)
	}
	dsn.Params["time_zone"] = "'+00:00'"
	dsn.Timeout = config.Config.MySQL.ConnectTimeout
	dsn.ReadTimeout = config.Config.MySQL.ReadTimeout
	dsn.WriteTimeout = config.Config.MySQL.WriteTimeout

	// 初始化 GORM，连接检查统一在接入追踪后执行。
	db, err = gorm.Open(
		gormmysql.New(gormmysql.Config{
			DSNConfig:                 dsn,
			SkipInitializeWithVersion: true,
		}),
		&gorm.Config{
			DisableAutomaticPing:      true,
			DefaultContextTimeout:     config.Config.MySQL.OperationTimeout,
			DefaultTransactionTimeout: config.Config.MySQL.OperationTimeout,
			TranslateError:            true,
			NowFunc:                   func() time.Time { return time.Now().UTC() },
			Logger:                    gormlogger.Discard,
		},
	)
	if err != nil {
		logger.Logger.Fatalf("MySQL GORM initialization failed: %v", err)
	}

	// 连接池参数统一读取 YAML。
	sqlDB, err := db.DB()
	if err != nil {
		logger.Logger.Fatalf("MySQL connection pool lookup failed: %v", err)
	}
	sqlDB.SetMaxOpenConns(config.Config.MySQL.MaxOpenConns)
	sqlDB.SetMaxIdleConns(config.Config.MySQL.MaxIdleConns)
	sqlDB.SetConnMaxLifetime(config.Config.MySQL.ConnMaxLifetime)

	// 接入 GORM 官方追踪，查询和事务内操作沿用当前上下文，省略查询参数。
	if err := db.Use(gormtracing.NewPlugin(
		gormtracing.WithTracerProvider(tracing.Provider),
		gormtracing.WithAttributes(semconv.DBNamespace(dsn.DBName)),
		gormtracing.WithoutQueryVariables(),
		gormtracing.WithoutMetrics(),
	)); err != nil {
		if closeErr := sqlDB.Close(); closeErr != nil {
			logger.Logger.Errorf("MySQL shutdown failed: %v", closeErr)
		}
		logger.Logger.Fatalf("MySQL trace instrumentation failed: %v", err)
	}

}

func pingMySQL(ctx context.Context) error {
	// GORM 查询回调不覆盖底层 Ping，单独补充客户端追踪。
	ctx, span := tracing.Tracer.Start(
		ctx,
		"mysql.ping",
		trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(semconv.DBSystemNameMySQL, semconv.DBOperationName("ping")),
	)
	defer span.End()

	// 初始化和就绪检查共用真实 Ping，错误保留在内部追踪中。
	sqlDB, err := db.DB()
	if err == nil {
		err = sqlDB.PingContext(ctx)
	}
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return err
	}
	return nil
}

// DB 返回 init 阶段创建的 GORM 客户端，业务操作需通过 WithContext 传入当前上下文。
func DB() *gorm.DB {
	// 直接使用已经初始化的共享资源。
	return db
}
