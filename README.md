# Apple Sign

**面向团队与 CI 的 macOS 应用签名、公证和制品分发服务。**

[![Checks](https://github.com/OVINC-CN/AppleSign/actions/workflows/checks.yml/badge.svg)](https://github.com/OVINC-CN/AppleSign/actions/workflows/checks.yml)
[![Docker Build](https://github.com/OVINC-CN/AppleSign/actions/workflows/docker-build.yml/badge.svg)](https://github.com/OVINC-CN/AppleSign/actions/workflows/docker-build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[快速开始](#快速开始) · [使用](#使用) · [配置](#配置) · [开发](#开发) · [贡献](#贡献) · [许可证](#许可证)

Apple Sign 将签名凭据集中在服务端，通过网页工作台和 HTTP API 为 macOS App、DMG 提供统一的发布流程。开发者上传文件，团队管理应用权限，构建流水线通过应用 Key 完成自动化接入。

## 特性

- **签名与公证**：使用 rcodesign 完成代码签名、提交 Apple 公证和 Staple，交付可分发的 ZIP 制品。
- **团队工作台**：OIDC 登录、应用授权、任务历史、任务重命名，以及公证详情和文件签名拓扑。
- **CI 接入**：应用级 OpenAPI Key，配套 macOS 脚本完成打包、上传、查询、下载和解压。
- **制品分发**：S3 或兼容对象存储承载制品，下载前检查文件是否存在，缺失时自动补传，通过 EdgeOne 方式 A 鉴权链接分发。
- **并发协调**：Redis 协调多实例操作，同一应用内按文件内容与原始文件名复用任务。
- **运行观测**：JSON 日志、OpenTelemetry 追踪，以及可选的企业微信群机器人通知。

上传请求完成签名并提交 Apple，后续状态查询推动公证检查、Staple 和 S3 上传。最终文件上传成功后，任务进入 `completed` 状态；工作台和客户端脚本负责发起后续查询。

## 快速开始

### 1. 准备依赖

| 资源 | 要求 |
| --- | --- |
| 服务环境 | Docker，支持 Linux 容器 |
| 数据库 | MySQL 8.0+，预先创建空数据库 |
| 任务协调 | 支持逻辑 DB 和 Lua 脚本的 Redis |
| Apple 凭据 | Developer ID 签名证书及私钥、证书密码、App Store Connect API 凭据 |
| 用户登录 | 支持 Authorization Code 与 PKCE S256 的 confidential OIDC client |
| 制品存储 | S3 或兼容存储桶，以及映射到桶根目录的 EdgeOne 方式 A 鉴权 CDN |
| 网页构建 | Node.js 24；静态站点托管或反向代理 |

### 2. 获取源码并配置

```sh
git clone https://github.com/OVINC-CN/AppleSign.git
cd AppleSign
cp src/api/config.yaml config.yaml
mkdir -p data secrets
```

编辑 `config.yaml`，填写数据库、Redis、OIDC、S3 和 CDN 配置。签名凭据放在 `secrets/` 目录。

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

在 OIDC 提供方登记 `config.yaml` 中的 `oidc.redirect_uri`，并将初始管理员的用户名加入 `oidc.admin_usernames`。

### 3. 初始化数据库

使用具有建表权限的管理账号，向空数据库导入建表脚本。

```sh
mysql --host=mysql.example.internal --user=admin --password apple_remote_sign \
  < support-files/sql/0001_init.sql
```

该脚本创建应用、用户、授权、API Key、会话、登录事务和签名任务表。运行账号仅需目标库的 `SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限。初始化由部署者完成，应用启动时校验表结构。

### 4. 部署后端

容器使用 UID/GID `10001:10001`。在 Linux 主机上，为专用数据和凭据目录设置访问权限：

```sh
sudo chown -R 10001:10001 data secrets config.yaml
sudo chmod 700 data secrets
sudo chmod 400 config.yaml secrets/signing.p12 secrets/signing-password secrets/apple-api-key.json

docker build -t applesign:local .
docker run -d --name signer --restart unless-stopped --init --stop-timeout 3900 \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  -v "$PWD/secrets:/run/secrets:ro" \
  -v "$PWD/data:/data" \
  applesign:local
```

容器在宿主机的 `127.0.0.1:8000` 提供 API，通过 HTTPS 反向代理对外发布。`data/` 持久化任务文件，`secrets/` 以只读方式挂载。后端就绪状态可通过 `GET /ready` 检查。

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

同域部署时可将 `API_BASE_UR` 设为空字符串。反向代理将 `/api/`、`/openapi/`、`/health`、`/ready` 转发至后端，静态服务器将前端页面路径回退到 `index.html`。

访问工作台，使用管理员账号登录，在“应用与权限”中创建应用、保存用户授权，并按需签发 CI 使用的 Key。

### 前端 CDN 刷新

完成前端构建后，在仓库根目录生成刷新清单：

```sh
cdn_manifest=$(mktemp)
mise exec -- make cdn-urls CDN_ORIGIN=https://sign.example.com > "$cdn_manifest"
```

`CDN_ORIGIN` 填写前端站点的协议和域名，`CDN_WEB_DIST` 默认指向 `src/web/dist`。清单逐行列出固定页面和构建目录中的静态文件，可提交给 CDN 刷新。
页面 HTML 的 CDN 缓存键应忽略查询参数，例如 `/jobs/detail?job_id=…` 统一使用 `/jobs/detail` 的缓存键。

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

HTTP 客户端使用应用 Key 作为 Bearer 凭据。通过 `POST /openapi/v1/jobs` 的 multipart 字段 `file` 上传 ZIP，保存响应中的 `data.id`。调用 `GET /openapi/v1/jobs/{id}` 刷新状态，任务完成后请求 `GET /openapi/v1/jobs/{id}/artifact` 获取指向 CDN 的 302 跳转。接口定义可用同一凭据从 `GET /openapi/v1/openapi.json` 获取。

## 配置

后端使用 `--config` 指定 YAML 文件。MySQL DSN 示例为 `signer:password@tcp(mysql:3306)/apple_remote_sign?charset=utf8mb4`。Redis URL 末尾指定逻辑库，例如 `rediss://signer:password@redis.example.com:6380/3`。超时值带单位，如 `30s`，上传和解压大小限制以字节为单位。[完整配置示例](src/api/config.yaml)

| 配置组 | YAML 字段 |
| --- | --- |
| 数据与协调 | `mysql.dsn`、`redis.url`、`signing.data_dir` |
| 签名与公证 | `signing.apple_key_file`、`signing.certificate_file`、`signing.password_file` |
| 登录与权限 | `oidc.issuer_url`、`oidc.client_id`、`oidc.client_secret`、`oidc.redirect_uri`、`oidc.admin_usernames`、`oidc.allowed_usernames` |
| 网页会话 | `web.frontend_url`、`web.allowed_origins`、`web.cookie_secure`、`web.cookie_same_site` |
| 制品存储 | `s3.bucket`、`s3.region`、`s3.endpoint_url`、`s3.prefix` |
| CDN 鉴权 | `cdn.base_url`、`cdn.sign_key`、`cdn.sign_param` |
| 可选集成 | `wecom.webhook_url`、`trace.endpoint` |

OIDC 用户名采用精确匹配；普通用户需要同时获得登录白名单和应用授权。Key 绑定单个应用，完整令牌只展示一次，可在工作台吊销。S3 支持显式 AWS 凭据和 SDK 标准凭证链；CDN 的鉴权密钥、参数名和有效期由服务端与 CDN 规则配合配置。

## 运行与维护

- **探测**：`GET /health` 用于存活检查；`GET /ready` 检查初始化、MySQL 和 Redis 的就绪状态。
- **日志**：标准输出采用 JSON，使用响应头 `X-Trace-ID` 与日志中的 `trace_id` 关联请求；通过 `docker logs signer` 查看原始日志。
- **追踪与通知**：配置 OTLP/gRPC 接收地址后上报追踪；配置企业微信群机器人 Webhook 后发送提交、成功和失败通知。
- **持久化**：保留 MySQL、`data/` 和 S3 制品的备份；多副本共用同一数据库、Redis 逻辑 DB 和 ReadWriteMany 数据卷。
- **镜像发布**：PR 触发镜像构建验证。推送 Git tag（`latest` 除外）时发布 `ghcr.io/ovinc-cn/applesign:<Git tag>`，CI 镜像平台为 `linux/amd64`，镜像标签保留原始 Git tag。

## 开发

使用 mise 管理 Go、Python、Node.js、ShellCheck 和 shfmt。开发入口、模块布局与各项检查见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```sh
mise install
mise run setup
mise check
```

## 贡献

欢迎通过 [Issues](https://github.com/OVINC-CN/AppleSign/issues) 反馈问题或讨论功能，通过 [Pull Requests](https://github.com/OVINC-CN/AppleSign/pulls) 提交改进。

提交前阅读 [AGENTS.md](AGENTS.md)，为行为变化补充相关测试，在最后一次修改后运行 `mise run check`，并在 PR 中说明改动目的、验证结果和未执行的验证项目。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
