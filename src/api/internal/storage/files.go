package storage

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
)

func Resolve(relative string) (string, error) {
	if !filepath.IsLocal(relative) || relative == "." {
		return "", errors.New("Invalid stored file path.")
	}
	root, e := filepath.EvalSymlinks(config.Config.Signing.DataDir)
	if e != nil {
		return "", e
	}
	full := filepath.Join(root, relative)
	// 已有路径中的每一层都必须留在共享卷内，拒绝悬空链接。
	for p := full; p != root; p = filepath.Dir(p) {
		_, err := os.Lstat(p)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return "", err
		}
		v, err := filepath.EvalSymlinks(p)
		if err != nil {
			return "", err
		}
		if v != root && !strings.HasPrefix(v, root+string(os.PathSeparator)) {
			return "", errors.New("Invalid stored file path.")
		}
		break
	}
	return full, nil
}

type guarded struct {
	ctx context.Context
	src io.Reader
}

func (r guarded) Read(b []byte) (int, error) {
	if e := context.Cause(r.ctx); e != nil {
		return 0, e
	}
	return r.src.Read(b)
}

func Hash(ctx context.Context, p string) (string, error) {
	f, e := os.Open(p) // #nosec G304 -- 调用方仅传入经 Resolve 验证或本服务生成的文件。
	if e != nil {
		return "", e
	}
	defer func() { _ = f.Close() }()
	h := sha256.New()
	if _, e = io.Copy(h, guarded{ctx, f}); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func Copy(ctx context.Context, src, relative string) (string, error) {
	p, e := Resolve(relative)
	if e != nil {
		return "", e
	}
	if e = os.MkdirAll(filepath.Dir(p), 0700); e != nil {
		return "", e
	}
	in, e := os.Open(src) // #nosec G304 -- 源是上传暂存文件或经过 Resolve 验证的持久化路径。
	if e != nil {
		return "", e
	}
	defer func() { _ = in.Close() }()
	f, e := os.CreateTemp(filepath.Dir(p), ".upload-")
	if e != nil {
		return "", e
	}
	defer func() { _ = os.Remove(f.Name()) }()
	_, e = io.Copy(f, guarded{ctx, in})
	e = errors.Join(e, f.Sync(), f.Close())
	if e == nil {
		e = db.Check(ctx)
	}
	if e == nil {
		e = os.Rename(f.Name(), p)
	}
	return p, e
}

var (
	ErrEmptyUpload = errors.New("Uploaded file is empty; upload a file containing data.")
	ErrLargeUpload = errors.New("Upload exceeds MAX_UPLOAD_BYTES.")
)

func Spool(ctx context.Context, r io.Reader) (path, hash string, size int64, err error) {
	f, e := os.CreateTemp("", "ars-upload-")
	if e != nil {
		return "", "", 0, e
	}
	h := sha256.New()
	limit := config.Config.Signing.MaxUploadBytes
	n, e := io.Copy(io.MultiWriter(f, h), io.LimitReader(guarded{ctx, r}, limit+1))
	e = errors.Join(e, f.Close())
	if n > limit {
		e = ErrLargeUpload
	} else if n == 0 && e == nil {
		e = ErrEmptyUpload
	}
	if e != nil {
		_ = os.Remove(f.Name())
		return "", "", 0, e
	}
	return f.Name(), hex.EncodeToString(h.Sum(nil)), n, nil
}
