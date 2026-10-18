-- Complete schema for a NEW MySQL 8.0+ database; import this file once.
-- Includes all current tables, S3 artifact keys, and job display names.
-- Select an empty database before importing; this file creates no database or users.
-- All timestamps are UTC. The application never executes DDL.

CREATE TABLE applications (
    id VARCHAR(36) NOT NULL,
    identifier VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL,
    name VARCHAR(255) NOT NULL,
    enabled BOOL NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (identifier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE oidc_transactions (
    state_hash VARCHAR(64) NOT NULL,
    browser_hash VARCHAR(64) NOT NULL,
    data TEXT NOT NULL,
    expires_at DATETIME NOT NULL,
    PRIMARY KEY (state_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE INDEX ix_oidc_transactions_expires_at ON oidc_transactions (expires_at);

CREATE TABLE users (
    id VARCHAR(36) NOT NULL,
    username VARCHAR(255) COLLATE utf8mb4_0900_bin NOT NULL,
    issuer VARCHAR(512) COLLATE utf8mb4_0900_bin,
    subject VARCHAR(255) COLLATE utf8mb4_0900_bin,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_users_identity UNIQUE (issuer, subject),
    UNIQUE (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE api_keys (
    id VARCHAR(36) NOT NULL,
    application_id VARCHAR(36) NOT NULL,
    name VARCHAR(255) NOT NULL,
    prefix VARCHAR(16) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    last_used_at DATETIME,
    revoked_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(application_id) REFERENCES applications (id),
    UNIQUE (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE INDEX ix_api_keys_application_id ON api_keys (application_id);

CREATE TABLE application_grants (
    application_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (application_id, user_id),
    FOREIGN KEY(application_id) REFERENCES applications (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE web_sessions (
    token_hash VARCHAR(64) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    csrf_token VARCHAR(64) NOT NULL,
    expires_at DATETIME NOT NULL,
    PRIMARY KEY (token_hash),
    FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE INDEX ix_web_sessions_expires_at ON web_sessions (expires_at);

CREATE TABLE jobs (
    id VARCHAR(36) NOT NULL,
    application_id VARCHAR(36) NOT NULL,
    submitted_by_user_id VARCHAR(36),
    submitted_by_key_id VARCHAR(36),
    filename VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    status VARCHAR(16) NOT NULL,
    submission_id VARCHAR(36),
    original_path VARCHAR(1024),
    submitted_path VARCHAR(1024),
    artifact_path VARCHAR(1024),
    artifact_s3_key VARCHAR(1024),
    original_sha256 VARCHAR(64),
    submitted_sha256 VARCHAR(64),
    artifact_sha256 VARCHAR(64),
    size_bytes BIGINT,
    error_message TEXT NOT NULL,
    diagnostics LONGTEXT NOT NULL,
    notary_log LONGTEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_checked_at DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT ck_jobs_status CHECK (status IN ('preparing', 'processing', 'completed', 'failed')),
    FOREIGN KEY(application_id) REFERENCES applications (id),
    FOREIGN KEY(submitted_by_user_id) REFERENCES users (id),
    FOREIGN KEY(submitted_by_key_id) REFERENCES api_keys (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE INDEX ix_jobs_application_created ON jobs (application_id, created_at, id);

CREATE INDEX ix_jobs_created_at ON jobs (created_at);

CREATE INDEX ix_jobs_hash_status ON jobs (application_id, original_sha256, status, created_at);

CREATE UNIQUE INDEX ix_jobs_submission_id ON jobs (submission_id);
