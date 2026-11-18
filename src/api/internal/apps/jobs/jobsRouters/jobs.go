package jobsRouters

import (
	"errors"
	"io"
	"math"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/labstack/echo/v4"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authRouters"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func job(c echo.Context) *jobsModels.Job { return c.Get("job").(*jobsModels.Job) }
func List(c echo.Context) error {
	parse := func(key string, def int) (int, error) {
		v, ok := c.QueryParams()[key]
		if !ok {
			return def, nil
		}
		return strconv.Atoi(v[len(v)-1])
	}
	page, e := parse("page", 1)
	size, e2 := parse("page_size", 20)
	status := c.QueryParam("status")
	app := c.QueryParam("application_id")
	name := c.QueryParam("filename")
	if e != nil || e2 != nil || page < 1 || size < 1 || size > 100 || page > math.MaxInt/size ||
		!slices.Contains([]string{"", "preparing", "processing", "completed", "failed"}, status) ||
		utf8.RuneCountInString(name) > 255 {
		return response.Fail(422, "Invalid history filter or pagination.")
	}
	if app != "" {
		id, e := uuid.Parse(app)
		if e != nil {
			return response.Fail(422, "Invalid application ID.")
		}
		app = id.String()
	}
	v, e := jobsSvc.History(
		c.Request().Context(),
		authRouters.Actor(c),
		page,
		size,
		app,
		status,
		name,
	)
	return response.Result(c, v, e)
}

func Detail(c echo.Context) error {
	v, e := jobsSvc.Detail(c.Request().Context(), job(c))
	return response.Result(c, v, e)
}

func Rename(c echo.Context) error {
	var b struct {
		DisplayName string `json:"display_name"`
	}
	if e := response.Body(c, &b); e != nil {
		return e
	}
	name := strings.TrimSpace(b.DisplayName)
	if name == "" || utf8.RuneCountInString(name) > 255 ||
		strings.IndexFunc(name, func(r rune) bool { return r < 32 || r == 127 }) >= 0 {
		return response.Fail(422, "Invalid display name.")
	}
	v, e := jobsSvc.Rename(c.Request().Context(), authRouters.Actor(c), job(c), name)
	return response.Result(c, v, e)
}

func Submit(c echo.Context) error {
	r := c.Request()
	p := authRouters.Actor(c)
	app := p.ApplicationID
	if app == "" {
		app = c.Param("application_id")
	}
	r.Body = http.MaxBytesReader(c.Response(), r.Body, config.Config.Signing.MaxUploadBytes+(1<<20))
	m, e := r.MultipartReader()
	if e != nil {
		return response.Fail(422, "Expected a ZIP file.")
	}
	for {
		part, e := m.NextPart()
		if errors.Is(e, io.EOF) || e != nil {
			return response.Fail(422, "Missing multipart file.")
		}
		if part.FormName() == "file" && part.FileName() != "" {
			defer func() { _ = part.Close() }()
			v, e := jobsSvc.Submit(r.Context(), p, app, part.FileName(), part)
			return response.Result(c, v, e)
		}
		if e := part.Close(); e != nil {
			return response.Fail(422, "Invalid multipart request.")
		}
	}
}

func Query(c echo.Context) error {
	p := authRouters.Actor(c)
	v, e := jobsSvc.Query(c.Request().Context(), p, job(c), p.KeyID != "")
	return response.Result(c, v, e)
}

func Notary(c echo.Context) error {
	v, e := jobsSvc.Notary(c.Request().Context(), authRouters.Actor(c), job(c))
	return response.Result(c, v, e)
}

func Artifact(c echo.Context) error {
	p := authRouters.Actor(c)
	url, e := jobsSvc.Download(c.Request().Context(), p, job(c))
	if e != nil {
		return e
	}
	if p.KeyID != "" {
		return c.Redirect(302, url)
	}
	return response.Send(c, 200, echo.Map{"url": url})
}
