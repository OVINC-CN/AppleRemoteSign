package wecom

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

var markdown = regexp.MustCompile("[\\\\`*_\\[\\]<>]")

func escape(s string) string {
	return markdown.ReplaceAllString(
		strings.Join(
			strings.FieldsFunc(s, func(r rune) bool { return r == '\n' || r == '\r' }),
			" ",
		),
		"\\$0",
	)
}

func Notify(ctx context.Context, event, file, app, id, source string) {
	cfg := config.Config.Wecom
	if cfg.WebhookURL == "" {
		return
	}
	title := map[string]string{"submitted": "新签名提交", "completed": "签名成功", "failed": "签名失败"}[event]
	content := fmt.Sprintf(
		"**AppleSign · %s**\n**文件名**: %s\n**应用名称**: %s\n**任务 ID**: %s\n**提交人**: %s",
		title,
		escape(file),
		escape(app),
		escape(id),
		escape(source),
	)
	if len(content) > 4096 {
		content = content[:4096]
		for !utf8.ValidString(content) {
			content = content[:len(content)-1]
		}
	}
	data, _ := json.Marshal(
		map[string]any{"msgtype": "markdown", "markdown": map[string]string{"content": content}},
	)
	req, e := http.NewRequestWithContext(ctx, "POST", cfg.WebhookURL, bytes.NewReader(data))
	if e != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	client := http.Client{
		Timeout:       cfg.Timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	r, e := client.Do(req)
	if e == nil {
		defer func() { _ = r.Body.Close() }()
		var result struct {
			Code *int `json:"errcode"`
		}
		if r.StatusCode/100 == 2 && json.NewDecoder(r.Body).Decode(&result) == nil &&
			result.Code != nil &&
			*result.Code == 0 {
			return
		}
	}
	logger.Logger.WithContext(ctx).Warnf("wecom.notification_failed job_id=%s event=%s", id, event)
}
