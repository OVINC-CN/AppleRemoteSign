package logger

import (
	"net/url"
	"regexp"
	"slices"
	"strings"
	"sync"

	"github.com/go-sql-driver/mysql"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

var lock sync.RWMutex
var secrets []string
var signedURL = regexp.MustCompile(`(?i)(https?://[^\s?]+)\?[^\s]+`)
var apiKey = regexp.MustCompile(`\bars_[a-zA-Z0-9_-]{32,}\b`)
var authority = regexp.MustCompile(`(?i)([a-z][a-z0-9+.-]*://[^\s/:@]*:)[^\s@]+@`)
var bearer = regexp.MustCompile(`(?i)\bbearer\s+[^\s]+`)
var jwtToken = regexp.MustCompile(`\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b`)
var ansi = regexp.MustCompile(`\x1b\[[0-?]*[ -/]*[@-~]`)

func init() {
	c := config.Config
	AddSecrets(
		c.MySQL.DSN,
		c.Redis.URL,
		c.OIDC.ClientSecret,
		c.CDN.SignKey,
		c.S3.SecretAccessKey,
		c.S3.AccessKeyID,
		c.S3.SessionToken,
		c.Wecom.WebhookURL,
	)
	if d, e := mysql.ParseDSN(c.MySQL.DSN); e == nil {
		AddSecrets(d.Passwd)
	}
	if u, e := url.Parse(c.Redis.URL); e == nil && u.User != nil {
		p, _ := u.User.Password()
		AddSecrets(p)
	}
	if u, e := url.Parse(c.Wecom.WebhookURL); e == nil {
		AddSecrets(u.Query().Get("key"))
	}
}

func AddSecrets(values ...string) {
	lock.Lock()
	defer lock.Unlock()
	for _, s := range values {
		if s != "" && !slices.Contains(secrets, s) {
			secrets = append(secrets, s)
			slices.SortFunc(secrets, func(a, b string) int { return len(b) - len(a) })
		}
	}
}

func Clean(s string) string {
	lock.RLock()
	defer lock.RUnlock()
	s = ansi.ReplaceAllString(s, "")
	for _, v := range secrets {
		s = strings.ReplaceAll(s, v, "[REDACTED]")
	}
	s = authority.ReplaceAllString(s, "${1}[REDACTED]@")
	s = bearer.ReplaceAllString(s, "Bearer [REDACTED]")
	s = apiKey.ReplaceAllString(s, "[REDACTED]")
	s = jwtToken.ReplaceAllString(s, "[REDACTED]")
	return signedURL.ReplaceAllString(s, "${1}?[REDACTED]")
}
