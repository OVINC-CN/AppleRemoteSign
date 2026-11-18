package archive

import (
	"archive/zip"
	"context"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
)

type reader struct {
	ctx context.Context
	r   io.Reader
}

func (r reader) Read(b []byte) (int, error) {
	if err := context.Cause(r.ctx); err != nil {
		return 0, err
	}
	return r.r.Read(b)
}

func extract(ctx context.Context, file, dir string) (string, error) {
	z, e := zip.OpenReader(file)
	if e != nil {
		return "", e
	}
	defer func() { _ = z.Close() }()
	m, e := inspect(&z.Reader)
	if e != nil {
		return "", e
	}
	remain := config.Config.Signing.MaxExtractedBytes
	for name, f := range m.entries {
		if e := ctx.Err(); e != nil {
			return "", e
		}
		dst := filepath.Join(dir, name)
		e = os.MkdirAll(filepath.Dir(dst), 0755) // #nosec G301 -- 包内目录需要保留可遍历权限，工作目录为私有。
		if e != nil {
			return "", e
		}
		mode := f.Mode()
		switch {
		case mode.IsDir():
			e = os.MkdirAll(dst, 0700)
			if e == nil {
				e = os.Chmod(dst, mode.Perm()|0700) // #nosec G302 -- 还原已验证归档中的目录权限。
			}
		case mode.IsRegular():
			var n int64
			n, e = extractFile(ctx, f, dst, remain)
			remain -= n
		}
		if e != nil {
			return "", e
		}
	}
	// 先完成普通文件写入，再创建已验证全部目标的符号链接。
	for name, target := range m.links {
		if e = os.Symlink(target, filepath.Join(dir, name)); e != nil {
			return "", e
		}
	}
	return filepath.Join(dir, m.root), nil
}

func extractFile(ctx context.Context, f *zip.File, dst string, remain int64) (int64, error) {
	r, e := f.Open()
	if e != nil {
		return 0, e
	}
	defer func() { _ = r.Close() }()
	w, e := os.OpenFile(
		dst,
		os.O_WRONLY|os.O_CREATE|os.O_EXCL,
		f.Mode().Perm()|0600,
	) // #nosec G304 -- 清单已验证路径，所有链接在普通文件写完后才创建。
	if e != nil {
		return 0, e
	}
	n, e := io.Copy(w, io.LimitReader(reader{ctx, r}, remain+1))
	e = errors.Join(e, w.Chmod(f.Mode().Perm()|0600), w.Close())
	if n > remain {
		return n, errors.New("ZIP exceeds MAX_EXTRACTED_BYTES.")
	}
	return n, e
}

func pack(ctx context.Context, asset, dst string) error {
	root, err := os.OpenRoot(filepath.Dir(asset))
	if err != nil {
		return err
	}
	defer func() { _ = root.Close() }()

	f, e := os.OpenFile(
		dst,
		os.O_CREATE|os.O_EXCL|os.O_WRONLY,
		0600,
	) // #nosec G304 -- 私有临时目录中的固定输出。
	if e != nil {
		return e
	}
	z := zip.NewWriter(f)
	err = fs.WalkDir(root.FS(), filepath.Base(asset), func(p string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if e = ctx.Err(); e != nil {
			return e
		}
		info, e := d.Info()
		if e != nil {
			return e
		}
		hdr, e := zip.FileInfoHeader(info)
		if e != nil {
			return e
		}
		hdr.Name = p
		if d.IsDir() {
			hdr.Name += "/"
		} else if info.Mode().IsRegular() {
			hdr.Method = zip.Deflate
		}
		w, e := z.CreateHeader(hdr)
		if e != nil {
			return e
		}
		switch {
		case d.IsDir():
			return nil
		case info.Mode()&fs.ModeSymlink != 0:
			target, e := root.Readlink(p)
			if e != nil {
				return e
			}
			_, e = io.WriteString(w, target)
			return e
		case info.Mode().IsRegular():
			in, e := root.Open(p)
			if e != nil {
				return e
			}
			defer func() { _ = in.Close() }()
			_, e = io.Copy(w, reader{ctx, in})
			return e
		}
		return errors.New("Unexpected special file.")
	})
	return errors.Join(err, z.Close(), f.Sync(), f.Close())
}

func Transform(
	ctx context.Context,
	file string,
	op func(context.Context, string) (string, error),
) (string, error) {
	if e := db.Check(ctx); e != nil {
		return "", e
	}
	if !strings.EqualFold(filepath.Ext(file), ".zip") {
		return op(ctx, file)
	}
	dir, e := os.MkdirTemp(filepath.Dir(file), ".app-")
	if e != nil {
		return "", e
	}
	defer func() { _ = os.RemoveAll(dir) }()
	asset, e := extract(ctx, file, dir)
	if e != nil {
		return "", e
	}
	out, e := op(ctx, asset)
	if e != nil {
		return out, e
	}
	dst := filepath.Join(dir, "processed.zip")
	if e = pack(ctx, asset, dst); e == nil {
		e = Validate(dst)
	}
	if e == nil {
		e = db.Check(ctx)
	}
	if e == nil {
		e = os.Rename(dst, file)
	}
	return out, e
}
