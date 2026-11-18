package jobsSvc

import (
	"context"
	"errors"
	"strings"

	"gorm.io/gorm"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/wecom"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func Require(ctx context.Context, p authModels.Principal, id string) (jobsModels.Job, error) {
	var j jobsModels.Job
	e := db.DB().
		WithContext(ctx).
		Where("id=? AND application_id IN (?)", id, accessSvc.Allowed(ctx, p).Select("applications.id")).
		Take(&j).
		Error
	if errors.Is(e, gorm.ErrRecordNotFound) {
		e = response.Fail(404, "Job not found.")
	}
	return j, e
}

func data(j *jobsModels.Job) jobsModels.Data {
	return jobsModels.Data{ID: j.ID, Status: j.Status, SubmissionID: j.SubmissionID}
}

func view(ctx context.Context, j *jobsModels.Job) (jobsModels.View, error) {
	v := jobsModels.View{
		Data:          data(j),
		Filename:      j.Filename,
		DisplayName:   j.Filename,
		Source:        "Unknown",
		SizeBytes:     j.SizeBytes,
		ErrorMessage:  j.ErrorMessage,
		CreatedAt:     j.CreatedAt,
		UpdatedAt:     j.UpdatedAt,
		LastCheckedAt: j.LastCheckedAt,
	}
	if j.DisplayName != nil && *j.DisplayName != "" {
		v.DisplayName = *j.DisplayName
	}
	q := db.DB().WithContext(ctx)
	if e := q.Where("id=?", j.ApplicationID).Take(&v.Application).Error; e != nil {
		return v, e
	}
	if j.SubmittedByUserID != nil {
		var u authModels.User
		if e := q.Where("id=?", *j.SubmittedByUserID).Take(&u).Error; e != nil {
			return v, e
		}
		v.Source = "用户 · " + u.Username
	} else if j.SubmittedByKeyID != nil {
		var k accessModels.Key
		if e := q.Where("id=?", *j.SubmittedByKeyID).Take(&k).Error; e != nil {
			return v, e
		}
		v.Source = "OpenAPI · " + k.Name
	}
	return v, nil
}

func History(
	ctx context.Context,
	p authModels.Principal,
	page, size int,
	app, status, name string,
) (jobsModels.Page, error) {
	out := jobsModels.Page{Items: []jobsModels.View{}, Page: page, PageSize: size}
	q := db.DB().
		WithContext(ctx).
		Model(&jobsModels.Job{}).
		Where("application_id IN (?)", accessSvc.Allowed(ctx, p).Select("applications.id"))
	if app != "" {
		if _, e := accessSvc.Require(ctx, p, app, false); e != nil {
			return out, e
		}
		q = q.Where("application_id=?", app)
	}
	if status != "" {
		q = q.Where("status=?", status)
	}
	if name != "" {
		pattern := "%" + strings.NewReplacer("!", "!!", "%", "!%", "_", "!_").Replace(name) + "%"
		q = q.Where(
			"(LOWER(filename) LIKE LOWER(?) ESCAPE '!' OR LOWER(display_name) LIKE LOWER(?) ESCAPE '!')",
			pattern,
			pattern,
		)
	}
	if e := q.Count(&out.Total).Error; e != nil {
		return out, e
	}
	var rows []jobsModels.Job
	if e := q.Order("created_at DESC,id DESC").
		Offset((page - 1) * size).
		Limit(size).
		Find(&rows).
		Error; e != nil {
		return out, e
	}
	for i := range rows {
		v, e := view(ctx, &rows[i])
		if e != nil {
			return out, e
		}
		out.Items = append(out.Items, v)
	}
	return out, nil
}

func Detail(ctx context.Context, j *jobsModels.Job) (jobsModels.Detail, error) {
	v, e := view(ctx, j)
	return jobsModels.Detail{
		View:           v,
		Diagnostics:    j.Diagnostics,
		NotaryLog:      j.NotaryLog,
		OriginalSHA256: j.OriginalSHA256,
		ArtifactSHA256: j.ArtifactSHA256,
	}, e
}

func Rename(
	ctx context.Context,
	p authModels.Principal,
	j *jobsModels.Job,
	name string,
) (v jobsModels.View, err error) {
	err = locked(ctx, p, j, false, func(ctx context.Context) error {
		j.DisplayName = &name
		if e := save(ctx, j); e != nil {
			return e
		}
		v, err = view(ctx, j)
		return err
	})
	return
}

func Notify(ctx context.Context, j *jobsModels.Job, event string) {
	if config.Config.Wecom.WebhookURL == "" {
		return
	}
	v, e := view(ctx, j)
	if e != nil {
		logger.Logger.Warnf("wecom.notification_failed job_id=%s", j.ID)
		return
	}
	if db.Check(ctx) == nil {
		wecom.Notify(ctx, event, j.Filename, v.Application.Name, j.ID, v.Source)
	}
}
