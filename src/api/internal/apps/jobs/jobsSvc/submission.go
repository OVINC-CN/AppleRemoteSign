package jobsSvc

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/rcodesign"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
	"github.com/OVINC-CN/AppleSign/src/api/internal/storage"
	"github.com/OVINC-CN/AppleSign/src/api/internal/storage/archive"
)

func issue(code int, msg string, j *jobsModels.Job) error {
	e := &response.Error{Status: code, Message: msg}
	if j != nil && j.ID != "" {
		e.Data = data(j)
	}
	return e
}

func coordination(e error, j *jobsModels.Job) error {
	if errors.Is(e, db.ErrLease) {
		return issue(503, db.ErrLease.Error(), j)
	}
	return e
}

func save(ctx context.Context, j *jobsModels.Job) error {
	return db.DB().WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := db.Check(ctx); e != nil {
			return e
		}
		j.UpdatedAt = time.Now().UTC()
		r := tx.Model(j).Select("*").Updates(j)
		if r.Error != nil {
			return issue(500, "Database update failed; query this task again.", j)
		}
		return db.Check(ctx)
	})
}

func locked(
	ctx context.Context,
	p authModels.Principal,
	j *jobsModels.Job,
	wait bool,
	op func(context.Context) error,
) (err error) {
	l, e := db.Acquire(ctx, j.ID, wait)
	if e != nil {
		return coordination(e, j)
	}
	if l == nil {
		return issue(
			409,
			"This job is already being processed or refreshed. Please try again shortly.",
			j,
		)
	}
	defer func() {
		cause := context.Cause(l.Context())
		end := l.Close()
		if errors.Is(cause, db.ErrLease) {
			err = db.ErrLease
		} else if end != nil {
			err = end
		}
		err = coordination(err, j)
	}()
	fresh, e := Require(l.Context(), p, j.ID)
	if e != nil {
		return e
	}
	*j = fresh
	return op(l.Context())
}

func result(j *jobsModels.Job) error {
	if j.Status == "failed" {
		return issue(500, j.ErrorMessage, j)
	}
	return nil
}

func directory(j *jobsModels.Job) string {
	return path.Join(j.ApplicationID, j.CreatedAt.UTC().Format("2006/01/02"), j.ID)
}

func record(j *jobsModels.Job, label, text string) {
	j.Diagnostics += "\n[" + label + "]\n" + logger.Clean(text)
	if len(j.Diagnostics) > 65536 {
		j.Diagnostics = strings.ToValidUTF8(j.Diagnostics[len(j.Diagnostics)-65536:], "")
	}
}

func fail(ctx context.Context, j *jobsModels.Job, stage string, err error, notify bool) error {
	var dbErr *response.Error
	if errors.As(err, &dbErr) {
		return err
	}
	if e := db.Check(ctx); e != nil {
		return e
	}
	j.Status = "failed"
	j.ErrorMessage = logger.Clean(stage + ": " + err.Error())
	if e := save(ctx, j); e != nil {
		return e
	}
	logger.Logger.WithContext(ctx).Errorf("job.failed job_id=%s stage=%s", j.ID, stage)
	if notify {
		Notify(ctx, j, "failed")
	}
	return result(j)
}

var version = regexp.MustCompile(`\d+\.\d+\.\d+`)

func Submit(
	ctx context.Context,
	p authModels.Principal,
	appID, name string,
	src io.Reader,
) (out jobsModels.Data, err error) {
	if _, e := accessSvc.Require(ctx, p, appID, true); e != nil {
		return out, e
	}
	name = path.Base(strings.ReplaceAll(name, "\\", "/"))
	if name == "." || name == ".." || !utf8.ValidString(name) ||
		strings.IndexFunc(name, func(r rune) bool { return r < 32 || r == 127 }) >= 0 {
		return out, response.Fail(422, "Filename is empty or contains invalid characters.")
	}
	if len(name) > 255 {
		return out, response.Fail(422, "Filename exceeds 255 UTF-8 bytes.")
	}
	if !strings.EqualFold(path.Ext(name), ".zip") {
		return out, response.Fail(
			422,
			"Only ZIP uploads are supported. Package one .app directory or one .dmg file into a .zip archive (for example, using ditto -c -k --keepParent), then upload it.",
		)
	}
	if !version.MatchString(name) {
		return out, response.Fail(
			422,
			"ZIP filename must contain a version number (X.Y.Z), for example product-v1.2.3.zip.",
		)
	}
	tmp, hash, n, e := storage.Spool(ctx, src)
	if e != nil {
		var limit *http.MaxBytesError
		switch {
		case errors.Is(e, storage.ErrEmptyUpload), errors.Is(e, storage.ErrLargeUpload):
			return out, response.Fail(422, e.Error())
		case errors.As(e, &limit):
			return out, response.Fail(422, storage.ErrLargeUpload.Error())
		case errors.Is(e, io.ErrUnexpectedEOF):
			return out, response.Fail(422, "Incomplete multipart upload.")
		default:
			return out, e
		}
	}
	defer func() { _ = os.Remove(tmp) }()
	// 先校验归档，再获取内容锁，避免无效输入创建任务。
	if e = archive.Validate(tmp); e != nil {
		return out, response.Fail(422, e.Error())
	}
	content, e := db.Acquire(ctx, "sha256:"+appID+":"+hash, true)
	if e != nil {
		return out, coordination(e, nil)
	}
	var j jobsModels.Job
	defer func() {
		if content != nil {
			end := content.Close()
			if end != nil {
				err = end
			}
		}
		err = coordination(err, &j)
	}()
	q := db.DB().WithContext(content.Context())
	e = q.Where("application_id=? AND original_sha256=? AND HEX(filename)=HEX(?) AND status IN ?", appID, hash, name, []string{"preparing", "processing", "completed"}).
		Order("created_at DESC,id DESC").
		Take(&j).
		Error
	if e == nil {
		return data(&j), nil
	}
	if !errors.Is(e, gorm.ErrRecordNotFound) {
		return out, e
	}
	// 在数据库提交前取得独立的任务租约，恢复扫描不能抢走刚创建的任务。
	id := uuid.NewString()
	lease, e := db.Acquire(ctx, id, true)
	if e != nil {
		return out, e
	}
	defer func() {
		cause := context.Cause(lease.Context())
		end := lease.Close()
		if errors.Is(cause, db.ErrLease) {
			err = db.ErrLease
		} else if end != nil {
			err = end
		}
	}()
	ctx = lease.Context()
	j = jobsModels.Job{
		ID:             id,
		ApplicationID:  appID,
		Filename:       name,
		Status:         "preparing",
		OriginalSHA256: &hash,
	}
	if p.KeyID != "" {
		j.SubmittedByKeyID = &p.KeyID
	} else {
		j.SubmittedByUserID = &p.UserID
	}
	e = q.Transaction(func(tx *gorm.DB) error {
		if e := db.Check(ctx); e != nil {
			return e
		}
		if e := db.Check(content.Context()); e != nil {
			return e
		}
		if e := tx.Create(&j).Error; e != nil {
			return e
		}
		return errors.Join(db.Check(ctx), db.Check(content.Context()))
	})
	if e != nil {
		j.ID = ""
		return out, e
	}
	// 此时重复上传可以复用任务，签名和提交继续持有任务租约。
	e = content.Close()
	content = nil
	if e != nil {
		return out, e
	}
	logger.Logger.WithContext(ctx).Infof("job.created job_id=%s application_id=%s", j.ID, appID)
	Notify(ctx, &j, "submitted")
	e = process(ctx, &j, tmp, n)
	return data(&j), e
}

func process(ctx context.Context, j *jobsModels.Job, tmp string, n int64) error {
	dir := directory(j)
	orig := path.Join(dir, "original", j.Filename)
	source, e := storage.Copy(ctx, tmp, orig)
	if e != nil {
		return fail(ctx, j, "prepare", e, true)
	}
	j.OriginalPath = &orig
	j.SizeBytes = &n
	j.Status = "processing"
	if e = save(ctx, j); e != nil {
		return e
	}
	signed := path.Join(dir, "submitted", j.Filename)
	p, e := storage.Copy(ctx, source, signed)
	if e != nil {
		return fail(ctx, j, "sign", e, true)
	}
	out, e := archive.Transform(ctx, p, rcodesign.Sign)
	record(j, "sign", out)
	if e != nil {
		return fail(ctx, j, "sign", e, true)
	}
	hash, e := storage.Hash(ctx, p)
	if e != nil {
		return fail(ctx, j, "sign", e, true)
	}
	j.SubmittedPath = &signed
	j.SubmittedSHA256 = &hash
	if e = save(ctx, j); e != nil {
		return e
	}
	logger.Logger.WithContext(ctx).Infof("job.signed job_id=%s", j.ID)
	out, e = rcodesign.Submit(ctx, p, func(id string) error {
		j.SubmissionID = &id
		if err := save(ctx, j); err != nil {
			return err
		}
		logger.Logger.WithContext(ctx).
			Infof("job.submission_id_saved job_id=%s submission_id=%s", j.ID, id)
		return nil
	})
	record(j, "submit", out)
	if e != nil {
		return fail(ctx, j, "submit", e, true)
	}
	return save(ctx, j)
}
