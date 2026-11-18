package tracing

import (
	"context"
	"crypto/tls"
	"os"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

const instrumentationName = "apple-remote-sign"

// Tracer 是请求和服务共用的追踪入口。
var Tracer trace.Tracer

// Provider 是所有组件共用的追踪提供者，先于数据库等资源初始化。
var Provider *sdktrace.TracerProvider

func init() {
	// 部署环境可以为 API 和工作进程分别命名，空值回退到 YAML 配置。
	serviceName := os.Getenv("OTEL_SERVICE_NAME")
	if serviceName == "" {
		serviceName = config.Config.App.Name
	}

	// 无论是否上报，都创建真实 SDK 并记录追踪，不能退回空实现。
	attrs := make([]attribute.KeyValue, 0, 2+len(config.Config.Trace.ResourceAttributes))
	attrs = append(attrs,
		attribute.String("service.name", serviceName),
		attribute.String("deployment.environment.name", config.Config.App.Environment),
	)
	for k, v := range config.Config.Trace.ResourceAttributes {
		attrs = append(attrs, attribute.String(k, v))
	}
	options := []sdktrace.TracerProviderOption{
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
		sdktrace.WithSpanProcessor(errorLogProcessor{}),
		sdktrace.WithResource(resource.NewSchemaless(attrs...)),
	}

	// 上报是唯一可关闭的追踪功能，关闭后仍生成有效 Trace ID。
	if config.Config.Trace.ReportEnabled {
		// 显式指定传输凭据，TLS 模式不受 OTEL 环境变量影响。
		transportCredentials := credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})
		if config.Config.Trace.Insecure {
			transportCredentials = insecure.NewCredentials()
		}
		ctx, cancel := context.WithTimeout(context.Background(), config.Config.Trace.ExportTimeout)
		exporter, err := otlptracegrpc.New(
			ctx,
			otlptracegrpc.WithEndpoint(config.Config.Trace.Endpoint),
			otlptracegrpc.WithTLSCredentials(transportCredentials),
			otlptracegrpc.WithHeaders(config.Config.Trace.Headers),
			otlptracegrpc.WithTimeout(config.Config.Trace.ExportTimeout),
		)
		cancel()
		if err != nil {
			logger.Logger.Fatalf("Trace exporter initialization failed: %v", err)
		}
		options = append(options, sdktrace.WithBatcher(
			safeExporter{exporter},
			sdktrace.WithBatchTimeout(config.Config.Trace.BatchTimeout),
			sdktrace.WithExportTimeout(config.Config.Trace.ExportTimeout),
			sdktrace.WithMaxQueueSize(config.Config.Trace.MaxQueueSize),
			sdktrace.WithMaxExportBatchSize(config.Config.Trace.MaxExportBatchSize),
		))
	}

	// 显式设置采样和传播协议，不让外部环境变量关闭请求追踪。
	Provider = sdktrace.NewTracerProvider(options...)
	otel.SetTracerProvider(Provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(err error) {
		// 后台上报错误复用全局日志。
		logger.Logger.Errorf("Trace export failed: %v", err)
	}))
	Tracer = Provider.Tracer(instrumentationName)
}

// TraceID 返回上下文中真实的 OpenTelemetry Trace ID。
func TraceID(ctx context.Context) string {
	// HTTP 追踪中间件保证正常响应和错误响应都有有效标识。
	return trace.SpanContextFromContext(ctx).TraceID().String()
}

// Shutdown 在进程退出时等待批量追踪上报完成。
func Shutdown(ctx context.Context) error {
	// 关闭上报不会改变运行期间的追踪能力。
	return Provider.Shutdown(ctx)
}
