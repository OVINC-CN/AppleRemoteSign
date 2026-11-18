package authModels

import "time"

type Login struct {
	StateHash   string    `gorm:"column:state_hash;primaryKey" json:"-"`
	BrowserHash string    `gorm:"column:browser_hash"          json:"-"`
	Data        string    `gorm:"column:data"                  json:"-"`
	ExpiresAt   time.Time `gorm:"column:expires_at"            json:"-"`
}

func (Login) TableName() string { return "oidc_transactions" }
