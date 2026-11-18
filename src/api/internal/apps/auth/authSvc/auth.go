package authSvc

import (
	"context"
	"errors"
	"slices"
	"time"

	"gorm.io/gorm"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func Session(ctx context.Context, token string) (authModels.Principal, error) {
	var p authModels.Principal
	err := db.DB().
		WithContext(ctx).
		Table("web_sessions s").
		Select("s.token_hash AS session_hash, s.csrf_token, s.user_id, u.username").
		Joins("JOIN users u ON u.id=s.user_id").
		Where("s.token_hash = ? AND s.expires_at > ?", authUtils.Digest(token), time.Now().UTC()).
		Take(&p).
		Error
	if errors.Is(err, gorm.ErrRecordNotFound) || err == nil && !config.Config.Allowed(p.Username) {
		return p, response.Fail(401, "Please sign in.")
	}
	p.Admin = slices.Contains(config.Config.OIDC.AdminUsernames, p.Username)
	return p, err
}

func Key(ctx context.Context, token string) (authModels.Principal, error) {
	var p authModels.Principal
	err := db.DB().WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		err := tx.Table("api_keys k").
			Select("k.id AS key_id, k.application_id").
			Joins("JOIN applications a ON a.id=k.application_id").
			Where("k.token_hash=? AND k.revoked_at IS NULL AND a.enabled=?", authUtils.Digest(token), true).
			Take(&p).
			Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return response.Fail(401, "Invalid or missing Bearer token.")
		}
		if err != nil {
			return err
		}
		return tx.Table("api_keys").
			Where("id=?", p.KeyID).
			Update("last_used_at", time.Now().UTC()).
			Error
	})
	return p, err
}
