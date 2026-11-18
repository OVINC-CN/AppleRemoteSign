package response

import (
	"errors"
	"net/http"

	"github.com/labstack/echo/v4"
	"gorm.io/gorm"
)

type Envelope struct {
	Data         any    `json:"data"`
	ErrorMessage string `json:"error_message"`
}
type Error struct {
	Status  int
	Message string
	Data    any
}

func (e *Error) Error() string                      { return e.Message }
func Fail(status int, msg string) error             { return &Error{Status: status, Message: msg} }
func Send(c echo.Context, code int, data any) error { return c.JSON(code, Envelope{Data: data}) }
func Handler(err error, c echo.Context) {
	if c.Response().Committed {
		return
	}
	var failure *Error
	var httpErr *echo.HTTPError
	switch {
	case errors.As(err, &failure):
	case errors.Is(err, gorm.ErrRecordNotFound):
		failure = &Error{Status: 404, Message: "Resource not found."}
	case errors.As(err, &httpErr):
		failure = &Error{Status: httpErr.Code, Message: http.StatusText(httpErr.Code)}
	default:
		failure = &Error{Status: 500, Message: "Internal service or database error."}
	}
	_ = c.JSON(failure.Status, Envelope{failure.Data, failure.Message})
}
