//go:build testfixture

package httpd

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
)

func TestAdminGrantsAndKeyLifecycle(t *testing.T) {
	f := setup(t)
	ctx := t.Context()
	s := db.DB().WithContext(ctx)
	admin := authModels.User{ID: uuid.NewString(), Username: "admin"}
	must(t, s.Create(&admin).Error)
	cookie := &http.Cookie{Name: config.Config.Web.CookieName, Value: "old-session"}
	must(
		t,
		s.Create(
			&authModels.Session{
				TokenHash: authUtils.Digest(cookie.Value),
				UserID:    admin.ID,
				CSRFToken: "csrf",
				ExpiresAt: time.Now().Add(time.Hour),
			},
		).Error,
	)
	web := func(method, path, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequestWithContext(ctx, method, path, strings.NewReader(body))
		req.AddCookie(cookie)
		req.Header.Set("Origin", config.Config.Web.FrontendURL)
		req.Header.Set("X-CSRF-Token", "csrf")
		req.Header.Set("Content-Type", "application/json")
		r := httptest.NewRecorder()
		f.engine.ServeHTTP(r, req)
		return r
	}
	r := web("POST", "/api/v1/admin/applications", `{"identifier":"client.app","name":"Viewer"}`)
	status(t, r, 201)
	id := fields(t, r)["data"].(map[string]any)["id"].(string)
	prefix := "/api/v1/admin/applications/" + id
	status(t, web("PUT", prefix+"/users", `{"usernames":["alice","alice"]}`), 200)
	r = web("GET", prefix+"/users", "")
	status(t, r, 200)
	if names := fields(t, r)["data"].([]any); len(names) != 1 || names[0] != "alice" {
		t.Fatal(names)
	}
	status(t, web("PUT", prefix+"/users", `{"usernames":["unknown"]}`), 422)
	r = web("POST", prefix+"/keys", `{"name":"Build"}`)
	status(t, r, 201)
	created := fields(t, r)["data"].(map[string]any)
	token := created["token"].(string)
	key := created["key"].(map[string]any)["id"].(string)
	r = web("GET", prefix+"/keys", "")
	status(t, r, 200)
	if strings.Contains(r.Body.String(), token) || strings.Contains(r.Body.String(), "token_hash") {
		t.Fatal("key list exposes credential")
	}
	schema := func() *httptest.ResponseRecorder {
		return request(t, f.engine, "GET", "/openapi/v1/openapi.json", token, nil, nil, "")
	}
	status(t, schema(), 200)
	status(t, web("PATCH", prefix, `{"enabled":false}`), 200)
	status(t, schema(), 401)
	status(t, web("PATCH", prefix, `{"enabled":true}`), 200)
	status(t, schema(), 200)
	status(t, web("POST", prefix+"/keys/"+key+"/revoke", ""), 200)
	status(t, schema(), 401)
	status(t, web("POST", "/api/v1/auth/logout", ""), 200)
	status(t, web("GET", "/api/v1/me", ""), 401)
}
