# 开发与验证

Go 服务入口为 `src/api/main.go`。Cobra 的 `api --config <path>` 命令加载 YAML，检查共享资源并启动 Echo。集中路由在 `internal/httpd`，认证、应用授权和签名分别位于 `internal/apps/auth`、`internal/apps/access` 和 `internal/apps/jobs`。Go 命令以 `src/api` 为模块根目录。

运行 `mise run setup` 准备 Go 和前端依赖。最后一次修改后，在仓库根目录运行 `mise check`。本地和 GitHub Checks 均执行前端 Biome lint、TypeScript 与 Vite 生产构建，以及 Go 后端构建。

需要运行单元测试时，先用 `mise run setup-client` 准备客户端脚本测试依赖，再执行 `mise run test`。该任务运行 Go 竞态测试、客户端脚本 pytest 和前端 Vitest。Go 契约测试使用 `testfixture` 构建标签，通过 Echo 内存请求、临时 SQLite 文件和外部服务替身验证授权及签名流程。

真实 MySQL 测试读取 `TEST_MYSQL_DSN`，要求数据库名以 `_test` 结尾并使用 DML 账号。向空的测试库手动导入 `support-files/sql/0001_init.sql`。租约集成测试通过 `TEST_REDIS_URL` 接入独立测试库。未提供对应测试连接时会跳过。不得在本地启动验收服务或安装浏览器。
