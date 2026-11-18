package apple

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/tracing"
)

const endpoint = "https://appstoreconnect.apple.com/notary/v2/submissions/"

var key *ecdsa.PrivateKey
var issuer, keyID string

func Load() error {
	b, e := os.ReadFile(config.Config.Signing.AppleKeyFile)
	if e != nil {
		return e
	}
	var doc struct {
		Issuer  string `json:"issuer_id"`
		Key     string `json:"key_id"`
		Private string `json:"private_key"`
	}
	bad := errors.New("Invalid Apple credential document.")
	if json.Unmarshal(b, &doc) != nil || doc.Issuer == "" ||
		!regexp.MustCompile("^[A-Za-z0-9]+$").MatchString(doc.Key) {
		return bad
	}
	der, e := base64.StdEncoding.DecodeString(doc.Private)
	if e != nil {
		return bad
	}
	pk, e := x509.ParsePKCS8PrivateKey(der)
	if e != nil {
		pk, e = x509.ParseECPrivateKey(der)
	}
	k, ok := pk.(*ecdsa.PrivateKey)
	if e != nil || !ok || k.Curve != elliptic.P256() {
		return bad
	}
	key, issuer, keyID = k, doc.Issuer, doc.Key
	logger.AddSecrets(doc.Private)
	return nil
}

func get(ctx context.Context, uri string, signed bool) (map[string]any, error) {
	ctx, span := tracing.Tracer.Start(ctx, "apple.http")
	defer span.End()
	req, e := http.NewRequestWithContext(ctx, "GET", uri, nil)
	if e != nil {
		return nil, errors.New("Invalid Apple URL.")
	}
	req.Header.Set("Accept", "application/json")
	if signed {
		now := time.Now().Unix()
		t := jwt.NewWithClaims(
			jwt.SigningMethodES256,
			jwt.MapClaims{"iss": issuer, "iat": now, "exp": now + 300, "aud": "appstoreconnect-v1"},
		)
		t.Header["kid"] = keyID
		token, e := t.SignedString(key)
		if e != nil {
			return nil, e
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}
	client := http.Client{
		Timeout:       config.Config.Signing.AppleTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	r, e := client.Do(req)
	if e != nil {
		return nil, errors.New("Apple request failed.")
	}
	defer func() { _ = r.Body.Close() }()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("Apple request returned HTTP %d.", r.StatusCode)
	}
	var v map[string]any
	dec := json.NewDecoder(io.LimitReader(r.Body, 64<<20))
	dec.UseNumber()
	if dec.Decode(&v) != nil || v == nil {
		return nil, errors.New("Apple returned invalid JSON.")
	}
	return v, nil
}

func attrs(ctx context.Context, id, suffix string) (map[string]any, error) {
	v, e := get(ctx, endpoint+id+suffix, true)
	if e != nil {
		return nil, e
	}
	data, _ := v["data"].(map[string]any)
	if data["id"] != id {
		return nil, errors.New("Apple returned an unexpected submission ID.")
	}
	a, ok := data["attributes"].(map[string]any)
	if !ok {
		return nil, errors.New("Apple response is missing submission attributes.")
	}
	return a, nil
}

func Status(ctx context.Context, id string) (string, error) {
	a, e := attrs(ctx, id, "")
	if e != nil {
		return "", e
	}
	switch s := a["status"]; s {
	case "In Progress", "Accepted", "Invalid", "Rejected":
		return s.(string), nil
	}
	return "", errors.New("Apple returned an unrecognized notarization status.")
}

func Log(ctx context.Context, id string) (map[string]any, error) {
	a, e := attrs(ctx, id, "/logs")
	if e != nil {
		return nil, e
	}
	uri, _ := a["developerLogUrl"].(string)
	u, e := url.Parse(uri)
	if e != nil || u.Scheme != "https" || u.Host == "" || u.User != nil {
		return nil, errors.New("Invalid Apple log URL.")
	}
	// Apple 提供的是独立的预签名地址，日志下载请求只使用该地址自带的鉴权。
	return get(ctx, uri, false)
}
