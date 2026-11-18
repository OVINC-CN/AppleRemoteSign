package archive

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"testing"

	"howett.net/plist"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

type member struct {
	name string
	mode fs.FileMode
	data string
}

func build(t *testing.T, entries []member) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "sample.zip")
	f, e := os.Create(p)
	if e != nil {
		t.Fatal(e)
	}
	z := zip.NewWriter(f)
	for _, v := range entries {
		h := zip.FileHeader{Name: v.name, Method: zip.Deflate}
		h.SetMode(v.mode)
		w, e := z.CreateHeader(&h)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = w.Write([]byte(v.data)); e != nil {
			t.Fatal(e)
		}
	}
	if e = errors.Join(z.Close(), f.Close()); e != nil {
		t.Fatal(e)
	}
	return p
}

func bundle(t *testing.T, format int) []member {
	t.Helper()
	b, e := plist.Marshal(map[string]string{"CFBundleExecutable": "Demo"}, format)
	if e != nil {
		t.Fatal(e)
	}
	return []member{
		{"Demo.app/Contents/Info.plist", 0644, string(b)},
		{"Demo.app/Contents/", fs.ModeDir | 0751, ""},
		{"Demo.app/Contents/MacOS/Demo", 0755, "mach-o"},
		{"Demo.app/Contents/Frameworks/F/Versions/A/F", 0755, "framework"},
		{"Demo.app/Contents/Frameworks/F/Versions/Current", fs.ModeSymlink | 0777, "A"},
		{"Demo.app/Contents/Frameworks/F/F", fs.ModeSymlink | 0777, "Versions/Current/F"},
	}
}

func TestAppRoundTrip(t *testing.T) {
	for _, format := range []int{plist.XMLFormat, plist.BinaryFormat} {
		p := build(t, append(bundle(t, format), member{"__MACOSX/._Demo", 0644, "metadata"}))
		_, e := Transform(t.Context(), p, func(ctx context.Context, asset string) (string, error) {
			target, e := os.Readlink(filepath.Join(asset, "Contents/Frameworks/F/F"))
			if e != nil || target != "Versions/Current/F" {
				t.Fatal(target, e)
			}
			exe := filepath.Join(asset, "Contents/MacOS/Demo")
			i, e := os.Stat(exe)
			if e != nil || i.Mode().Perm()&0111 == 0 {
				t.Fatal("executable mode lost")
			}
			return "", os.WriteFile(exe, []byte("signed"), 0755)
		})
		if e != nil {
			t.Fatal(e)
		}
		if e = Validate(p); e != nil {
			t.Fatal(e)
		}
		z, e := zip.OpenReader(p)
		if e != nil {
			t.Fatal(e)
		}
		for _, f := range z.File {
			if f.Name == "Demo.app/Contents/" && f.Mode().Perm() != 0751 {
				t.Fatalf("directory mode: %v", f.Mode())
			}
			if f.Name == "__MACOSX/._Demo" {
				t.Fatal("metadata repacked")
			}
		}
		_ = z.Close()
	}
}

func TestInvalidArchive(t *testing.T) {
	base := bundle(t, plist.XMLFormat)
	for _, bad := range []member{{"../evil", 0600, "x"}, {"Demo.app/Contents/../x", 0600, "x"}, {"Demo.app/Contents/MacOS/demo", 0755, "x"}, {"Demo.app/Contents/Info.plist", 0644, "x"}, {"Demo.app/Contents/link", fs.ModeSymlink, "../../outside"}, {"Demo.app/Contents/link", fs.ModeSymlink, "link"}, {"Demo.app/Contents/link", fs.ModeSymlink, "nonexistent"}, {"Demo.app/Contents/Frameworks/F/F/evil", 0644, "x"}, {"Demo.app/pipe", fs.ModeNamedPipe, ""}, {"Other.dmg", 0600, "x"}} {
		p := build(t, append(base, bad))
		if Validate(p) == nil {
			t.Errorf("accepted %s -> %s", bad.name, bad.data)
		}
	}
	for _, entries := range [][]member{{{"empty.dmg", 0600, ""}}, {{"Foo.app/Contents/Info.plist", 0600, "garbage"}}, {{"archive.dmg/inside", 0600, "x"}}} {
		if Validate(build(t, entries)) == nil {
			t.Error("accepted invalid asset")
		}
	}
	cfg := &config.Config.Signing
	old := *cfg
	t.Cleanup(func() { *cfg = old })
	p := build(t, base)
	cfg.MaxArchiveEntries = 1
	if Validate(p) == nil {
		t.Fatal("entry limit")
	}
	cfg.MaxArchiveEntries = 100
	cfg.MaxExtractedBytes = 4
	if Validate(p) == nil {
		t.Fatal("size limit")
	}
}

func TestTransformFailurePreservesArchive(t *testing.T) {
	p := build(t, []member{{"Foo.dmg", 0644, "original"}})
	original, _ := os.ReadFile(p)
	_, e := Transform(
		t.Context(),
		p,
		func(context.Context, string) (string, error) { return "", errors.New("unavailable") },
	)
	b, _ := os.ReadFile(p)
	files, _ := os.ReadDir(filepath.Dir(p))
	if e == nil || !bytes.Equal(b, original) || len(files) != 1 {
		t.Fatal("failed transform modified original")
	}
}
