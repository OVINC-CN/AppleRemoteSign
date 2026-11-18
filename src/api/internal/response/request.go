package response

import (
	"encoding/json"
	"io"
	"net/http"

	"github.com/labstack/echo/v4"
)

func Body(c echo.Context, v any) error {
	d := json.NewDecoder(http.MaxBytesReader(c.Response(), c.Request().Body, 1<<20))
	d.DisallowUnknownFields()
	if d.Decode(v) != nil || d.Decode(&struct{}{}) != io.EOF {
		return Fail(422, "Invalid request body.")
	}
	return nil
}

func Result(c echo.Context, data any, err error) error {
	if err != nil {
		return err
	}
	return Send(c, 200, data)
}
