package authModels

import "time"

type Session struct {
	TokenHash string    `gorm:"column:token_hash;primaryKey" json:"-"`
	UserID    string    `gorm:"column:user_id"               json:"-"`
	CSRFToken string    `gorm:"column:csrf_token"            json:"-"`
	ExpiresAt time.Time `gorm:"column:expires_at"            json:"-"`
}

func (Session) TableName() string { return "web_sessions" }
