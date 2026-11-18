package jobsSvc

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/apple"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func clean(value any) any {
	switch v := value.(type) {
	case string:
		return logger.Clean(v)
	case []any:
		for i := range v {
			v[i] = clean(v[i])
		}
	case map[string]any:
		for k, x := range v {
			v[k] = clean(x)
		}
	}
	return value
}

func validateLog(v map[string]any, id *string) error {
	bad := errors.New("Invalid notarization details.")
	if id == nil || v["status"] != "Accepted" {
		return bad
	}
	job, ok := v["jobId"].(string)
	if !ok || !strings.EqualFold(job, *id) {
		return bad
	}
	for _, key := range []string{"archiveFilename", "sha256", "statusSummary", "uploadDate"} {
		s, ok := v[key].(string)
		if !ok || key == "archiveFilename" && s == "" {
			return bad
		}
	}
	for _, key := range []string{"statusCode", "logFormatVersion"} {
		n, ok := v[key].(json.Number)
		if !ok {
			return bad
		}
		if _, e := n.Int64(); e != nil {
			return bad
		}
	}
	for _, key := range []string{"ticketContents", "issues"} {
		if v[key] == nil {
			continue
		}
		rows, ok := v[key].([]any)
		if !ok {
			return bad
		}
		for _, row := range rows {
			r, ok := row.(map[string]any)
			if !ok {
				return bad
			}
			if key == "ticketContents" {
				for _, field := range []string{"path", "cdhash", "digestAlgorithm"} {
					s, ok := r[field].(string)
					if !ok || field == "path" && s == "" {
						return bad
					}
				}
				if r["arch"] != nil {
					if _, ok := r["arch"].(string); !ok {
						return bad
					}
				}
			} else {
				for _, f := range []string{"severity", "path", "architecture", "message", "docUrl"} {
					if r[f] != nil {
						if _, ok := r[f].(string); !ok {
							return bad
						}
					}
				}
				if n := r["code"]; n != nil {
					switch c := n.(type) {
					case string:
					case json.Number:
						if _, e := c.Int64(); e != nil {
							return bad
						}
					default:
						return bad
					}
				}
			}
		}
	}
	return nil
}

func cached(j *jobsModels.Job) (any, error) {
	var v map[string]any
	d := json.NewDecoder(strings.NewReader(*j.NotaryLog))
	d.UseNumber()
	if d.Decode(&v) != nil || v == nil {
		return nil, response.Fail(500, "Stored notarization details are invalid.")
	}
	if v["status"] != "Accepted" {
		return nil, nil
	}
	if validateLog(v, j.SubmissionID) != nil {
		return nil, response.Fail(500, "Stored notarization details are invalid.")
	}
	return v, nil
}

func fetchLog(ctx context.Context, j *jobsModels.Job) (any, error) {
	v, e := apple.Log(ctx, *j.SubmissionID)
	if e != nil {
		return nil, response.Fail(502, e.Error())
	}
	clean(v)
	if e = validateLog(v, j.SubmissionID); e != nil {
		return nil, response.Fail(502, "Apple returned invalid notarization details.")
	}
	b, e := json.Marshal(v)
	if e != nil {
		return nil, e
	}
	s := string(b)
	j.NotaryLog = &s
	if e = save(ctx, j); e != nil {
		j.NotaryLog = nil
		return nil, e
	}
	return v, nil
}

func Notary(ctx context.Context, p authModels.Principal, j *jobsModels.Job) (out any, err error) {
	if j.NotaryLog != nil {
		return cached(j)
	}
	if j.SubmissionID == nil {
		return nil, nil
	}
	err = locked(ctx, p, j, false, func(ctx context.Context) error {
		if j.NotaryLog != nil {
			out, err = cached(j)
			return err
		}
		if j.SubmissionID == nil {
			return nil
		}
		s, e := apple.Status(ctx, *j.SubmissionID)
		if e != nil {
			return response.Fail(502, e.Error())
		}
		if s != "Accepted" {
			return nil
		}
		out, err = fetchLog(ctx, j)
		return err
	})
	return
}
