package accessModels

import "time"

type Application struct {
	ID         string    `gorm:"column:id;primaryKey" json:"id"`
	Identifier string    `gorm:"column:identifier"    json:"identifier"`
	Name       string    `gorm:"column:name"          json:"name"`
	Enabled    bool      `gorm:"column:enabled"       json:"enabled"`
	CreatedAt  time.Time `gorm:"column:created_at"    json:"created_at"`
}

func (Application) TableName() string { return "applications" }
