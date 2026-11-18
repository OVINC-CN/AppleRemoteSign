package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/OVINC-CN/AppleSign/src/api/internal/cmd"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

const exitCodeFailure = 1

// @title Apple 签名 API
// @version 1.0
// @description 业务 JSON 响应包含 data 和 error_message；追踪始终开启。
// @BasePath /
func main() {
	// 先完成退出清理，再向调用方返回失败状态。
	if err := run(); err != nil {
		logger.Logger.Errorf("Command failed: %v", err)
		os.Exit(exitCodeFailure)
	}
}

func run() error {
	// 统一接收终止信号，供 HTTP 和后台任务完成收尾。
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// 全局配置和共享资源已在包初始化阶段准备完成。
	return cmd.Execute(ctx)
}
