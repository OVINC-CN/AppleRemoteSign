package db

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	driver "github.com/go-sql-driver/mysql"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"gorm.io/driver/mysql"
	"gorm.io/gorm"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/access/accessModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authModels"
	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/jobs/jobsModels"
)

func TestExistingMySQLSchema(t *testing.T) {
	dsn := os.Getenv("TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("TEST_MYSQL_DSN 未配置")
	}
	cfg, e := driver.ParseDSN(dsn)
	if e != nil || !strings.HasSuffix(cfg.DBName, "_test") {
		t.Fatal("expected disposable MySQL database ending in _test")
	}
	cfg.ParseTime = true
	pool, e := sql.Open("mysql", cfg.FormatDSN())
	if e != nil {
		t.Fatal(e)
	}
	defer func() { _ = pool.Close() }()
	conn, e := gorm.Open(mysql.New(mysql.Config{Conn: pool}), &gorm.Config{})
	if e != nil {
		t.Fatal(e)
	}
	conn = conn.WithContext(t.Context())
	var grants []string
	if e = conn.Raw("SHOW GRANTS").Scan(&grants).Error; e != nil {
		t.Fatal(e)
	}
	for _, grant := range grants {
		if strings.Contains(grant, "ALL PRIVILEGES") || strings.Contains(grant, "CREATE") {
			t.Fatal("DML-only account required")
		}
	}
	for _, m := range []any{&accessModels.Application{}, &accessModels.Key{}, &accessModels.Grant{}, &authModels.Session{}, &authModels.Login{}, &authModels.User{}, &jobsModels.Job{}} {
		stmt := gorm.Statement{DB: conn}
		if e := stmt.Parse(m); e != nil {
			t.Fatal(e)
		}
		var columns []struct {
			Name     string
			Nullable string
		}
		if e = conn.Raw("SELECT column_name AS name,is_nullable AS nullable FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=?", stmt.Table).
			Scan(&columns).
			Error; e != nil {
			t.Fatal(e)
		}
		if len(columns) != len(stmt.Schema.DBNames) {
			t.Fatalf("column count mismatch: %s", stmt.Table)
		}
		for _, c := range columns {
			field := stmt.Schema.FieldsByDBName[c.Name]
			if field == nil ||
				(field.FieldType.Kind() == reflect.Pointer) != (c.Nullable == "YES") {
				t.Fatalf("incompatible %s.%s", stmt.Table, c.Name)
			}
		}
	}
	rollback := errors.New("rollback fixture")
	e = conn.Transaction(func(tx *gorm.DB) error {
		a := accessModels.Application{
			ID:         uuid.NewString(),
			Identifier: uuid.NewString(),
			Name:       "legacy",
			Enabled:    true,
		}
		if e := tx.Create(&a).Error; e != nil {
			return e
		}
		j := jobsModels.Job{
			ID:            uuid.NewString(),
			ApplicationID: a.ID,
			Filename:      "old.zip",
			Status:        "processing",
		}
		if e := tx.Create(&j).Error; e != nil {
			return e
		}
		var loaded jobsModels.Job
		if e := tx.Take(&loaded, "id=?", j.ID).Error; e != nil {
			return e
		}
		if loaded.ID != j.ID || loaded.SubmissionID != nil {
			return errors.New("legacy row mismatch")
		}
		return rollback
	})
	if !errors.Is(e, rollback) {
		t.Fatal(e)
	}
}

func TestExistingRedisLease(t *testing.T) {
	raw := os.Getenv("TEST_REDIS_URL")
	if raw == "" {
		t.Skip("TEST_REDIS_URL 未配置")
	}
	opts, e := redis.ParseURL(raw)
	if e != nil {
		t.Fatal("invalid test Redis URL")
	}
	old := rdb
	rdb = redis.NewClient(opts)
	defer func() { _ = rdb.Close(); rdb = old }()
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	id := "test-" + uuid.NewString()
	l, e := Acquire(ctx, id, true)
	if e != nil {
		t.Fatal(e)
	}
	defer rdb.Del(ctx, l.key)
	second, e := Acquire(ctx, id, false)
	if e != nil || second != nil {
		t.Fatal("lease was not exclusive")
	}
	if e = rdb.Set(ctx, l.key, "new-owner", time.Minute).Err(); e != nil {
		t.Fatal(e)
	}
	if !errors.Is(Check(l.Context()), ErrLease) || !errors.Is(l.Close(), ErrLease) {
		t.Fatal("replacement lease was not detected")
	}
	if v, e := rdb.Get(ctx, l.key).Result(); e != nil || v != "new-owner" {
		t.Fatal("replacement lease was released")
	}
}
