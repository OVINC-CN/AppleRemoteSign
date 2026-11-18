//go:build testfixture

package httpd

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsTask"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

func TestRedisDiagnosticEvents(t *testing.T) {
	f := setup(t)
	var logs bytes.Buffer
	previous := logger.Logger.Out
	logger.Logger.SetOutput(&logs)
	defer logger.Logger.SetOutput(previous)
	for _, reason := range []string{"WRONGPASS", "NOPERM", "connection refused", "i/o timeout"} {
		f.cache.failure = errors.New(
			reason + " redis://:never-log-this@redis.invalid/20 unavailable",
		)
		if e := jobsTask.Bootstrap(t.Context()); e == nil {
			t.Fatal("startup accepted unavailable Redis")
		}
		s := logs.String()
		if !strings.Contains(s, "redis.ping_failed") || !strings.Contains(s, reason) ||
			strings.Contains(s, "never-log-this") {
			t.Fatal("missing or unsafe Redis diagnostic")
		}
		logs.Reset()
	}
	f.cache.failure = nil
	l, e := db.Acquire(t.Context(), "release-test", true)
	must(t, e)
	f.cache.failure = errors.New("redis://:never-log-this@redis.invalid/20 unavailable")
	if !errors.Is(l.Close(), db.ErrLease) {
		t.Fatal("release error lost")
	}
	if !strings.Contains(logs.String(), "redis.release_failed") ||
		strings.Contains(logs.String(), "redis.ping_failed") ||
		strings.Contains(logs.String(), "never-log-this") {
		t.Fatal("incorrect release diagnostic")
	}
}
