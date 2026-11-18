package accessModels

type Grant struct {
	ApplicationID string `gorm:"column:application_id;primaryKey" json:"-"`
	UserID        string `gorm:"column:user_id;primaryKey"        json:"-"`
}

func (Grant) TableName() string { return "application_grants" }
