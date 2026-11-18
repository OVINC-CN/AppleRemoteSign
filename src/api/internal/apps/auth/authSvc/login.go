package authSvc

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/oidc"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
)

func Start(ctx context.Context) (redirect, state, browser string, err error) {
	state, browser = authUtils.Token(), authUtils.Token()
	redirect, t, err := oidc.Authorize(ctx, state)
	if err != nil {
		return "", "", "", err
	}
	data, err := json.Marshal(t)
	if err != nil {
		return "", "", "", err
	}
	now := time.Now().UTC()
	err = db.DB().WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if e := tx.Where("expires_at <= ?", now).Delete(&authModels.Login{}).Error; e != nil {
			return e
		}
		return tx.Create(
			&authModels.Login{
				StateHash:   authUtils.Digest(state),
				BrowserHash: authUtils.Digest(browser),
				Data:        string(data),
				ExpiresAt:   now.Add(config.Config.OIDC.LoginTTL),
			},
		).Error
	})
	return
}

func Finish(ctx context.Context, state, browser, code, old string) (string, string) {
	if state == "" || len(state) > 256 {
		return "", "login_expired"
	}
	if browser == "" || len(browser) > 256 {
		return "", "login_cookie_missing"
	}
	var t oidc.Transaction
	reason := "login_failed"
	conn := db.DB().WithContext(ctx)
	err := conn.Transaction(func(tx *gorm.DB) error {
		var row authModels.Login
		err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("state_hash=? AND expires_at>?", authUtils.Digest(state), time.Now().UTC()).
			Take(&row).
			Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			reason = "login_expired"
		}
		if err != nil {
			return err
		}
		if !authUtils.Equal(row.BrowserHash, authUtils.Digest(browser)) {
			reason = "login_cookie_mismatch"
			return errors.New(reason)
		}
		if e := json.Unmarshal([]byte(row.Data), &t); e != nil {
			return e
		}
		// 接续 Python 已签发的 Authlib 登录事务。
		if t.Verifier == "" {
			var prior map[string]struct {
				Data oidc.Transaction `json:"data"`
			}
			if err := json.Unmarshal([]byte(row.Data), &prior); err != nil {
				return err
			}
			t = prior["_state_oidc_"+state].Data
		}
		return tx.Delete(&row).Error
	})
	if err != nil {
		return "", reason
	}
	if code == "" || t.Verifier == "" || t.Nonce == "" {
		return "", "login_failed"
	}
	name, sub, err := oidc.Identity(ctx, code, t)
	if err != nil {
		return "", "login_failed"
	}
	if !config.Config.Allowed(name) {
		return "", "access_denied"
	}
	token := authUtils.Token()
	issuer := config.Config.OIDC.IssuerURL
	err = conn.Transaction(func(tx *gorm.DB) error {
		var u authModels.User
		err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("username=?", name).
			Take(&u).
			Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			u = authModels.User{
				ID:       uuid.NewString(),
				Username: name,
				Issuer:   &issuer,
				Subject:  &sub,
			}
			err = tx.Create(&u).Error
		} else if err == nil {
			if u.Subject != nil && (*u.Subject != sub || u.Issuer == nil || *u.Issuer != issuer) {
				reason = "access_denied"
				return errors.New(reason)
			}
			err = tx.Model(&u).Updates(map[string]any{"issuer": issuer, "subject": sub}).Error
		}
		if err != nil {
			if errors.Is(err, gorm.ErrDuplicatedKey) {
				reason = "access_denied"
			}
			return err
		}
		now := time.Now().UTC()
		if err := tx.Where("expires_at<=? OR token_hash=?", now, authUtils.Digest(old)).
			Delete(&authModels.Session{}).
			Error; err != nil {
			return err
		}
		return tx.Create(
			&authModels.Session{
				TokenHash: authUtils.Digest(token),
				UserID:    u.ID,
				CSRFToken: authUtils.Token(),
				ExpiresAt: now.Add(config.Config.Web.SessionTTL),
			},
		).Error
	})
	if err != nil {
		return "", reason
	}
	return token, ""
}

func Logout(ctx context.Context, hash string) error {
	return db.DB().WithContext(ctx).Where("token_hash=?", hash).Delete(&authModels.Session{}).Error
}
