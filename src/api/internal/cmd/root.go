package cmd

import (
	"context"
	"fmt"

	"github.com/OVINC-CN/AppleSign/src/api/internal/tracing"

	"github.com/spf13/cobra"

	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
	"github.com/OVINC-CN/AppleSign/src/api/internal/db"
	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

var rootCmd = &cobra.Command{
	Use: "apple-remote-sign", Short: "Apple 签名服务",
	SilenceUsage: true, SilenceErrors: true, Args: cobra.NoArgs,
}

func init() {
	// 配置已在依赖包 init 中读取，此处声明同一启动选项供命令解析和说明使用。
	rootCmd.PersistentFlags().String(config.ConfigFlagName, config.FilePath, "指定 YAML 配置文件")
	rootCmd.CompletionOptions.DisableDefaultCmd = true

	// 默认启动 API，其他模式显式选择子命令。
	rootCmd.RunE = apiCmd.RunE
	rootCmd.AddCommand(apiCmd)
}

func onExit() {

	// 在退出超时内完成追踪数据上报。
	ctx, cancel := context.WithTimeout(context.Background(), config.Config.App.ShutdownTimeout)
	defer cancel()
	if err := tracing.Shutdown(ctx); err != nil {
		logger.Logger.Errorf("Trace shutdown failed: %v", err)
	}

	// 后台服务和 HTTP 请求完成收尾后，统一释放共享连接。
	if err := db.Close(); err != nil {
		logger.Logger.Errorf("Resource shutdown failed: %v", err)
	}
}

// Execute 执行命令，不重新读取或逐层传递配置。
func Execute(ctx context.Context) error {
	// 为所有服务模式统一安排退出清理。
	defer onExit()

	// 参数和命令错误交给入口统一输出。
	if err := rootCmd.ExecuteContext(ctx); err != nil {
		return fmt.Errorf("command execution failed: %w", err)
	}
	return nil
}
