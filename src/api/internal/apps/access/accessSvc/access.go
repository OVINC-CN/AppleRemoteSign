package accessSvc

import (
	"context"
	"errors"
	"slices"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/response"
)

func Allowed(ctx context.Context, p authModels.Principal) *gorm.DB {
	q := db.DB().WithContext(ctx).Model(&accessModels.Application{})
	if p.Admin {
		return q
	}
	q = q.Where("applications.enabled=?", true)
	if p.KeyID != "" {
		return q.Where("applications.id=?", p.ApplicationID)
	}
	return q.Joins("JOIN application_grants g ON g.application_id=applications.id").
		Where("g.user_id=?", p.UserID)
}

func Require(
	ctx context.Context,
	p authModels.Principal,
	id string,
	sign bool,
) (accessModels.Application, error) {
	var a accessModels.Application
	q := Allowed(ctx, p).Where("applications.id=?", id)
	if sign {
		q = q.Where("applications.enabled=?", true)
	}
	err := q.Take(&a).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		err = response.Fail(404, "Application not found or unavailable.")
	}
	return a, err
}

func List(ctx context.Context, p authModels.Principal) ([]accessModels.Application, error) {
	a := []accessModels.Application{}
	err := Allowed(ctx, p).Order("applications.created_at DESC").Find(&a).Error
	return a, err
}

func Create(ctx context.Context, id, name string) (accessModels.Application, error) {
	a := accessModels.Application{ID: uuid.NewString(), Identifier: id, Name: name, Enabled: true}
	err := db.DB().WithContext(ctx).Create(&a).Error
	if errors.Is(err, gorm.ErrDuplicatedKey) {
		err = response.Fail(409, "Identifier already exists.")
	}
	return a, err
}

func Update(
	ctx context.Context,
	id string,
	name *string,
	enabled *bool,
) (accessModels.Application, error) {
	var a accessModels.Application
	q := db.DB().WithContext(ctx)
	fields := map[string]any{}
	if name != nil {
		fields["name"] = *name
	}
	if enabled != nil {
		fields["enabled"] = *enabled
	}
	err := q.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&a).Where("id=?", id).Updates(fields).Error; err != nil {
			return err
		}
		return tx.Where("id=?", id).Take(&a).Error
	})
	return a, err
}

func Grants(ctx context.Context, id string) ([]string, error) {
	out := []string{}
	err := db.DB().
		WithContext(ctx).
		Table("application_grants g").
		Joins("JOIN users u ON u.id=g.user_id").
		Where("g.application_id=?", id).
		Order("u.username").
		Pluck("u.username", &out).
		Error
	return out, err
}

func Replace(ctx context.Context, id string, names []string) error {
	slices.Sort(names)
	return db.DB().WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("id=?", id).
			Take(&accessModels.Application{}).
			Error; err != nil {
			return err
		}
		if err := tx.Where("application_id=?", id).Delete(&accessModels.Grant{}).Error; err != nil {
			return err
		}
		for _, name := range slices.Compact(names) {
			var u authModels.User
			if err := tx.Where("username=?", name).
				Attrs(authModels.User{ID: uuid.NewString(), Username: name}).
				FirstOrCreate(&u).
				Error; err != nil {
				return err
			}
			if err := tx.Create(
				&accessModels.Grant{ApplicationID: id, UserID: u.ID},
			).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

func Keys(ctx context.Context, id string) ([]accessModels.Key, error) {
	out := []accessModels.Key{}
	err := db.DB().
		WithContext(ctx).
		Where("application_id=?", id).
		Order("created_at DESC").
		Find(&out).
		Error
	return out, err
}

func CreateKey(ctx context.Context, id, name string) (accessModels.Key, string, error) {
	token := "ars_" + authUtils.Token()
	k := accessModels.Key{
		ID:            uuid.NewString(),
		ApplicationID: id,
		Name:          name,
		Prefix:        token[:12],
		TokenHash:     authUtils.Digest(token),
	}
	err := db.DB().WithContext(ctx).Create(&k).Error
	return k, token, err
}

func Revoke(ctx context.Context, id, key string) error {
	var k accessModels.Key
	q := db.DB().WithContext(ctx).Where("id=? AND application_id=?", key, id)
	if err := q.Take(&k).Error; err != nil {
		return err
	}
	return db.DB().
		WithContext(ctx).
		Model(&k).
		Where("application_id=? AND revoked_at IS NULL", id).
		Update("revoked_at", time.Now().UTC()).
		Error
}
