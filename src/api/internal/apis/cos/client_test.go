package cos

import (
	"context"
	"encoding/xml"
	"hash/crc64"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"

	sdk "github.com/tencentyun/cos-go-sdk-v5"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

type cosTransport func(*http.Request) (*http.Response, error)

func (fn cosTransport) RoundTrip(r *http.Request) (*http.Response, error) { return fn(r) }

func TestUpload(t *testing.T) {
	const (
		key         = "app-dist/任务/App-v1.0.0.zip"
		name        = "App-v1.0.0.zip"
		disposition = "attachment; filename*=UTF-8''" + name
	)
	for _, test := range []struct {
		name   string
		size   int64
		fail   bool
		cancel bool
	}{
		{name: "single", size: 1024},
		{name: "multipart", size: multipartThreshold + 1},
		{name: "part failure", size: multipartThreshold, fail: true},
		{name: "lease canceled", size: multipartThreshold, cancel: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			cfg := &config.Config.COS
			original := *cfg
			oldTransport := http.DefaultTransport
			t.Cleanup(func() {
				*cfg = original
				http.DefaultTransport = oldTransport
				client = nil
			})
			cfg.BucketURL = "https://artifacts-1250000000.cos.ap-guangzhou.myqcloud.com"
			cfg.SecretID, cfg.SecretKey, cfg.SessionToken = "test-id", "test-key", "test-token"

			file, err := os.CreateTemp(t.TempDir(), "artifact-*.zip")
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = file.Close() }()
			if err = file.Truncate(test.size); err != nil {
				t.Fatal(err)
			}

			var lock sync.Mutex
			var started, completed, aborted, stored bool
			http.DefaultTransport = cosTransport(func(r *http.Request) (*http.Response, error) {
				lock.Lock()
				defer lock.Unlock()
				if r.URL.Path != "/"+key ||
					!strings.HasPrefix(r.Header.Get("Authorization"), "q-sign-") ||
					r.Header.Get("X-Cos-Security-Token") != "test-token" {
					t.Error("unexpected COS path or authorization")
				}
				if r.Header.Get("Content-Encoding") != "" ||
					r.Header.Get("X-Cos-Meta-Sha256") != "" {
					t.Error("unexpected Content-Encoding or custom metadata")
				}
				for name := range r.Header {
					if strings.HasPrefix(strings.ToLower(name), "x-amz-") {
						t.Errorf("unexpected AWS header %s", name)
					}
				}
				rec := httptest.NewRecorder()
				part := r.URL.Query().Get("partNumber")
				switch {
				case r.Method == http.MethodHead:
					if !stored {
						rec.WriteHeader(http.StatusNotFound)
					}
				case r.Method == http.MethodPost && r.URL.Query().Has("uploads"):
					started = true
					_, _ = io.WriteString(
						rec,
						"<InitiateMultipartUploadResult><UploadId>test</UploadId></InitiateMultipartUploadResult>",
					)
				case r.Method == http.MethodPut && part != "":
					if test.fail {
						rec.WriteHeader(http.StatusForbidden)
						break
					}
					if test.cancel {
						cancel()
					}
					number, _ := strconv.Atoi(part)
					want := min(int64(minimumPartSize), test.size-int64(number-1)*minimumPartSize)
					recordUploadBody(t, rec, r, want)
					rec.Header().Set("ETag", part)
				case r.Method == http.MethodPost && r.URL.Query().Get("uploadId") == "test":
					var opt sdk.CompleteMultipartUploadOptions
					if err := xml.NewDecoder(r.Body).Decode(&opt); err != nil {
						t.Error(err)
					}
					if len(opt.Parts) != 3 {
						t.Errorf("expected three parts, got %+v", opt.Parts)
					}
					for i, part := range opt.Parts {
						if part.PartNumber != i+1 || part.ETag != strconv.Itoa(i+1) {
							t.Errorf("unexpected completed part %+v", part)
						}
					}
					completed, stored = true, true
					_, _ = io.WriteString(
						rec,
						"<CompleteMultipartUploadResult><ETag>done</ETag></CompleteMultipartUploadResult>",
					)
				case r.Method == http.MethodDelete && r.URL.Query().Get("uploadId") == "test":
					aborted = true
				case r.Method == http.MethodPut:
					recordUploadBody(t, rec, r, test.size)
					stored = true
				default:
					t.Errorf("unexpected COS operation %s %s", r.Method, r.URL)
					rec.WriteHeader(http.StatusBadRequest)
				}
				if (r.Method == http.MethodPut && part == "") || r.URL.Query().Has("uploads") {
					if r.Header.Get("Content-Type") != "application/zip" ||
						r.Header.Get("Content-Disposition") != disposition {
						t.Error("missing download headers")
					}
				}
				resp := rec.Result()
				resp.Request = r
				return resp, nil
			})

			if exists, err := Exists(ctx, key); err != nil || exists {
				t.Fatalf("missing object returned %v, %v", exists, err)
			}
			err = Upload(ctx, file, key, name)
			if (err != nil) != (test.fail || test.cancel) {
				t.Fatalf("unexpected upload result: %v", err)
			}
			if started != (test.size >= multipartThreshold) ||
				completed != (started && err == nil) || aborted != test.fail {
				t.Fatalf("multipart started=%t complete=%t abort=%t", started, completed, aborted)
			}
			if err == nil {
				if exists, err := Exists(ctx, key); err != nil || !exists {
					t.Fatalf("uploaded object returned %v, %v", exists, err)
				}
			}
		})
	}
}

func recordUploadBody(t *testing.T, rec *httptest.ResponseRecorder, r *http.Request, want int64) {
	t.Helper()
	digest := crc64.New(crc64.MakeTable(crc64.ECMA))
	n, err := io.Copy(digest, r.Body)
	if err != nil {
		t.Error(err)
	}
	if n != want || r.ContentLength != want {
		t.Errorf("body=%d Content-Length=%d, want %d", n, r.ContentLength, want)
	}
	rec.Header().Set("X-Cos-Hash-Crc64ecma", strconv.FormatUint(digest.Sum64(), 10))
}
