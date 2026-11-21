package jobsSvc

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path"
	"strings"
	"time"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/apple"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/cdn"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/cos"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/rcodesign"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/storage"
	"github.com/OVINC-CN/AppleSign/src/api/internal/storage/archive"
)

func artifact(ctx context.Context, j *jobsModels.Job) (string, error) {
	if j.ArtifactPath != nil && j.ArtifactSHA256 != nil {
		f, e := storage.Resolve(*j.ArtifactPath)
		if e == nil {
			sum, e := storage.Hash(ctx, f)
			if e == nil && sum == *j.ArtifactSHA256 {
				return f, nil
			}
		}
	}
	if j.SubmittedPath == nil {
		return "", errors.New("Signed submission file is missing.")
	}
	src, e := storage.Resolve(*j.SubmittedPath)
	if e != nil {
		return "", e
	}
	dir := directory(j)
	work, e := storage.Copy(ctx, src, path.Join(dir, "working", j.Filename))
	if e != nil {
		return "", e
	}
	out, e := archive.Transform(ctx, work, rcodesign.Staple)
	record(j, "staple", out)
	if e != nil {
		return "", e
	}
	rel := path.Join(dir, "artifact", j.Filename)
	p, e := storage.Copy(ctx, work, rel)
	if e != nil {
		return "", e
	}
	sum, e := storage.Hash(ctx, p)
	if e != nil {
		return "", e
	}
	j.ArtifactPath = &rel
	j.ArtifactSHA256 = &sum
	return p, save(ctx, j)
}

func deliver(ctx context.Context, j *jobsModels.Job, key string) (string, error) {
	p, e := artifact(ctx, j)
	if e != nil {
		return "staple", e
	}
	if e = db.Check(ctx); e != nil {
		return "upload", e
	}
	f, e := os.Open(p) // #nosec G304 -- artifact 已验证共享卷路径。
	if e != nil {
		return "upload", e
	}
	defer func() { _ = f.Close() }()
	return "upload", cos.Upload(ctx, f, key, j.Filename)
}

func Query(
	ctx context.Context,
	p authModels.Principal,
	j *jobsModels.Job,
	wait bool,
) (jobsModels.Data, error) {
	if j.SubmissionID == nil {
		return data(j), result(j)
	}
	e := locked(ctx, p, j, wait, func(ctx context.Context) error {
		status, e := apple.Status(ctx, *j.SubmissionID)
		if e != nil {
			record(j, "query", e.Error())
			if e2 := save(ctx, j); e2 != nil {
				return e2
			}
			return issue(502, e.Error(), j)
		}
		if e = db.Check(ctx); e != nil {
			return e
		}
		now := time.Now().UTC()
		j.LastCheckedAt = &now
		previous := j.Status
		completed := previous == "completed" && j.ArtifactS3Key != nil
		switch status {
		case "Accepted":
			if !completed {
				j.Status = "processing"
				if e = save(ctx, j); e != nil {
					return e
				}
				key := config.Config.COS.Prefix + path.Join(directory(j), j.Filename)
				if j.ArtifactS3Key != nil {
					key = *j.ArtifactS3Key
				}
				stage, e := deliver(ctx, j, key)
				if e != nil {
					return fail(ctx, j, stage, e, previous != "failed")
				}
				j.ArtifactS3Key = &key
				j.Status = "completed"
				j.ErrorMessage = ""
			}
		case "In Progress":
			if j.Status != "failed" && j.Status != "completed" {
				j.Status = "processing"
				j.ErrorMessage = ""
			}
		case "Rejected", "Invalid":
			j.Status = "failed"
			j.ErrorMessage = "Apple notarization " + status + "."
			if j.NotaryLog == nil {
				log, e := apple.Log(ctx, *j.SubmissionID)
				if e != nil {
					record(j, "notary-log", e.Error())
				} else {
					clean(log)
					b, _ := json.Marshal(log)
					s := string(b)
					j.NotaryLog = &s
				}
			}
			if j.NotaryLog != nil {
				var log map[string]any
				if json.Unmarshal([]byte(*j.NotaryLog), &log) == nil {
					summary, _ := log["statusSummary"].(string)
					issues, _ := log["issues"].([]any)
					for _, v := range issues[:min(3, len(issues))] {
						i, _ := v.(map[string]any)
						msg, _ := i["message"].(string)
						summary += " " + msg
					}
					j.ErrorMessage = strings.TrimSpace(j.ErrorMessage + " " + logger.Clean(summary))
				}
			}
		}
		if e = save(ctx, j); e != nil {
			return e
		}
		if status == "Accepted" && j.NotaryLog == nil {
			if _, e = fetchLog(ctx, j); e != nil {
				logger.Logger.WithContext(ctx).
					Warnf("job.notary_details.prefetch_failed job_id=%s", j.ID)
			}
		}
		if !completed && j.Status == "completed" {
			Notify(ctx, j, "completed")
		}
		if previous != "failed" && j.Status == "failed" {
			Notify(ctx, j, "failed")
		}
		return result(j)
	})
	return data(j), e
}

func Download(
	ctx context.Context,
	p authModels.Principal,
	j *jobsModels.Job,
) (url string, err error) {
	err = locked(ctx, p, j, true, func(ctx context.Context) error {
		if e := result(j); e != nil {
			return e
		}
		if j.Status != "completed" {
			return issue(409, "Artifact is not ready; query the job first.", j)
		}
		if j.ArtifactS3Key == nil {
			return issue(500, "Artifact is unavailable; query this job to retry delivery.", j)
		}
		key := *j.ArtifactS3Key
		ok, e := cos.Exists(ctx, key)
		if e == nil && !ok {
			_, e = deliver(ctx, j, key)
		}
		if e != nil {
			return issue(502, logger.Clean(e.Error()), j)
		}
		if e = db.Check(ctx); e != nil {
			return e
		}
		url = cdn.Sign(key)
		return nil
	})
	return
}
