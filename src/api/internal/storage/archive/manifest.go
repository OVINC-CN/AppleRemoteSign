package archive

import (
	"archive/zip"
	"bytes"
	"errors"
	"io"
	"io/fs"
	"os"
	"path"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
	"howett.net/plist"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

type manifest struct {
	root    string
	entries map[string]*zip.File
	nodes   map[string]fs.FileMode
	links   map[string]string
}

func safePart(p string) bool {
	return p != "" && p != "." && p != ".." && len(p) <= 255 && utf8.ValidString(p) &&
		strings.IndexFunc(
			p,
			func(r rune) bool { return r < 32 || r == 127 || r == ':' || r == '\\' },
		) < 0
}

func Filename(name string) bool { return safePart(name) && !strings.Contains(name, "/") }
func readSmall(f *zip.File, max int64) ([]byte, error) {
	if max < 0 || f.UncompressedSize64 > uint64(max) {
		return nil, errors.New("ZIP entry too large")
	}
	r, e := f.Open()
	if e != nil {
		return nil, e
	}
	defer func() { _ = r.Close() }()
	return io.ReadAll(io.LimitReader(r, max+1))
}

func metadata(f *zip.File, name, root string) bool {
	if root == "__MACOSX" || root == ".DS_Store" {
		return true
	}
	if !f.Mode().IsRegular() || !strings.HasPrefix(path.Base(name), "._") {
		return false
	}
	r, e := f.Open()
	if e != nil {
		return false
	}
	defer func() { _ = r.Close() }()
	var magic [8]byte
	_, e = io.ReadFull(r, magic[:])
	return e == nil && bytes.Equal(magic[:], []byte{0, 5, 22, 7, 0, 2, 0, 0})
}

func inspect(z *zip.Reader) (*manifest, error) {
	cfg := config.Config.Signing
	if len(z.File) > cfg.MaxArchiveEntries {
		return nil, errors.New("ZIP exceeds MAX_ARCHIVE_ENTRIES.")
	}
	m := &manifest{
		entries: map[string]*zip.File{},
		nodes:   map[string]fs.FileMode{},
		links:   map[string]string{},
	}
	spellings := map[string]string{}
	if cfg.MaxExtractedBytes <= 0 {
		return nil, errors.New("invalid archive size limit")
	}
	remaining := uint64(cfg.MaxExtractedBytes)
	fold := cases.Fold()
	for _, f := range z.File {
		if f.UncompressedSize64 > remaining {
			return nil, errors.New("ZIP exceeds MAX_EXTRACTED_BYTES.")
		}
		remaining -= f.UncompressedSize64
		name := strings.TrimSuffix(f.Name, "/")
		parts := strings.Split(name, "/")
		if len(parts) > 128 {
			return nil, errors.New("ZIP path is too deep.")
		}
		for _, p := range parts {
			if !safePart(p) {
				return nil, errors.New("ZIP contains an invalid or unsafe entry path.")
			}
		}
		kind := f.Mode().Type()
		if f.CreatorVersion>>8 == 3 && strings.HasSuffix(f.Name, "/") {
			rawType := (f.ExternalAttrs >> 16) & 0170000
			if rawType != 0 && rawType != 0040000 {
				return nil, errors.New("ZIP directory type mismatch.")
			}
		}
		if kind != 0 && kind != fs.ModeDir && kind != fs.ModeSymlink || f.Flags&1 != 0 ||
			f.Method != zip.Store && f.Method != zip.Deflate {
			return nil, errors.New("Unsupported ZIP entry.")
		}
		if metadata(f, name, parts[0]) {
			continue
		}
		if m.root == "" {
			m.root = parts[0]
		}
		if parts[0] != m.root {
			return nil, errors.New(
				"ZIP must contain exactly one top-level .app directory or .dmg file.",
			)
		}
		if _, found := m.entries[name]; found {
			return nil, errors.New("ZIP contains duplicate entries.")
		}
		m.entries[name] = f
		for i := range parts {
			node := strings.Join(parts[:i+1], "/")
			mode := fs.ModeDir
			if i == len(parts)-1 {
				mode = kind
			}
			key := fold.String(norm.NFD.String(node))
			if prev, ok := spellings[key]; ok && prev != node {
				return nil, errors.New("ZIP contains paths that collide on macOS filesystems.")
			}
			spellings[key] = node
			if v, ok := m.nodes[node]; ok && v != mode {
				return nil, errors.New("Invalid ZIP parent directory.")
			}
			m.nodes[node] = mode
		}
		if kind == fs.ModeSymlink {
			data, e := readSmall(f, 4096)
			if e != nil || len(data) == 0 || data[0] == '/' || !utf8.Valid(data) ||
				strings.IndexFunc(
					string(data),
					func(r rune) bool { return r < 32 || r == 127 || r == ':' || r == '\\' },
				) >= 0 {
				return nil, errors.New("Invalid ZIP link target.")
			}
			m.links[name] = string(data)
		}
	}
	for name := range m.links {
		resolved, e := m.resolve(name)
		if e != nil {
			return nil, e
		}
		if _, ok := m.nodes[resolved]; !ok || strings.HasPrefix(name, resolved+"/") {
			return nil, errors.New("Dangling or ancestor ZIP link.")
		}
	}
	switch strings.ToLower(path.Ext(m.root)) {
	case ".dmg":
		f := m.entries[m.root]
		if len(m.entries) != 1 || f == nil || !f.Mode().IsRegular() || f.UncompressedSize64 == 0 {
			return nil, errors.New("Expected a nonempty DMG file.")
		}
	case ".app":
		if e := m.app(); e != nil {
			return nil, e
		}
	default:
		return nil, errors.New(
			"ZIP must contain exactly one top-level .app directory or .dmg file.",
		)
	}
	return m, nil
}

func (m *manifest) resolve(name string) (string, error) {
	pending := strings.Split(name, "/")
	var parts []string
	count := 0
	for len(pending) > 0 {
		p := pending[0]
		pending = pending[1:]
		switch p {
		case "", ".":
			continue
		case "..":
			if len(parts) <= 1 {
				return "", errors.New("ZIP symlink escapes the app bundle.")
			}
			parts = parts[:len(parts)-1]
		default:
			parts = append(parts, p)
			if target, ok := m.links[strings.Join(parts, "/")]; ok {
				count++
				if count > 40 {
					return "", errors.New("ZIP contains a cyclic or excessively deep symlink.")
				}
				parts = parts[:len(parts)-1]
				pending = append(strings.Split(target, "/"), pending...)
			}
		}
	}
	return strings.Join(parts, "/"), nil
}

func (m *manifest) app() error {
	contents := m.root + "/Contents/"
	f := m.entries[contents+"Info.plist"]
	if f == nil || !f.Mode().IsRegular() {
		return errors.New("ZIP must contain Contents/Info.plist.")
	}
	data, e := readSmall(f, 1<<20)
	if e != nil {
		return e
	}
	var info struct {
		Executable string `plist:"CFBundleExecutable"`
	}
	if _, e = plist.Unmarshal(data, &info); e != nil || !Filename(info.Executable) {
		return errors.New("Invalid Info.plist executable.")
	}
	target, e := m.resolve(contents + "MacOS/" + info.Executable)
	if e != nil {
		return e
	}
	bin := m.entries[target]
	if bin == nil || !bin.Mode().IsRegular() || bin.Mode().Perm()&0111 == 0 {
		return errors.New("App executable missing or lacks execute permission.")
	}
	return nil
}

func Validate(file string) error {
	f, e := os.Open(file) // #nosec G304 -- 文件来自上传暂存路径或本次生成的归档。
	if e != nil {
		return e
	}
	defer func() { _ = f.Close() }()
	var prefix [4]byte
	if _, err := io.ReadFull(
		f,
		prefix[:],
	); err != nil ||
		(string(prefix[:]) != "PK\x03\x04" && string(prefix[:]) != "PK\x05\x06") {
		return errors.New("Uploaded file is not a valid ZIP archive.")
	}
	info, e := f.Stat()
	if e != nil {
		return e
	}
	z, e := zip.NewReader(f, info.Size())
	if e != nil {
		return e
	}
	_, e = inspect(z)
	return e
}
