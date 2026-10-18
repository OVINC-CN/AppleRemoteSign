# Apple Sign

**面向团队与 CI 的 macOS 应用签名、公证和制品分发服务。**

[![Checks](https://github.com/OVINC-CN/AppleRemoteSign/actions/workflows/checks.yml/badge.svg)](https://github.com/OVINC-CN/AppleRemoteSign/actions/workflows/checks.yml)
[![Docker Build](https://github.com/OVINC-CN/AppleRemoteSign/actions/workflows/docker-build.yml/badge.svg)](https://github.com/OVINC-CN/AppleRemoteSign/actions/workflows/docker-build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[快速开始](#快速开始) · [使用](#使用) · [文档](#文档) · [开发](#开发) · [贡献](#贡献) · [许可证](#许可证)

Apple Sign 将签名凭据集中在服务端，通过网页工作台和 HTTP API 为 macOS App、DMG 提供统一的发布流程。开发者上传文件，团队管理应用权限，构建流水线通过应用 Key 完成自动化接入。

## 特性

- **签名与公证**：使用 rcodesign 完成代码签名、提交 Apple 公证和 Staple，交付可分发的 ZIP 制品。
- **团队工作台**：OIDC 登录、应用授权、任务历史、任务重命名，以及公证详情和文件签名拓扑。
- **CI 接入**：应用级 OpenAPI Key，配套 macOS 脚本完成打包、上传、查询、下载和解压。
- **制品分发**：S3 或兼容对象存储承载制品，下载前检查文件是否存在，通过 EdgeOne 方式 A 鉴权链接分发。
- **并发协调**：Redis 协调多实例操作，同一应用内按文件内容与原始文件名复用任务。
- **运行观测**：JSON 日志、OpenTelemetry 追踪，以及可选的企业微信群机器人通知。

上传请求完成签名并提交 Apple，后续状态查询推动公证检查、Staple 和 S3 上传。最终文件上传成功后，任务进入 `completed` 状态；工作台和客户端脚本负责发起后续查询。

## 快速开始

### 1. 准备依赖

| 资源 | 要求 |
| --- | --- |
| 服务环境 | Docker 与 Docker Compose；后端镜像运行于 Linux |
| 数据库 | MySQL 8.0+，预先创建空数据库 |
| 任务协调 | 支持逻辑 DB 和 Lua 脚本的 Redis |
| Apple 凭据 | Developer ID 签名证书及私钥、证书密码、App Store Connect API 凭据 |
| 用户登录 | 支持 Authorization Code 与 PKCE S256 的 confidential OIDC client |
| 制品存储 | S3 或兼容存储桶，以及映射到桶根目录的 EdgeOne 方式 A 鉴权 CDN |
| 网页构建 | Node.js 24；静态站点托管或反向代理 |

### 2. 获取源码并配置

```sh
git clone https://github.com/OVINC-CN/AppleRemoteSign.git
cd AppleRemoteSign
cp .env.example .env
mkdir -p data secrets
```

编辑 `.env`，填写数据库、Redis、OIDC、S3 和 CDN 配置。将以下文件放入 `secrets/`：

| 文件名 | 内容 |
| --- | --- |
| `signing.p12` | 包含 Developer ID 证书和私钥的 PKCS#12 文件 |
| `signing-password` | 第一行为证书密码；空密码使用一个换行 |
| `apple-api-key.json` | rcodesign 格式的 App Store Connect 凭据 |

`apple-api-key.json` 的结构如下，`private_key` 为 Base64 编码的 DER EC P-256 私钥：

```json
{
  "issuer_id": "YOUR_ISSUER_ID",
  "key_id": "YOURKEYID",
  "private_key": "YOUR_BASE64_DER_PRIVATE_KEY"
}
```

在 OIDC 提供方登记 `.env` 中的 `OIDC_REDIRECT_URI`，并将初始管理员的用户名加入 `OIDC_ADMIN_USERNAMES`。

### 3. 初始化数据库

使用具有建表权限的管理账号，向空数据库导入唯一的完整建表脚本：

```sh
mysql --host=mysql.example.internal --user=admin --password apple_remote_sign \
  < support-files/sql/0001_init.sql
```

该文件一次性创建应用、用户、授权、API Key、会话、登录事务和任务表，已包含 S3 制品字段与任务显示名称。运行账号仅需目标库的 `SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限。初始化由部署者完成，应用启动时校验表结构。

### 4. 部署后端

容器使用 UID/GID `10001:10001`。在 Linux 主机上，为专用数据和凭据目录设置访问权限：

```sh
sudo chown -R 10001:10001 data secrets
chmod 700 data secrets
chmod 400 secrets/signing.p12 secrets/signing-password secrets/apple-api-key.json
docker compose up -d --build
```

Compose 将后端绑定到 `127.0.0.1:8000`，通过 HTTPS 反向代理发布 API。`data/` 持久化任务文件，`secrets/` 以只读方式挂载。后端就绪状态可通过 `GET /ready` 检查。

### 5. 发布工作台

使用 Node.js 24 构建前端：

```sh
npm --prefix src/web ci
npm --prefix src/web run build
```

将 `src/web/dist/` 发布到静态站点，将产物中的 `config.js` 配置为 API 根地址：

```js
window.__APP_CONFIG__ = {
  API_BASE_UR: 'https://api.sign.example.com',
};
```

同域部署时可将 `API_BASE_UR` 设为空字符串。静态服务器需将页面深层路由回退到 `index.html`；Cookie、CORS 和反向代理示例见[工作台部署文档](docs/web.md)。

访问工作台，使用管理员账号登录，在“应用与权限”中创建应用、保存用户授权，并按需签发 CI 使用的 Key。

## 使用

### 网页上传

选择所属应用，上传包含单个顶层 `.app` 目录或 `.dmg` 文件的 ZIP。ZIP 文件名需包含三段数字版本号，例如 `MyApp-v1.2.3.zip`。在任务详情中查看进度、公证结果和日志，完成后下载制品。

### macOS 与 CI

在 macOS 上准备 Bash、curl 7.71+、jq、Python 3.9+ 和系统 `ditto`。从工作台取得应用 Key 后，直接向脚本传入原始 App 或 DMG：

```sh
export API_BASE_URL='https://api.sign.example.com/openapi/v1'
export API_TOKEN='YOUR_APPLICATION_KEY'

bash support-files/scripts/sign-and-notarize.sh \
  './MyApp-v1.2.3.app' \
  './MyApp-notarized.app'
```

脚本会自动打包并跟进任务，完成后将结果解压到输出路径。输入名称需包含版本号，输出路径应尚不存在；保留脚本同目录的 `archive-helper.py`。DMG 的签名与 Staple 作用于 DMG 外层，其中的应用应预先完成签名。

直接使用 HTTP API 时，从 `POST /openapi/v1/jobs` 上传 ZIP 开始，保存返回的任务 ID，再查询状态和获取下载地址。请求示例、状态码及重试约定见[开发者接入文档](docs/integration.md)。

## 配置

完整配置清单位于 [`.env.example`](.env.example)，以下为部署时需要确认的配置组：

| 配置组 | 主要环境变量 |
| --- | --- |
| 数据与协调 | `DATABASE_URL`、`REDIS_URL`、`DATA_DIR` |
| 签名与公证 | `APPLE_KEY_FILE`、`SIGNING_CERTIFICATE_FILE`、`SIGNING_PASSWORD_FILE` |
| 登录与权限 | `OIDC_ISSUER_URL`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET`、`OIDC_REDIRECT_URI`、`OIDC_ADMIN_USERNAMES`、`OIDC_ALLOWED_USERNAMES` |
| 网页会话 | `FRONTEND_URL`、`CORS_ALLOWED_ORIGINS`、`COOKIE_SECURE`、`COOKIE_SAMESITE` |
| 制品存储 | `S3_BUCKET`、`S3_REGION`、`S3_ENDPOINT_URL`、`S3_PREFIX` |
| CDN 鉴权 | `CDN_BASE_URL`、`CDN_SIGN_KEY`、`CDN_SIGN_PARAM` |
| 可选集成 | `WECOM_WEBHOOK_URL`、`OTEL_EXPORTER_OTLP_ENDPOINT` |

OIDC 用户名采用精确匹配；普通用户需要同时获得登录白名单和应用授权。Key 绑定单个应用，完整令牌只展示一次，可在工作台吊销。S3 支持显式 AWS 凭据和 SDK 标准凭证链；CDN 的鉴权密钥、参数名和有效期由服务端与 CDN 规则配合配置。

## 文档

| 文档 | 内容 |
| --- | --- |
| [开发者接入](docs/integration.md) | HTTP API、ZIP 规范、任务状态、macOS 脚本与错误处理 |
| [工作台部署](docs/web.md) | OIDC、Cookie、静态托管、反向代理与浏览器 API |
| [多副本与滚动发布](docs/rolling-release.md) | 共享存储、Redis、探测、请求排空与发布配置 |
| [配置示例](.env.example) | 环境变量、默认值与填写示例 |
| [数据库初始化](support-files/sql/0001_init.sql) | MySQL 完整表结构、约束与索引 |

## 运行与维护

- **探测**：`GET /health` 用于存活检查；`GET /ready` 检查初始化、MySQL 和 Redis 的就绪状态。
- **日志**：标准输出采用 JSON，使用响应头 `X-Trace-ID` 与日志中的 `trace_id` 关联请求；通过 `docker compose logs --no-log-prefix --no-color signer` 查看原始日志。
- **追踪与通知**：配置 OTLP/gRPC 接收地址后上报追踪；配置企业微信群机器人 Webhook 后发送提交、成功和失败通知。
- **持久化**：保留 MySQL、`data/` 和 S3 制品的备份；多副本共用同一数据库、Redis 逻辑 DB 和 ReadWriteMany 数据卷。
- **镜像发布**：Git tag（`latest` 除外）触发构建并推送 `ghcr.io/ovinc-cn/appleremotesign:<Git tag>`，CI 镜像平台为 `linux/amd64`，镜像标签保留原始 Git tag。

## 开发

使用 [mise](https://mise.jdx.dev/) 管理 Python 3.14、Node.js 24、ShellCheck 和 shfmt：

```sh
mise install
mise run setup
mise run check
```

`mise run check` 执行 Ruff、mypy、模块大小检查、ShellCheck、shfmt、Biome、pytest、Vitest，以及 TypeScript 检查和前端静态构建。格式调整使用 `mise run format`，修改后重新执行完整检查。

单元测试使用临时数据库和模拟外部依赖。MySQL 集成测试通过 `TEST_MYSQL_URL` 接入手动初始化的测试库，使用仅有 DML 权限的账号，数据库名以 `_test` 结尾；Redis 集成测试通过 `TEST_REDIS_URL` 接入独立测试 DB。未配置对应地址时，这些集成测试会明确跳过。CI 使用同一份 `0001_init.sql` 初始化 MySQL，并验证表、字段、主键和索引与模型一致。

```text
src/apple_remote_sign/   后端 API、业务服务与外部集成
src/web/                 React 工作台与前端测试
support-files/sql/       完整数据库初始化脚本
support-files/scripts/   macOS 接入脚本与归档工具
tests/                   后端测试及依赖替身
docs/                    部署与接入文档
```

## 贡献

欢迎通过 [Issues](https://github.com/OVINC-CN/AppleRemoteSign/issues) 反馈问题或讨论功能，通过 [Pull Requests](https://github.com/OVINC-CN/AppleRemoteSign/pulls) 提交改进。

提交前阅读 [AGENTS.md](AGENTS.md)，为行为变化补充相关测试，在最后一次修改后运行 `mise run check`，并在 PR 中说明改动目的、验证结果和未执行的验证项目。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
