package jobsModels

import (
	"time"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
)

type Data struct {
	ID           string  `json:"id"`
	Status       string  `json:"status"`
	SubmissionID *string `json:"submission_id"`
}
type View struct {
	Data
	Application   accessModels.Application `json:"application"`
	Filename      string                   `json:"filename"`
	DisplayName   string                   `json:"display_name"`
	Source        string                   `json:"source"`
	SizeBytes     *int64                   `json:"size_bytes"`
	ErrorMessage  string                   `json:"error_message"`
	CreatedAt     time.Time                `json:"created_at"`
	UpdatedAt     time.Time                `json:"updated_at"`
	LastCheckedAt *time.Time               `json:"last_checked_at"`
}
type Detail struct {
	View
	Diagnostics    string  `json:"diagnostics"`
	NotaryLog      *string `json:"notary_log"`
	OriginalSHA256 *string `json:"original_sha256"`
	ArtifactSHA256 *string `json:"artifact_sha256"`
}
type Page struct {
	Items    []View `json:"items"`
	Total    int64  `json:"total"`
	Page     int    `json:"page"`
	PageSize int    `json:"page_size"`
}
