package rcodesign

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

func TestSubmissionIDBeforeExit(t *testing.T) {
	dir := t.TempDir()
	cfg := &config.Config.Signing
	old := *cfg
	t.Cleanup(func() { *cfg = old })
	cfg.Binary = filepath.Join(dir, "rcodesign")
	cfg.WorkDir = dir
	cfg.CommandTimeout = 3 * time.Second
	const id = "018bf105-0321-7493-9823-123456789abc"
	script := "#!/bin/sh\nprintf 'created submission ID: " + id + "\\n'\nwhile [ ! -e saved ]; do sleep 0.01; done\nprintf 'Bearer sensitive-value\\n'\nexit 1\n"
	if e := os.WriteFile(cfg.Binary, []byte(script), 0700); e != nil {
		t.Fatal(e)
	}
	seen := false
	out, e := Submit(t.Context(), "unused", func(s string) error {
		if s != id {
			t.Fatalf("wrong id %s", s)
		}
		seen = true
		return os.WriteFile(filepath.Join(dir, "saved"), nil, 0600)
	})
	if !seen || e == nil || strings.Contains(out, "sensitive-value") {
		t.Fatal(out, e)
	}
}

func TestCancelledCommandExits(t *testing.T) {
	dir := t.TempDir()
	cfg := &config.Config.Signing
	old := *cfg
	t.Cleanup(func() { *cfg = old })
	cfg.Binary = filepath.Join(dir, "tool")
	cfg.WorkDir = dir
	if e := os.WriteFile(cfg.Binary, []byte("#!/bin/sh\nexec sleep 30\n"), 0700); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(t.Context(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, e := Sign(ctx, "file")
	if !errors.Is(e, context.DeadlineExceeded) || time.Since(start) > 3*time.Second {
		t.Fatal(e)
	}
}
