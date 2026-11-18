package config

import (
	"net"
	"time"
)

type traceConfig struct {
	ResourceAttributes map[string]string `yaml:"resource_attributes"`
	ReportEnabled      bool              `yaml:"report_enabled"`
	Endpoint           string            `yaml:"endpoint"`
	Insecure           bool              `yaml:"insecure"`
	Headers            map[string]string `yaml:"headers"`
	ExportTimeout      time.Duration     `yaml:"export_timeout"`
	BatchTimeout       time.Duration     `yaml:"batch_timeout"`
	MaxQueueSize       int               `yaml:"max_queue_size"`
	MaxExportBatchSize int               `yaml:"max_export_batch_size"`
}

type mysqlConfig struct {
	OperationTimeout time.Duration `yaml:"operation_timeout"`
	DSN              string        `yaml:"dsn"`
	MaxOpenConns     int           `yaml:"max_open_conns"`
	MaxIdleConns     int           `yaml:"max_idle_conns"`
	ConnMaxLifetime  time.Duration `yaml:"conn_max_lifetime"`
	ConnectTimeout   time.Duration `yaml:"connect_timeout"`
	ReadTimeout      time.Duration `yaml:"read_timeout"`
	WriteTimeout     time.Duration `yaml:"write_timeout"`
}

type appConfig struct {
	Debug           bool          `yaml:"debug"`
	Name            string        `yaml:"name"`
	Environment     string        `yaml:"environment"`
	Addr            string        `yaml:"addr"`
	LogLevel        string        `yaml:"log_level"`
	ShutdownTimeout time.Duration `yaml:"shutdown_timeout"`
}

type config struct {
	HTTP struct {
		TrustedProxyCIDRs []string      `yaml:"trusted_proxy_cidrs"`
		TrustedProxyNets  []*net.IPNet  `yaml:"-"`
		ReadHeaderTimeout time.Duration `yaml:"read_header_timeout"`
		IdleTimeout       time.Duration `yaml:"idle_timeout"`
	} `yaml:"http"`
	App     appConfig     `yaml:"app"`
	MySQL   mysqlConfig   `yaml:"mysql"`
	Trace   traceConfig   `yaml:"trace"`
	Redis   redisConfig   `yaml:"redis"`
	OIDC    oidcConfig    `yaml:"oidc"`
	Web     webConfig     `yaml:"web"`
	Signing signingConfig `yaml:"signing"`
	S3      s3Config      `yaml:"s3"`
	CDN     cdnConfig     `yaml:"cdn"`
	Wecom   wecomConfig   `yaml:"wecom"`
}

type redisConfig struct {
	URL     string        `yaml:"url"`
	Timeout time.Duration `yaml:"timeout"`
}

type oidcConfig struct {
	IssuerURL               string        `yaml:"issuer_url"`
	ClientID                string        `yaml:"client_id"`
	ClientSecret            string        `yaml:"client_secret"`
	RedirectURI             string        `yaml:"redirect_uri"`
	UsernameClaim           string        `yaml:"username_claim"`
	Scopes                  string        `yaml:"scopes"`
	TokenEndpointAuthMethod string        `yaml:"token_endpoint_auth_method"`
	AllowedUsernames        []string      `yaml:"allowed_usernames"`
	AdminUsernames          []string      `yaml:"admin_usernames"`
	LoginTTL                time.Duration `yaml:"login_ttl"`
	Timeout                 time.Duration `yaml:"timeout"`
}

type webConfig struct {
	FrontendURL    string        `yaml:"frontend_url"`
	AllowedOrigins []string      `yaml:"allowed_origins"`
	CookieDomain   string        `yaml:"cookie_domain"`
	CookieName     string        `yaml:"cookie_name"`
	CookieSecure   bool          `yaml:"cookie_secure"`
	CookieSameSite string        `yaml:"cookie_same_site"`
	SessionTTL     time.Duration `yaml:"session_ttl"`
}

type signingConfig struct {
	DataDir           string        `yaml:"data_dir"`
	AppleKeyFile      string        `yaml:"apple_key_file"`
	CertificateFile   string        `yaml:"certificate_file"`
	PasswordFile      string        `yaml:"password_file"`
	Binary            string        `yaml:"binary"`
	WorkDir           string        `yaml:"work_dir"`
	ConfigFile        string        `yaml:"config_file"`
	CommandTimeout    time.Duration `yaml:"command_timeout"`
	StapleTimeout     time.Duration `yaml:"staple_timeout"`
	AppleTimeout      time.Duration `yaml:"apple_timeout"`
	MaxUploadBytes    int64         `yaml:"max_upload_bytes"`
	MaxExtractedBytes int64         `yaml:"max_extracted_bytes"`
	MaxArchiveEntries int           `yaml:"max_archive_entries"`
}

type s3Config struct {
	Bucket          string        `yaml:"bucket"`
	Prefix          string        `yaml:"prefix"`
	EndpointURL     string        `yaml:"endpoint_url"`
	Region          string        `yaml:"region"`
	AddressingStyle string        `yaml:"addressing_style"`
	ConnectTimeout  time.Duration `yaml:"connect_timeout"`
	ReadTimeout     time.Duration `yaml:"read_timeout"`
	AccessKeyID     string        `yaml:"access_key_id"`
	SecretAccessKey string        `yaml:"secret_access_key"`
	SessionToken    string        `yaml:"session_token"`
}

type cdnConfig struct {
	BaseURL   string `yaml:"base_url"`
	SignKey   string `yaml:"sign_key"`
	SignParam string `yaml:"sign_param"`
}

type wecomConfig struct {
	WebhookURL string        `yaml:"webhook_url"`
	Timeout    time.Duration `yaml:"timeout"`
}
