//go:build testfixture

package db

import (
	"github.com/redis/go-redis/v9"
	"gorm.io/gorm"
)

// BindFixture 只在测试构建中绑定临时连接，生产二进制不包含这个入口。
func BindFixture(sql *gorm.DB, cache *redis.Client) func() {
	oldDB, oldRedis := db, rdb
	db, rdb = sql, cache
	return func() { db, rdb = oldDB, oldRedis }
}
