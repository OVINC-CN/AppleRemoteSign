package tracing

import (
	"context"
	"slices"

	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// 第三方数据库和缓存 SDK 会把底层错误写入跨度。上报时仅保存异常类型。
type safeExporter struct{ sdktrace.SpanExporter }
type safeSpan struct{ sdktrace.ReadOnlySpan }

func (s safeSpan) Status() sdktrace.Status {
	v := s.ReadOnlySpan.Status()
	v.Description = ""
	return v
}
func (s safeSpan) Events() []sdktrace.Event {
	events := slices.Clone(s.ReadOnlySpan.Events())
	for i := range events {
		if events[i].Name != "exception" {
			continue
		}
		attrs := make([]attribute.KeyValue, 0, 1)
		for _, a := range events[i].Attributes {
			if a.Key == "exception.type" {
				attrs = append(attrs, a)
			}
		}
		events[i].Attributes = attrs
	}
	return events
}
func (e safeExporter) ExportSpans(ctx context.Context, spans []sdktrace.ReadOnlySpan) error {
	out := make([]sdktrace.ReadOnlySpan, len(spans))
	for i, s := range spans {
		out[i] = safeSpan{s}
	}
	return e.SpanExporter.ExportSpans(ctx, out)
}
