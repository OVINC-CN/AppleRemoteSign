package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestYAMLContract(t *testing.T) {
	good, err := os.ReadFile(FilePath)
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct{ old, new string }{
		{"10.0.0.0/12", "10.0.0.0/33"},
		{"cookie_secure: true", "cookie_secure: false\n  cookie_same_site: none"},
		{"login_ttl: \"10m\"", "login_ttl: 11m"},
		{"max_upload_bytes: 2147483648", "max_upload_bytes: -1"},
		{"allowed_origins: []", "allowed_origins: [\"https://site.example/a\"]"},
		{`bucket_url: "https://signing-artifacts-1250000000.cos.ap-guangzhou.myqcloud.com"`, `bucket_url: "https://cos.example/bucket"`},
		{`secret_id: ""`, `secret_id: "without-secret-key"`},
		{`session_token: ""`, `session_token: "without-secret-id"`},
		{"report_enabled: false", "unknown: bogus"},
		{"timeout: \"3s\"", "timeout: 0s"},
	} {
		v := strings.Replace(string(good), test.old, test.new, 1)
		if v == string(good) {
			t.Fatalf("test field missing: %s", test.old)
		}
		directory := t.TempDir()
		root, e := os.OpenRoot(directory)
		if e != nil {
			t.Fatal(e)
		}
		e = root.WriteFile("config.yaml", []byte(v), 0600)
		if closeErr := root.Close(); closeErr != nil {
			t.Fatal(closeErr)
		}
		path := filepath.Join(directory, "config.yaml")
		if e != nil {
			t.Fatal(e)
		}
		if _, e := load(path); e == nil {
			t.Fatalf("accepted invalid field: %s", test.old)
		}
	}
	if Config.Signing.MaxUploadBytes != 2<<30 || Config.App.ShutdownTimeout != 3700*time.Second {
		t.Fatal("changed signing defaults")
	}
}
