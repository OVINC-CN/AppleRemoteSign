package config

import (
	"errors"
	"math"
	"net"
	"net/url"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

func (c *config) Allowed(name string) bool {
	return slices.Contains(c.OIDC.AllowedUsernames, name) ||
		slices.Contains(c.OIDC.AdminUsernames, name)
}

func (c *config) Origins() []string {
	return append(slices.Clone(c.Web.AllowedOrigins), c.Web.FrontendURL)
}

func endpoint(raw string, origin bool) bool {
	u, err := url.Parse(raw)
	if err != nil {
		return false
	}
	local := slices.Contains([]string{"localhost", "127.0.0.1", "testserver"}, u.Hostname())
	return !strings.Contains(u.Hostname(), "*") && u.Hostname() != "" && u.User == nil &&
		u.Fragment == "" &&
		u.RawQuery == "" &&
		(!origin || u.Path == "" || u.Path == "/") &&
		(u.Scheme == "https" || u.Scheme == "http" && local)
}

func (c *config) validate() error {
	// 校验部署参数，再初始化连接池和追踪。
	_, port, err := net.SplitHostPort(c.App.Addr)
	number, portErr := strconv.Atoi(port)
	if err != nil || portErr != nil || number < minimumTCPPort || number > maximumTCPPort {
		return errors.New("invalid HTTP listen address")
	}
	c.HTTP.TrustedProxyNets = make([]*net.IPNet, 0, len(c.HTTP.TrustedProxyCIDRs))
	for _, cidr := range c.HTTP.TrustedProxyCIDRs {
		_, network, err := net.ParseCIDR(cidr)
		if err != nil {
			return errors.New("http.trusted_proxy_cidrs must contain valid CIDR ranges")
		}
		c.HTTP.TrustedProxyNets = append(c.HTTP.TrustedProxyNets, network)
	}
	if c.MySQL.MaxOpenConns <= 0 || c.MySQL.MaxIdleConns < 0 ||
		c.MySQL.MaxIdleConns > c.MySQL.MaxOpenConns {
		return errors.New("invalid MySQL pool capacity")
	}
	if c.Trace.ReportEnabled &&
		(c.Trace.Endpoint == "" || c.Trace.ExportTimeout <= 0 || c.Trace.BatchTimeout <= 0 || c.Trace.MaxQueueSize <= 0 || c.Trace.MaxExportBatchSize <= 0 || c.Trace.MaxExportBatchSize > c.Trace.MaxQueueSize) {
		return errors.New("invalid trace exporter settings")
	}
	for _, d := range []time.Duration{
		c.HTTP.ReadHeaderTimeout,
		c.HTTP.IdleTimeout,
		c.App.ShutdownTimeout,
		c.MySQL.ConnectTimeout,
		c.MySQL.ReadTimeout,
		c.MySQL.WriteTimeout,
		c.MySQL.OperationTimeout,
		c.Redis.Timeout,
		c.OIDC.LoginTTL,
		c.OIDC.Timeout,
		c.Web.SessionTTL,
		c.Signing.CommandTimeout,
		c.Signing.StapleTimeout,
		c.Signing.AppleTimeout,
		c.COS.ConnectTimeout,
		c.COS.ReadTimeout,
		c.Wecom.Timeout,
	} {
		if d <= 0 {
			return errors.New("timeouts must be positive")
		}
	}
	methods := []string{"client_secret_basic", "client_secret_post"}
	if c.OIDC.LoginTTL > 10*time.Minute || len(c.OIDC.AdminUsernames) == 0 ||
		c.OIDC.ClientID == "" ||
		!slices.Contains(strings.Fields(c.OIDC.Scopes), "openid") ||
		!slices.Contains(methods, c.OIDC.TokenEndpointAuthMethod) {
		return errors.New("invalid OIDC settings")
	}
	if !endpoint(c.OIDC.IssuerURL, false) || !endpoint(c.OIDC.RedirectURI, false) ||
		!endpoint(c.CDN.BaseURL, true) {
		return errors.New("invalid public endpoint")
	}
	c.Web.FrontendURL = strings.TrimSuffix(c.Web.FrontendURL, "/")
	for i := range c.Web.AllowedOrigins {
		c.Web.AllowedOrigins[i] = strings.TrimSuffix(c.Web.AllowedOrigins[i], "/")
	}
	for _, origin := range c.Origins() {
		if !endpoint(origin, true) {
			return errors.New("invalid allowed origin")
		}
	}
	if !regexp.MustCompile("^[A-Za-z0-9_-]{1,64}$").MatchString(c.Web.CookieName) ||
		!slices.Contains([]string{"none", "lax"}, c.Web.CookieSameSite) ||
		c.Web.CookieSameSite == "none" && !c.Web.CookieSecure {
		return errors.New("invalid session cookie settings")
	}
	redirect, _ := url.Parse(c.OIDC.RedirectURI)
	domain := strings.TrimPrefix(strings.ToLower(c.Web.CookieDomain), ".")
	host := strings.ToLower(redirect.Hostname())
	if domain != "" && host != domain && !strings.HasSuffix(host, "."+domain) {
		return errors.New("cookie domain must match API host")
	}
	if c.Signing.MaxUploadBytes <= 0 || c.Signing.MaxUploadBytes > math.MaxInt64-(1<<20) ||
		c.Signing.MaxExtractedBytes == math.MaxInt64 ||
		c.Signing.MaxExtractedBytes <= 0 ||
		c.Signing.MaxArchiveEntries <= 0 ||
		!filepath.IsAbs(c.Signing.DataDir) {
		return errors.New("invalid storage settings")
	}
	if !endpoint(c.COS.BucketURL, true) {
		return errors.New("invalid COS bucket URL")
	}
	if (c.COS.SecretID == "") != (c.COS.SecretKey == "") ||
		c.COS.SessionToken != "" && c.COS.SecretID == "" {
		return errors.New("invalid COS credentials")
	}
	c.COS.Prefix = strings.Trim(c.COS.Prefix, "/")
	if c.COS.Prefix != "" {
		for _, part := range strings.Split(c.COS.Prefix, "/") {
			if part == "." || part == ".." || strings.ContainsAny(part, "\\\r\n") {
				return errors.New("invalid COS prefix")
			}
		}
		c.COS.Prefix += "/"
	}
	if !regexp.MustCompile("^[a-zA-Z0-9_-]{1,100}$").MatchString(c.CDN.SignParam) {
		return errors.New("invalid CDN signing parameter")
	}
	for _, name := range append(slices.Clone(c.OIDC.AllowedUsernames), c.OIDC.AdminUsernames...) {
		if name == "" || utf8.RuneCountInString(name) > 255 {
			return errors.New("invalid OIDC username")
		}
	}
	if c.Wecom.WebhookURL != "" {
		u, e := url.Parse(c.Wecom.WebhookURL)
		if e != nil || u.Host == "" || u.Scheme != "https" || u.User != nil || u.Fragment != "" {
			return errors.New("invalid WeCom webhook")
		}
	}
	return nil
}
