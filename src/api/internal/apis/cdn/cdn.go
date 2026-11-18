package cdn

import (
	"crypto/md5" // #nosec G501 -- EdgeOne 方式 A 的固定协议。
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

func Escape(value string) string { return strings.ReplaceAll(url.QueryEscape(value), "+", "%20") }
func Sign(key string) string {
	cfg := config.Config.CDN
	path := "/" + strings.ReplaceAll(Escape(key), "%2F", "/")
	var nonce [16]byte
	rand.Read(nonce[:])
	value := fmt.Sprintf("%d-%s-0", time.Now().Unix(), hex.EncodeToString(nonce[:]))
	digest := md5.Sum(
		[]byte(path + "-" + value + "-" + cfg.SignKey),
	) // #nosec G401 -- EdgeOne 方式 A 的固定协议。
	return fmt.Sprintf(
		"%s%s?%s=%s-%x",
		strings.TrimRight(cfg.BaseURL, "/"),
		path,
		cfg.SignParam,
		value,
		digest,
	)
}
