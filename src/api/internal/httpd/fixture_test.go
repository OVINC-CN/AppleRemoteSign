//go:build testfixture

package httpd

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"flag"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/s3"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsTask"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
)

var browser = flag.Bool("browser-fixture", false, "启动 CI 浏览器测试夹具")

type fixture struct {
	engine   *echo.Echo
	cache    *memoryRedis
	provider *fixtureProvider
	app      accessModels.Application
}

func must(t *testing.T, e error) {
	t.Helper()
	if e != nil {
		t.Fatal(e)
	}
}

func setup(t *testing.T) fixture {
	t.Helper()
	dir := t.TempDir()
	cfg := config.Config
	original := *cfg
	t.Cleanup(func() { *cfg = original })
	cfg.App.Addr = "127.0.0.1:18765"
	cfg.OIDC.IssuerURL = browserHost
	cfg.OIDC.ClientID = "browser-tests"
	cfg.OIDC.ClientSecret = "test-only"
	cfg.OIDC.RedirectURI = browserHost + "/api/v1/auth/callback"
	cfg.OIDC.AdminUsernames = []string{"admin"}
	cfg.OIDC.AllowedUsernames = []string{"alice", "bob"}
	cfg.Web.FrontendURL = "http://127.0.0.1:4173"
	cfg.Web.CookieSecure = false
	cfg.CDN.BaseURL = browserHost
	cfg.CDN.SignKey = "browser-cdn-key"
	cfg.S3.Bucket = "browser-artifacts"
	cfg.S3.EndpointURL = "https://s3.example"
	cfg.S3.AddressingStyle = "path"
	cfg.S3.AccessKeyID = "test-access"
	cfg.S3.SecretAccessKey = "test-secret"
	cfg.Signing.DataDir = filepath.Join(dir, "data")
	cfg.Signing.WorkDir = dir
	cfg.Signing.Binary = filepath.Join(dir, "rcodesign")
	cfg.Signing.AppleKeyFile = filepath.Join(dir, "apple.json")
	cfg.Signing.PasswordFile = filepath.Join(dir, "pw")
	cfg.Signing.CertificateFile = filepath.Join(dir, "cert")
	must(t, os.WriteFile(cfg.Signing.CertificateFile, []byte("fixture"), 0600))
	must(t, os.WriteFile(cfg.Signing.PasswordFile, []byte("fixture-password\n"), 0600))
	key, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	must(t, e)
	der, e := x509.MarshalPKCS8PrivateKey(key)
	must(t, e)
	doc, e := json.Marshal(
		map[string]string{
			"issuer_id":   "test",
			"key_id":      "fixture",
			"private_key": base64.StdEncoding.EncodeToString(der),
		},
	)
	must(t, e)
	must(t, os.WriteFile(cfg.Signing.AppleKeyFile, doc, 0600))
	must(t, os.WriteFile(cfg.Signing.Binary, []byte(`#!/bin/sh
for asset do :; done
case "$3" in
sign) printf '|signed' >> "$asset";;
staple) printf '|stapled' >> "$asset";;
notary-submit) printf 'created submission ID: 9413d242-5d66-499a-8476-53e4b22f68ef\n';;
*) exit 2;;
esac
`), 0700))
	sql, e := gorm.Open(
		sqlite.Open(filepath.Join(dir, "test.db")),
		&gorm.Config{Logger: logger.Discard, NowFunc: func() time.Time { return time.Now().UTC() }},
	)
	must(t, e)
	pool, e := sql.DB()
	must(t, e)
	pool.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = pool.Close() })
	cache := &memoryRedis{leases: map[string]entry{}}
	client := redis.NewClient(&redis.Options{Addr: "fixture.invalid:0"})
	client.AddHook(cache)
	t.Cleanup(func() { _ = client.Close() })
	t.Cleanup(db.BindFixture(sql, client))
	must(
		t,
		sql.AutoMigrate(
			&authModels.User{},
			&authModels.Session{},
			&authModels.Login{},
			&accessModels.Application{},
			&accessModels.Key{},
			&accessModels.Grant{},
			&jobsModels.Job{},
		),
	)
	must(t, jobsTask.Bootstrap(t.Context()))
	app, e := accessSvc.Create(t.Context(), "com.example.desktop", "Desktop")
	must(t, e)
	_, e = accessSvc.Create(t.Context(), "com.example.private", "Private")
	must(t, e)
	must(t, accessSvc.Replace(t.Context(), app.ID, []string{"alice"}))
	provider, e := newProvider()
	must(t, e)
	engine := New()
	provider.register(engine)
	provider.handler = engine
	s3.ResetFixture()
	t.Cleanup(s3.ResetFixture)
	old := http.DefaultTransport
	http.DefaultTransport = provider
	t.Cleanup(func() { http.DefaultTransport = old })
	ready.Store(true)
	t.Cleanup(func() { ready.Store(false) })
	return fixture{engine, cache, provider, app}
}

func TestBrowserServer(t *testing.T) {
	if !*browser {
		t.Skip("CI browser entry point")
	}
	f := setup(t)
	must(t, f.engine.Start("127.0.0.1:18765"))
}
