package jobsModels

import "time"

type Job struct {
	ID                string     `gorm:"column:id;primaryKey"        json:"-"`
	ApplicationID     string     `gorm:"column:application_id"       json:"-"`
	SubmittedByUserID *string    `gorm:"column:submitted_by_user_id" json:"-"`
	SubmittedByKeyID  *string    `gorm:"column:submitted_by_key_id"  json:"-"`
	Filename          string     `gorm:"column:filename"             json:"-"`
	DisplayName       *string    `gorm:"column:display_name"         json:"-"`
	Status            string     `gorm:"column:status"               json:"-"`
	SubmissionID      *string    `gorm:"column:submission_id"        json:"-"`
	OriginalPath      *string    `gorm:"column:original_path"        json:"-"`
	SubmittedPath     *string    `gorm:"column:submitted_path"       json:"-"`
	ArtifactPath      *string    `gorm:"column:artifact_path"        json:"-"`
	ArtifactS3Key     *string    `gorm:"column:artifact_s3_key"      json:"-"`
	OriginalSHA256    *string    `gorm:"column:original_sha256"      json:"-"`
	SubmittedSHA256   *string    `gorm:"column:submitted_sha256"     json:"-"`
	ArtifactSHA256    *string    `gorm:"column:artifact_sha256"      json:"-"`
	SizeBytes         *int64     `gorm:"column:size_bytes"           json:"-"`
	ErrorMessage      string     `gorm:"column:error_message"        json:"-"`
	Diagnostics       string     `gorm:"column:diagnostics"          json:"-"`
	NotaryLog         *string    `gorm:"column:notary_log"           json:"-"`
	CreatedAt         time.Time  `gorm:"column:created_at"           json:"-"`
	UpdatedAt         time.Time  `gorm:"column:updated_at"           json:"-"`
	LastCheckedAt     *time.Time `gorm:"column:last_checked_at"      json:"-"`
}

func (Job) TableName() string { return "jobs" }
