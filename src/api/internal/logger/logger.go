package logger

import (
	"os"
	"time"

	"github.com/sirupsen/logrus"
	"go.opentelemetry.io/otel/trace"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

// Logger 是所有模块直接使用的全局日志实例。
var Logger = logrus.StandardLogger()

const (
	traceField = "trace_id"
	spanField  = "span_id"
)

type traceHook struct{}

func init() {
	// 直接读取全局配置，不向各个服务传递日志实例。
	level, err := logrus.ParseLevel(config.Config.App.LogLevel)
	if err != nil {
		Logger.Fatalf("Logger initialization failed: %v", err)
	}
	Logger.SetLevel(level)
	Logger.SetOutput(os.Stdout)
	Logger.SetFormatter(&logrus.JSONFormatter{TimestampFormat: time.RFC3339})

	// 所有带上下文的日志自动关联当前 OpenTelemetry Trace ID 和 Span ID。
	Logger.AddHook(credentialHook{})
	Logger.AddHook(traceHook{})
	Logger.Info("Logger initialized")
}

func (traceHook) Levels() []logrus.Level {
	// 追踪标识覆盖全部日志级别。
	return logrus.AllLevels
}

func (traceHook) Fire(entry *logrus.Entry) error {
	// 只使用上下文中的真实追踪标识，不另行生成随机请求编号。
	if entry.Context != nil {
		spanContext := trace.SpanContextFromContext(entry.Context)
		if spanContext.IsValid() {
			entry.Data[traceField] = spanContext.TraceID().String()
			entry.Data[spanField] = spanContext.SpanID().String()
		}
	}
	return nil
}
