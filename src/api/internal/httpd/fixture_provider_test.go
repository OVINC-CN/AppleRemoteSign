//go:build testfixture

package httpd

import (
	"crypto/md5"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/cdn"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

const browserHost = "http://127.0.0.1:18765"

type loginCode struct {
	params url.Values
	user   string
}
type fixtureProvider struct {
	mu            sync.Mutex
	key           *rsa.PrivateKey
	flows         map[string]url.Values
	codes         map[string]loginCode
	objects       map[string][]byte
	uploadFailure bool
	handler       http.Handler
	status        string
}

func (f *fixtureProvider) register(e *echo.Echo) {
	e.GET("/.well-known/openid-configuration", func(c echo.Context) error {
		return c.JSON(
			200,
			echo.Map{
				"issuer":                                browserHost,
				"authorization_endpoint":                browserHost + "/test/authorize",
				"token_endpoint":                        browserHost + "/test/token",
				"jwks_uri":                              browserHost + "/test/jwks",
				"id_token_signing_alg_values_supported": []string{"RS256"},
			},
		)
	})
	e.GET("/test/jwks", func(c echo.Context) error {
		encode := base64.RawURLEncoding.EncodeToString
		return c.JSON(
			200,
			echo.Map{
				"keys": []any{
					echo.Map{
						"kty": "RSA",
						"kid": "fixture",
						"alg": "RS256",
						"use": "sig",
						"n":   encode(f.key.N.Bytes()),
						"e":   encode(big.NewInt(int64(f.key.E)).Bytes()),
					},
				},
			},
		)
	})
	e.GET("/test/authorize", func(c echo.Context) error {
		f.mu.Lock()
		defer f.mu.Unlock()
		id := authUtils.Token()
		f.flows[id] = c.QueryParams()
		return c.HTML(
			200,
			fmt.Sprintf(
				`<form action="/test/approve"><input type="hidden" name="flow" value="%s"><button name="username" value="admin">admin</button><button name="username" value="alice">alice</button><button name="username" value="bob">bob</button></form>`,
				id,
			),
		)
	})
	e.GET("/test/approve", func(c echo.Context) error {
		f.mu.Lock()
		defer f.mu.Unlock()
		id := c.QueryParam("flow")
		params := f.flows[id]
		delete(f.flows, id)
		user := c.QueryParam("username")
		if params == nil || !slices.Contains([]string{"alice", "admin", "bob"}, user) {
			return c.NoContent(400)
		}
		code := authUtils.Token()
		f.codes[code] = loginCode{params, user}
		return c.Redirect(
			302,
			config.Config.OIDC.RedirectURI+"?"+url.Values{
				"code":  {code},
				"state": {params.Get("state")},
			}.Encode(),
		)
	})
	e.POST("/test/token", func(c echo.Context) error {
		f.mu.Lock()
		defer f.mu.Unlock()
		code := c.FormValue("code")
		flow, ok := f.codes[code]
		delete(f.codes, code)
		hash := sha256.Sum256([]byte(c.FormValue("code_verifier")))
		if !ok ||
			base64.RawURLEncoding.EncodeToString(hash[:]) != flow.params.Get("code_challenge") {
			return c.NoContent(400)
		}
		now := time.Now().Unix()
		token := jwt.NewWithClaims(
			jwt.SigningMethodRS256,
			jwt.MapClaims{
				"iss":                browserHost,
				"sub":                "subject-" + flow.user,
				"aud":                "browser-tests",
				"iat":                now,
				"exp":                now + 60,
				"nonce":              flow.params.Get("nonce"),
				"preferred_username": flow.user,
			},
		)
		token.Header["kid"] = "fixture"
		s, err := token.SignedString(f.key)
		if err != nil {
			return err
		}
		return c.JSON(
			200,
			echo.Map{"id_token": s, "access_token": "test-only", "token_type": "Bearer"},
		)
	})
	e.GET("/app-dist/*", func(c echo.Context) error {
		f.mu.Lock()
		defer f.mu.Unlock()
		p := c.Request().URL
		parts := strings.Split(c.QueryParam("eo_sign"), "-")
		if len(parts) != 4 {
			return c.NoContent(403)
		}
		when, _ := strconv.ParseInt(parts[0], 10, 64)
		sum := md5.Sum(
			[]byte(
				p.EscapedPath() + "-" + strings.Join(
					parts[:3],
					"-",
				) + "-" + config.Config.CDN.SignKey,
			),
		)
		if age := time.Now().
			Unix() -
			when; age < 0 || age > 300 ||
			parts[3] != fmt.Sprintf("%x", sum) {
			return c.NoContent(403)
		}
		b, ok := f.objects[strings.TrimPrefix(p.Path, "/")]
		if !ok {
			return c.NoContent(404)
		}
		c.Response().
			Header().
			Set("Content-Disposition", "attachment; filename*=UTF-8''"+cdn.Escape(path.Base(p.Path)))
		return c.Blob(200, "application/zip", b)
	})
}

func (f *fixtureProvider) RoundTrip(r *http.Request) (*http.Response, error) {
	rec := httptest.NewRecorder()
	switch r.URL.Hostname() {
	case "127.0.0.1":
		f.handler.ServeHTTP(rec, r)
	case "appstoreconnect.apple.com":
		id := path.Base(r.URL.Path)
		attr := echo.Map{"status": f.status}
		if id == "logs" {
			id = path.Base(path.Dir(r.URL.Path))
			attr = echo.Map{"developerLogUrl": "https://log.example/" + id}
		}
		_ = json.NewEncoder(rec).Encode(echo.Map{"data": echo.Map{"id": id, "attributes": attr}})
	case "log.example":
		if r.Header.Get("Authorization") != "" {
			return nil, fmt.Errorf("unexpected bearer on log request")
		}
		_ = json.NewEncoder(rec).
			Encode(echo.Map{"jobId": path.Base(r.URL.Path), "status": "Accepted", "archiveFilename": "Demo.zip", "uploadDate": "2024-01-01", "logFormatVersion": 1, "statusCode": 0, "statusSummary": "Accepted", "sha256": "abc", "ticketContents": []any{}})
	case "s3.example":
		f.mu.Lock()
		defer f.mu.Unlock()
		key := strings.TrimPrefix(r.URL.Path, "/browser-artifacts/")
		switch r.Method {
		case "PUT":
			if f.uploadFailure {
				rec.WriteHeader(403)
				break
			}
			b, e := io.ReadAll(r.Body)
			if e != nil {
				return nil, e
			}
			f.objects[key] = b
		case "HEAD":
			if _, ok := f.objects[key]; !ok {
				rec.WriteHeader(404)
			}
		default:
			return nil, fmt.Errorf("unexpected S3 method")
		}
	default:
		return nil, fmt.Errorf("unexpected fixture host %s", r.URL.Hostname())
	}
	resp := rec.Result()
	resp.Request = r
	return resp, nil
}

func newProvider() (*fixtureProvider, error) {
	key, e := rsa.GenerateKey(rand.Reader, 2048)
	return &fixtureProvider{
		key:     key,
		flows:   map[string]url.Values{},
		codes:   map[string]loginCode{},
		objects: map[string][]byte{},
		status:  "Accepted",
	}, e
}
