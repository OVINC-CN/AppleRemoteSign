# 仓库工作约束

- 不得安装浏览器或浏览器运行时，包括 Playwright、Chromium 等测试浏览器。
- 不得运行程序，包括启动应用或服务
- 仅进行静态阅读、分析和文件修改，允许运行单元测试和代码检查代码格式化；需要运行程序才能完成的验证，应说明未执行。
- 只有用户明确授权后，才可对本次授权范围内的事项例外执行。
- 所有改动必须在最后一次文件修改后运行 `mise check`（或 `mise run check`），前端 lint、前端生产构建和 Go 后端构建均须通过。检查失败时必须修复后重新执行。
- 最终回复必须说明检查结果；因环境限制未执行的验证或跳过的集成测试必须明确列出，不得声称已通过。

## 前端约束

- 前端页面路由必须使用固定路径。禁止使用 path params 定义路由或拼接页面 URL，任务 ID、筛选条件等动态值通过 query params 传递。
- 页面路径统一定义在 `src/web/src/lib/routes.ts`，供前端路由和 `make cdn-urls` 共用。

## 后端约束

- 每个业务 app 放在 `src/api/internal/apps/<app>`，使用带 app 前缀的 Models、Routers、Svc、Utils、Task 等包。Models 保存结构，Routers 提取和校验参数，Svc 处理业务，Task 处理恢复扫描。
- Echo 路由集中在 `internal/httpd` 注册。使用 `config.Config`、`db.DB()` 和 `db.RDB()` 访问共享资源，数据库、租约与外部调用传递当前上下文。第三方协议与签名归属 `internal/apis/<service>`。
- 后端运行参数使用 YAML，Go 模块和构建缓存使用系统默认位置。注释使用简体中文，Logrus 日志使用英文，通过 OpenTelemetry 关联请求。凭据、会话、签名 URL 查询串须脱敏。
- 公共 API、工作台 API、探针和下载保持既有契约。数据库由部署者手动初始化，运行账号仅使用 DML 权限。任务丢失租约后停止外部操作和状态写入。
- 后端格式遵循 gofmt、goimports 和 100 列目标。GitHub 数据使用 gh CLI 查询。
