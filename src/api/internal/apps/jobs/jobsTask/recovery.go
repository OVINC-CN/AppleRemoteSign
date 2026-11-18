package jobsTask

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gorm.io/gorm"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/apple"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsSvc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

func Bootstrap(ctx context.Context) error {
	c := config.Config
	if db.DB() == nil || db.RDB() == nil || c.OIDC.ClientSecret == "" || c.CDN.SignKey == "" {
		return errors.New("MySQL, Redis, OIDC and CDN credentials are required.")
	}
	if e := db.Ping(ctx); e != nil {
		return errors.New("Database or coordinator unavailable.")
	}
	for _, m := range []any{&authModels.User{}, &authModels.Session{}, &authModels.Login{}, &accessModels.Application{}, &accessModels.Grant{}, &accessModels.Key{}, &jobsModels.Job{}} {
		q := db.DB().WithContext(ctx)
		stmt := &gorm.Statement{DB: q}
		if e := stmt.Parse(m); e != nil {
			return e
		}
		rows, e := q.Model(m).Select(stmt.Schema.DBNames).Limit(0).Rows()
		if e != nil {
			return errors.New("Database schema missing or incompatible.")
		}
		if e = rows.Close(); e != nil {
			return e
		}
	}
	for _, p := range []string{c.Signing.Binary, c.Signing.CertificateFile, c.Signing.PasswordFile, c.Signing.AppleKeyFile} {
		if !filepath.IsAbs(p) {
			return errors.New("Credential and tool paths must be absolute.")
		}
		f, e := os.Open(p) // #nosec G304 -- 仅检查管理员挂载的绝对路径。
		if e != nil {
			return e
		}
		i, e := f.Stat()
		_ = f.Close()
		if e != nil || !i.Mode().IsRegular() || p == c.Signing.Binary && i.Mode()&0111 == 0 {
			return errors.New("Invalid mounted tool or credentials.")
		}
	}
	b, e := os.ReadFile(c.Signing.PasswordFile)
	if e != nil || len(b) == 0 {
		return errors.New("Signing password file is empty.")
	}
	logger.AddSecrets(strings.TrimSuffix(strings.SplitN(string(b), "\n", 2)[0], "\r"))
	if e = apple.Load(); e != nil {
		return e
	}
	if e = os.MkdirAll(c.Signing.DataDir, 0700); e != nil {
		return e
	}
	return Scan(ctx)
}

func interrupted(q *gorm.DB) *gorm.DB {
	return q.Model(&jobsModels.Job{}).
		Where("status IN ? AND submission_id IS NULL", []string{"preparing", "processing"})
}

func Scan(ctx context.Context) error {
	var ids []string
	if e := interrupted(db.DB().WithContext(ctx)).Pluck("id", &ids).Error; e != nil {
		return e
	}
	for _, id := range ids {
		l, e := db.Acquire(ctx, id, false)
		if e != nil {
			return e
		}
		if l == nil {
			continue
		}
		e = recoverOne(l.Context(), id)
		end := l.Close()
		if e = errors.Join(e, end); e != nil {
			return e
		}
	}
	return nil
}

func recoverOne(ctx context.Context, id string) error {
	var changed bool
	e := db.DB().WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := db.Check(ctx); e != nil {
			return e
		}
		r := interrupted(
			tx,
		).Where("id=?", id).
			Updates(map[string]any{"status": "failed", "error_message": "Processing interrupted before a submission ID was saved. No automatic resubmission was attempted.", "updated_at": time.Now().UTC()})
		if r.Error != nil {
			return r.Error
		}
		changed = r.RowsAffected != 0
		return db.Check(ctx)
	})
	if e == nil && changed {
		logger.Logger.WithContext(ctx).Infof("job.interrupted_recovered job_id=%s", id)
		var j jobsModels.Job
		if db.DB().WithContext(ctx).Where("id=?", id).Take(&j).Error == nil {
			jobsSvc.Notify(ctx, &j, "failed")
		}
	}
	return e
}

func Run(ctx context.Context) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if e := Scan(ctx); e != nil {
				logger.Logger.WithContext(ctx).Warnf("job.recovery_unavailable: %v", e)
			}
		}
	}
}
