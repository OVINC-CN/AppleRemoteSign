package accessModels

import "time"

type Key struct {
	ID            string     `gorm:"column:id;primaryKey"  json:"id"`
	ApplicationID string     `gorm:"column:application_id" json:"-"`
	Name          string     `gorm:"column:name"           json:"name"`
	Prefix        string     `gorm:"column:prefix"         json:"prefix"`
	TokenHash     string     `gorm:"column:token_hash"     json:"-"`
	CreatedAt     time.Time  `gorm:"column:created_at"     json:"created_at"`
	LastUsedAt    *time.Time `gorm:"column:last_used_at"   json:"last_used_at"`
	RevokedAt     *time.Time `gorm:"column:revoked_at"     json:"revoked_at"`
}

func (Key) TableName() string { return "api_keys" }
