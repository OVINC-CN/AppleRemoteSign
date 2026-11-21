//go:build testfixture

package httpd

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsTask"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/storage"
)

func request(
	t *testing.T,
	e *echo.Echo,
	method, path, auth string,
	cookie *http.Cookie,
	body io.Reader,
	mime string,
) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequestWithContext(t.Context(), method, path, body)
	if auth != "" {
		r.Header.Set("Authorization", "Bearer "+auth)
	}
	if cookie != nil {
		r.AddCookie(cookie)
	}
	r.Header.Set("Content-Type", mime)
	rec := httptest.NewRecorder()
	e.ServeHTTP(rec, r)
	return rec
}

func status(t *testing.T, r *httptest.ResponseRecorder, code int) {
	t.Helper()
	if r.Code != code {
		t.Fatalf("HTTP %d, want %d: %s", r.Code, code, r.Body.String())
	}
}

func fields(t *testing.T, r *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var v map[string]any
	must(t, json.Unmarshal(r.Body.Bytes(), &v))
	return v
}

func TestRealIPUsesTrustedProxies(t *testing.T) {
	engine := New()
	for _, tc := range []struct{ remote, forwarded, want string }{
		{"10.0.0.1:80", "", "10.0.0.1"},
		{"10.15.255.255:80", "203.0.113.1, 198.51.100.1, 10.0.0.2", "198.51.100.1"},
		{"10.16.0.1:80", "203.0.113.1", "10.16.0.1"},
		{"127.0.0.1:80", "203.0.113.1", "127.0.0.1"},
		{"169.254.1.1:80", "203.0.113.1", "169.254.1.1"},
	} {
		req := httptest.NewRequestWithContext(t.Context(), http.MethodGet, "/", nil)
		req.RemoteAddr = tc.remote
		if tc.forwarded != "" {
			req.Header.Set(echo.HeaderXForwardedFor, tc.forwarded)
		}
		if got := engine.NewContext(req, httptest.NewRecorder()).RealIP(); got != tc.want {
			t.Errorf("remote %s, forwarded %q: got %s, want %s", tc.remote, tc.forwarded, got, tc.want)
		}
	}
}

func TestProbeRequestLogs(t *testing.T) {
	engine := New()
	var logs bytes.Buffer
	previous := logger.Logger.Out
	logger.Logger.SetOutput(&logs)
	defer logger.Logger.SetOutput(previous)

	for _, tc := range []struct {
		method, path  string
		code          int
		wantAccessLog bool
	}{
		{"GET", "/health?verbose=1", 200, false},
		{"GET", "/ready", 503, false},
		{"POST", "/health", 405, true},
		{"GET", "/ready/missing", 404, true},
	} {
		logs.Reset()
		r := request(t, engine, tc.method, tc.path, "", nil, nil, "")
		status(t, r, tc.code)
		if r.Header().Get("X-Trace-ID") == "" {
			t.Fatalf("%s %s: missing trace ID", tc.method, tc.path)
		}
		path, _, _ := strings.Cut(tc.path, "?")
		if got := strings.Contains(logs.String(), `"msg":"`+tc.method+" "+path+" "); got != tc.wantAccessLog {
			t.Fatalf("%s %s: want access log %t, got %s", tc.method, tc.path, tc.wantAccessLog, &logs)
		}
		if tc.code == http.StatusServiceUnavailable && !strings.Contains(logs.String(), "Span failed") {
			t.Fatalf("%s %s: missing error diagnostic: %s", tc.method, tc.path, &logs)
		}
	}
}

func TestUploadErrors(t *testing.T) {
	f := setup(t)
	raw := "test-upload-errors"
	must(
		t,
		db.DB().
			WithContext(t.Context()).
			Create(&accessModels.Key{ID: uuid.NewString(), ApplicationID: f.app.ID, Name: "test", Prefix: "test", TokenHash: authUtils.Digest(raw)}).
			Error,
	)
	send := func(name, payload string) *httptest.ResponseRecorder {
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		part, err := form.CreateFormFile("file", name)
		must(t, err)
		_, err = io.WriteString(part, payload)
		must(t, err)
		must(t, form.Close())
		return request(
			t,
			f.engine,
			"POST",
			"/openapi/v1/jobs",
			raw,
			nil,
			&body,
			form.FormDataContentType(),
		)
	}
	for _, tc := range []struct{ name, message string }{
		{"App-v1.2.3.dmg", "Only ZIP uploads"},
		{"App.zip", "version number"},
		{"App-v1.2.3.zip", "Uploaded file is empty"},
	} {
		r := send(tc.name, "")
		status(t, r, 422)
		if !strings.Contains(r.Body.String(), tc.message) {
			t.Fatal(r.Body.String())
		}
	}
	t.Setenv("TMPDIR", filepath.Join(t.TempDir(), "missing"))
	r := send("App-v1.2.3.zip", "data")
	status(t, r, 500)
	if strings.Contains(r.Body.String(), os.TempDir()) {
		t.Fatal("temporary path exposed")
	}
}

func TestSigningContract(t *testing.T) {
	f := setup(t)
	s := db.DB().WithContext(t.Context())
	raw := "ars_legacy"
	key := accessModels.Key{
		ID:            uuid.NewString(),
		ApplicationID: f.app.ID,
		Name:          "CI",
		Prefix:        "ars_legacy",
		TokenHash:     authUtils.Digest(raw),
	}
	must(t, s.Create(&key).Error)
	call := func(m, p string) *httptest.ResponseRecorder { return request(t, f.engine, m, p, raw, nil, nil, "") }
	status(t, request(t, f.engine, "GET", "/openapi/v1/openapi.json", "", nil, nil, ""), 401)
	status(t, call("GET", "/openapi/v1/openapi.json"), 200)
	var buf bytes.Buffer
	z := zip.NewWriter(&buf)
	w, e := z.Create("App.dmg")
	must(t, e)
	_, e = w.Write([]byte("original"))
	must(t, e)
	must(t, z.Close())
	upload := func() *httptest.ResponseRecorder {
		var b bytes.Buffer
		form := multipart.NewWriter(&b)
		part, e := form.CreateFormFile("file", "App-v1.2.3.zip")
		must(t, e)
		_, e = part.Write(buf.Bytes())
		must(t, e)
		must(t, form.Close())
		return request(
			t,
			f.engine,
			"POST",
			"/openapi/v1/jobs",
			raw,
			nil,
			&b,
			form.FormDataContentType(),
		)
	}
	// 两个同时到达的请求只创建一个任务；复用者可以在提交完成前返回。
	responses := make(chan *httptest.ResponseRecorder, 2)
	for range 2 {
		go func() { responses <- upload() }()
	}
	var id string
	submitted := false
	for range 2 {
		r := <-responses
		status(t, r, 200)
		v := fields(t, r)["data"].(map[string]any)
		current := v["id"].(string)
		if id != "" && id != current {
			t.Fatal("concurrent duplicate created a second task")
		}
		id = current
		if v["status"] == "processing" && v["submission_id"] != nil {
			submitted = true
		}
	}
	if !submitted {
		t.Fatal("neither submission returned the saved Apple ID")
	}
	r := upload()
	status(t, r, 200)
	if fields(t, r)["data"].(map[string]any)["id"] != id {
		t.Fatal("existing submission was not reused")
	}
	route := "/openapi/v1/jobs/" + id
	status(t, call("GET", route+"/artifact"), 409)
	f.provider.uploadFailure = true
	status(t, call("GET", route), 500)
	var j jobsModels.Job
	must(t, s.Where("id=?", id).Take(&j).Error)
	if j.ArtifactPath == nil || j.ArtifactS3Key != nil {
		t.Fatal("unexpected cache after failed COS delivery")
	}
	f.provider.uploadFailure = false
	status(t, call("GET", route), 200)
	r = call("GET", route+"/artifact")
	status(t, r, 302)
	if r.Header().Get("Cache-Control") != "no-store" ||
		!strings.Contains(r.Header().Get("Location"), "?eo_sign=") {
		t.Fatal(r.Header())
	}
	must(t, s.Where("id=?", id).Take(&j).Error)
	if j.NotaryLog == nil || j.Status != "completed" {
		t.Fatal("missing notarization log")
	}
	p, e := storage.Resolve(*j.ArtifactPath)
	must(t, e)
	stored, e := os.ReadFile(p)
	must(t, e)
	zipfile, e := zip.NewReader(bytes.NewReader(stored), int64(len(stored)))
	must(t, e)
	stream, e := zipfile.File[0].Open()
	must(t, e)
	got, e := io.ReadAll(stream)
	must(t, e)
	_ = stream.Close()
	if string(got) != "original|signed|stapled" {
		t.Fatalf("bad archive: %q", got)
	}
	// 另一应用的有效 Key 无权访问已有任务。
	other := accessModels.Application{
		ID:         uuid.NewString(),
		Identifier: "other",
		Name:       "Other",
		Enabled:    true,
	}
	must(t, s.Create(&other).Error)
	otherKey := accessModels.Key{
		ID:            uuid.NewString(),
		ApplicationID: other.ID,
		Name:          "other",
		TokenHash:     authUtils.Digest("other-key"),
	}
	must(t, s.Create(&otherKey).Error)
	status(t, request(t, f.engine, "GET", route, "other-key", nil, nil, ""), 404)
	f.cache.failure = errors.New("redis://:never-log-this@redis.invalid/3")
	status(t, call("GET", route), 503)
	status(t, call("GET", "/ready"), 503)
	f.cache.failure = nil
}

func TestSessionAndOIDC(t *testing.T) {
	f := setup(t)
	engine := f.engine
	var user authModels.User
	must(t, db.DB().Where("username=?", "alice").Take(&user).Error)
	token := "old-browser-token"
	must(
		t,
		db.DB().
			Create(&authModels.Session{UserID: user.ID, TokenHash: authUtils.Digest(token), CSRFToken: "oldcsrf", ExpiresAt: time.Now().Add(time.Hour)}).
			Error,
	)
	ck := &http.Cookie{Name: config.Config.Web.CookieName, Value: token}
	status(t, request(t, engine, "GET", "/api/v1/me", "", ck, nil, ""), 200)
	status(t, request(t, engine, "POST", "/api/v1/auth/logout", "", ck, nil, ""), 403)
	login := request(t, engine, "GET", "/api/v1/auth/login", "", nil, nil, "")
	status(t, login, 302)
	u, e := url.Parse(login.Header().Get("Location"))
	must(t, e)
	params := u.Query()
	// 模拟排空切换前 Authlib 已写入 MySQL、浏览器仍在等待回调的记录。
	state := params.Get("state")
	var pending authModels.Login
	must(t, db.DB().First(&pending, "state_hash=?", authUtils.Digest(state)).Error)
	legacy, err := json.Marshal(
		map[string]any{
			"_state_oidc_" + state: map[string]any{
				"data": json.RawMessage(pending.Data),
				"exp":  pending.ExpiresAt.Unix(),
			},
		},
	)
	must(t, err)
	must(t, db.DB().Model(&pending).Update("data", string(legacy)).Error)
	f.provider.codes["example"] = loginCode{params, "alice"}
	callback := "/api/v1/auth/callback?code=example&state=" + params.Get("state")
	noCookie := request(t, engine, "GET", callback, "", nil, nil, "")
	if !strings.Contains(noCookie.Header().Get("Location"), "login_cookie_missing") {
		t.Fatal(noCookie.Header())
	}
	bound := login.Result().Cookies()[0]
	r := request(t, engine, "GET", callback, "", bound, nil, "")
	status(t, r, 303)
	if r.Header().Get("Location") != config.Config.Web.FrontendURL+"/jobs" {
		t.Fatal(r.Header())
	}
	r = request(t, engine, "GET", callback, "", bound, nil, "")
	if !strings.Contains(r.Header().Get("Location"), "login_expired") {
		t.Fatal(r.Header())
	}
}

func TestRecoveryHonorsActiveLease(t *testing.T) {
	f := setup(t)
	ctx := t.Context()
	j := jobsModels.Job{
		ID:            uuid.NewString(),
		ApplicationID: f.app.ID,
		Filename:      "old.zip",
		Status:        "processing",
	}
	must(t, db.DB().Create(&j).Error)
	saved := uuid.NewString()
	preserved := jobsModels.Job{
		ID:            uuid.NewString(),
		ApplicationID: f.app.ID,
		Filename:      "submitted.zip",
		Status:        "processing",
		SubmissionID:  &saved,
	}
	must(t, db.DB().Create(&preserved).Error)
	l, e := db.Acquire(ctx, j.ID, true)
	must(t, e)
	must(t, jobsTask.Scan(ctx))
	must(t, db.DB().First(&j).Error)
	if j.Status != "processing" {
		t.Fatal(j.Status)
	}
	f.cache.mu.Lock()
	f.cache.leases["apple-remote-sign:lock:"+j.ID] = entry{
		"replacement",
		time.Now().Add(time.Minute),
	}
	f.cache.mu.Unlock()
	if !errors.Is(db.Check(l.Context()), db.ErrLease) || l.Close() == nil {
		t.Fatal("lost lease was accepted")
	}
	f.cache.mu.Lock()
	delete(f.cache.leases, "apple-remote-sign:lock:"+j.ID)
	f.cache.mu.Unlock()
	must(t, jobsTask.Scan(ctx))
	must(t, db.DB().First(&j).Error)
	if j.Status != "failed" {
		t.Fatal(j.Status)
	}
	must(t, db.DB().First(&preserved).Error)
	if preserved.Status != "processing" || preserved.SubmissionID == nil ||
		*preserved.SubmissionID != saved {
		t.Fatal("recovery changed a submitted job")
	}
}
