package db

import (
	"context"
	"errors"
	"fmt"
)

// Ping 检查已配置的共享资源，不创建新连接池。
func Ping(ctx context.Context) error {
	// 检查 MySQL，可选资源未配置时跳过。
	var mysqlErr, redisErr error
	if db != nil {
		if err := pingMySQL(ctx); err != nil {
			mysqlErr = fmt.Errorf("mysql readiness check failed: %w", err)
		}
	}

	// 检查 Redis，沿用调用方的取消和超时控制。
	if rdb != nil {
		if err := PingRedis(ctx); err != nil {
			redisErr = fmt.Errorf("redis readiness check failed: %w", err)
		}
	}

	// 完成两项检查后再汇总错误，不因 MySQL 失败而漏掉 Redis Ping。
	return errors.Join(mysqlErr, redisErr)
}

// Close 在进程退出时统一释放共享资源。
func Close() error {
	// 分别关闭资源，避免一处失败阻止其他连接池释放。
	var mysqlErr, redisErr error
	if db != nil {
		sqlDB, err := db.DB()
		if err != nil {
			mysqlErr = err
		} else {
			mysqlErr = sqlDB.Close()
		}
	}
	if rdb != nil {
		redisErr = rdb.Close()
	}

	// 合并错误，交给退出流程记录。
	return errors.Join(mysqlErr, redisErr)
}
