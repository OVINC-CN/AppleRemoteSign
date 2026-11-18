package authModels

import "time"

type User struct {
	ID        string    `gorm:"column:id;primaryKey" json:"-"`
	Username  string    `gorm:"column:username"      json:"-"`
	Issuer    *string   `gorm:"column:issuer"        json:"-"`
	Subject   *string   `gorm:"column:subject"       json:"-"`
	CreatedAt time.Time `gorm:"column:created_at"    json:"-"`
}

func (User) TableName() string { return "users" }
