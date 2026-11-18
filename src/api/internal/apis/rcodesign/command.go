package rcodesign

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

var submission = regexp.MustCompile(`\bcreated submission ID:\s*([0-9a-fA-F-]{36})\b`)

func run(
	ctx context.Context,
	op string,
	args []string,
	timeout time.Duration,
	onLine func(string) error,
) (string, error) {
	if err := db.Check(ctx); err != nil {
		return "", err
	}
	cfg := config.Config.Signing
	toml := "/dev/null"
	if op == "sign" && cfg.ConfigFile != "" {
		toml = cfg.ConfigFile
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext( // #nosec G204 -- 可执行文件来自管理员配置，参数以独立 argv 传入。
		ctx,
		cfg.Binary,
		append([]string{"--config-file", toml, op}, args...)...)
	cmd.Dir = cfg.WorkDir
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	cmd.Env = []string{}
	for _, k := range []string{"PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"} {
		if v, ok := os.LookupEnv(k); ok {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
	}
	r, w, e := os.Pipe()
	if e != nil {
		return "", e
	}
	defer func() { _ = r.Close() }()
	cmd.Stdout = w
	cmd.Stderr = w
	e = cmd.Start()
	_ = w.Close()
	if e != nil {
		return "", errors.New("Unable to start rcodesign.")
	}
	defer func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }()
	buf := bufio.NewReaderSize(r, 16384)
	var tail string
	var readErr error
	for {
		line, err := buf.ReadSlice('\n')
		if len(line) > 0 {
			clean := logger.Clean(string(line))
			tail += clean
			if len(tail) > 32768 {
				tail = tail[len(tail)-32768:]
			}
			if onLine != nil {
				if readErr = onLine(clean); readErr != nil {
					cancel()
					break
				}
			}
		}
		if err != nil && !errors.Is(err, bufio.ErrBufferFull) {
			if !errors.Is(err, io.EOF) {
				readErr = err
			}
			break
		}
	}
	waitErr := cmd.Wait()
	if readErr != nil {
		return tail, readErr
	}
	if err := context.Cause(ctx); err != nil {
		return tail, err
	}
	if waitErr != nil {
		return tail, fmt.Errorf(
			"rcodesign %s failed: %w. %s",
			op,
			waitErr,
			strings.TrimSpace(tail[max(0, len(tail)-2000):]),
		)
	}
	return tail, db.Check(ctx)
}

func Sign(ctx context.Context, file string) (string, error) {
	c := config.Config.Signing
	return run(
		ctx,
		"sign",
		[]string{
			"--for-notarization",
			"--p12-file",
			c.CertificateFile,
			"--p12-password-file",
			c.PasswordFile,
			file,
		},
		c.CommandTimeout,
		nil,
	)
}

func Staple(ctx context.Context, file string) (string, error) {
	return run(ctx, "staple", []string{file}, config.Config.Signing.StapleTimeout, nil)
}

func Submit(ctx context.Context, file string, save func(string) error) (string, error) {
	var found string
	c := config.Config.Signing
	out, e := run(
		ctx,
		"notary-submit",
		[]string{"--api-key-file", c.AppleKeyFile, file},
		c.CommandTimeout,
		func(line string) error {
			m := submission.FindStringSubmatch(line)
			if m == nil {
				return nil
			}
			id, e := uuid.Parse(m[1])
			if e != nil {
				return e
			}
			if found != "" && found != id.String() {
				return errors.New("rcodesign reported multiple submission IDs.")
			}
			if found != "" {
				return nil
			}
			found = id.String()
			return save(found)
		},
	)
	if e == nil && strings.TrimSpace(found) == "" {
		e = errors.New("rcodesign finished without reporting a submission ID.")
	}
	return out, e
}
