# 网页工作台部署与 API

前端和后端独立部署。`src/web` 是 React + TypeScript + Vite + Tailwind CSS + shadcn/ui 单页应用；`src/web/dist` 只包含静态文件。后端 Docker 镜像仅运行 FastAPI，不包含前端服务。

## 首次配置

1. 准备空的 MySQL 8.0+ 数据库，手动导入唯一建表脚本 `support-files/sql/0001_init.sql`，其中已包含 S3 制品字段与任务显示名称。运行账号仅需 `SELECT/INSERT/UPDATE/DELETE`，应用不会自动修改数据库结构。
2. 在身份提供方注册 confidential OIDC client，启用 Authorization Code、PKCE S256，并登记后端回调地址，例如 `https://api.sign.example.com/api/v1/auth/callback`。
3. 复制 `.env.example`，填写数据库、签名凭据、OIDC、S3/CDN 和下面的配置项，启动后端。
4. 执行 `npm --prefix src/web ci`、`npm --prefix src/web run build`，发布 `src/web/dist`。修改产物中的 `config.js`，设置后端地址。
5. 管理员登录，创建应用、选择可访问用户名并保存授权；需要 CI 调用时签发 Key。完整 Key 只返回一次，放入 CI 的密钥存储中。

identifier 区分大小写，允许字母、数字、点、横线、下划线，首字符必须是字母或数字，长度 1–128。它是应用权限标识，不检查包内 Bundle ID。普通用户必须同时满足登录白名单和应用授权；管理员拥有全局访问权限。停用应用后所有签名入口都拒绝提交，普通用户及 Key 无法访问该应用，管理员可查看已有历史。

## 后端配置

| 环境变量 | 含义 / 默认值 |
| --- | --- |
| `OIDC_ISSUER_URL` | 必填，必须与 discovery 和 ID token 中的 issuer 精确一致；使用其 `.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | 必填，后端 OIDC 客户端凭据 |
| `OIDC_REDIRECT_URI` | 必填，固定后端回调 URL，不能设为前端路由 |
| `OIDC_USERNAME_CLAIM` | `preferred_username`；先取已验证的 ID token，缺失时查询 UserInfo 并核对 sub |
| `OIDC_SCOPES` | `openid profile`，必须包含 openid，可按提供方增加 scope |
| `OIDC_TOKEN_ENDPOINT_AUTH_METHOD` | `client_secret_basic`，也支持 `client_secret_post` |
| `OIDC_ADMIN_USERNAMES` | 必填非空 JSON 数组，如 `["admin"]` |
| `OIDC_ALLOWED_USERNAMES` | `[]`，普通用户登录白名单；空列表仅允许管理员 |
| `FRONTEND_URL` | 必填，前端 Origin，如 `https://sign.example.com`，登录成功返回其 `/jobs` |
| `CORS_ALLOWED_ORIGINS` | `[]`，额外允许的前端 Origin；FRONTEND_URL 自动允许，不支持 `*` |
| `COOKIE_NAME` | `ars_session`；每次登录事务使用独立的 `_oidc_<state 哈希>` Cookie，避免多个标签页互相覆盖 |
| `COOKIE_DOMAIN` | 默认不设置，host-only；仅需要时设置 API 主机或合法父域 |
| `COOKIE_SECURE` | `true`，本机 HTTP 开发可设 `false` |
| `COOKIE_SAMESITE` | `lax`；也支持 `none`，此时必须开启 Secure |
| `SESSION_TTL_SECONDS` | `28800`，固定 8 小时，不滑动续期 |
| `OIDC_LOGIN_TTL_SECONDS` | `600`，登录事务最长 10 分钟 |
| `OIDC_TIMEOUT_SECONDS` | `15`，身份提供方 HTTP 请求超时 |

配置 URL 默认要求 HTTPS，仅本机开发允许 HTTP。用户名精确匹配，区分大小写。用户名可预授权；首次登录将该用户记录绑定到 issuer/sub，之后不允许其他身份使用同名接管权限，也不自动处理账号改名。修改用户名 claim、issuer 或账号命名策略前，应核实已有绑定和授权，不能简单覆盖用户身份。

会话保存于 MySQL，Cookie 仅包含随机令牌，数据库仅存令牌哈希。登录成功轮换当前会话，退出立即删除服务端会话。退出仅影响本系统，不执行身份提供方全局退出。过期会话和登录事务在创建新记录时清理；数据库备份应按凭据数据管理。

浏览器所有请求携带 credentials；写操作还必须携带 `/api/v1/me` 返回的 `csrf_token` 到 `X-CSRF-Token`，并具有被允许的 Origin。OIDC 回调使用一次性的 state、浏览器事务 Cookie 和 nonce 校验，不依赖前端 JavaScript 保存 OIDC token。

同主域子域部署可保留 host-only Cookie，浏览器仍会对 API 域名发送它。`COOKIE_DOMAIN` 不能设置为无关域名或公共后缀。完全跨站时需要 `SameSite=None; Secure`，并受到第三方 Cookie 策略限制；Cookie Domain 无法绕过这一限制。需要兼容禁止第三方 Cookie 的浏览器时，将 API 反代到前端同站域名。

## 静态托管

`src/web/public/config.js` 会原样复制到构建产物：

```js
window.__APP_CONFIG__ = {
  API_BASE_UR: "https://api.sign.example.com"
};
```

字段名保留为约定的 `API_BASE_UR`。填写后端根 URL，不包含 `/api/v1`、查询参数或凭据；空字符串使用前端当前 Origin。此文件是公开资源，不能放 Key 或 OIDC client secret。修改后刷新页面即可生效。

回调报 `login_cookie_missing` 表示浏览器没有送回该次登录的 Cookie；检查以上 Origin、Cookie Domain、Secure 和浏览器限制。`login_cookie_mismatch` 表示浏览器绑定不匹配，`login_expired` 表示事务过期或已消费。错误会显示在登录页，服务端记录错误类别而不记录 Cookie、state 或授权码。修改配置后应从登录页重新发起登录，不能重用旧 callback URL。

Nginx 静态站点示例（域名、证书和 root 按实际服务配置）：

```nginx
server {
    listen 443 ssl;
    server_name sign.example.com;
    root /srv/apple-remote-sign/web;

    location = /config.js {
        add_header Cache-Control "no-store" always;
        try_files $uri =404;
    }
    location = /index.html {
        add_header Cache-Control "no-cache" always;
    }
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }
    location /helper/ {
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

若同域反代，将 `/api/`、`/openapi/`、`/health` 和 `/ready` 转发后端，不能落入 SPA 回退。代理上传大小限制须匹配 `MAX_UPLOAD_BYTES`；上传接口会等待签名与提交，代理超时应覆盖两个命令时限加上传耗时。跨域代理保留 CORS/Set-Cookie 响应头，私有 API 不缓存。多副本共用 MySQL、Redis DB 和 ReadWriteMany PVC，探测及退出宽限配置见 [滚动发布文档](rolling-release.md)。

构建会递归复制仓库 `support-files/scripts` 到 `src/web/dist/helper/`，无需额外执行复制命令。完整部署 `dist` 后，可下载 `/helper/sign-and-notarize.sh` 与 `/helper/archive-helper.py`，脚本的客户端凭据变量名仍为 `API_TOKEN`。

普通 API 请求等待上限为 30 秒；任务刷新包含 Apple 查询、staple 与 S3 上传，浏览器等待上限为 3 分钟。超时会取消浏览器等待并显示错误，自动轮询暂停，用户可手动重试刷新；服务端上传可能继续进行，同一任务的后续刷新在前次操作结束前返回 409。同步上传最多等待 2 小时；上传失败或超时不自动重传，先查看历史确认任务状态。离开查询页面会取消未完成的请求，但取消浏览器连接不会撤销服务端已开始的签名或提交。

签名详情请求最多等待 2 分钟，包含缺失缓存时的 Apple 状态查询、日志地址获取与日志下载。该请求与页面状态刷新串行执行；获取失败只在签名详情区域提示并提供重试，现有状态轮询继续按规则执行。离开页面或切换任务会取消等待并清理旧详情。

详情刷新遇到同一任务已有操作在运行时立即返回 409，避免请求排队。服务日志通过独立的 `trace_id` 字段关联请求，`X-Trace-ID` 响应头可用于排查，详见 [运行与维护](../README.md#运行与维护)。

## 浏览器 API

除登录入口和回调外，`/api/v1` 仅接受 Cookie 会话；OpenAPI Key 不能代替网页登录，Cookie 不能代替 `/openapi/v1` 的 Bearer Key。响应格式统一为 `{ "data": ..., "error_message": "" }`，失败保持相同 envelope。以下路径均相对于后端根 URL。

| 方法和路径 | 输入 / 返回 |
| --- | --- |
| `GET /api/v1/auth/login` | 跳转到固定 OIDC 提供方 |
| `GET /api/v1/auth/callback` | 校验 code/state；成功返回前端 `/jobs`，失败返回 `/login?error=...` |
| `POST /api/v1/auth/logout` | 使当前会话失效并删除 Cookie |
| `GET /api/v1/me` | username、is_admin、csrf_token、max_upload_bytes |
| `GET /api/v1/applications` | 可见应用数组，字段 id、identifier、name、enabled、created_at |
| `GET /api/v1/jobs` | page 默认 1，page_size 默认 20/最大 100；可按 application_id、status、filename 筛选，filename 同时匹配任务名称和原始文件名；返回 items、total、page、page_size |
| `GET /api/v1/jobs/{id}` | 任务详情和脱敏日志；即使任务失败也返回 200，读取不调用 Apple |
| `GET /api/v1/jobs/{id}/notary-details` | 返回 Accepted 公证详情；数据库缓存优先，缺失时确认 Apple 为 Accepted 后获取并保存；未提交或非 Accepted 返回 200 且 data 为 null；忙碌返回 409，上游失败返回 502 |
| `PATCH /api/v1/jobs/{id}` | `{display_name}`，去除首尾空白后为 1–255 字符且不能包含控制字符；返回任务摘要；忙碌时立即返回 409 |
| `POST /api/v1/applications/{id}/jobs` | multipart 的 file，沿用 ZIP 校验；同步返回 id、status、submission_id |
| `POST /api/v1/jobs/{id}/refresh` | 显式查询 Apple，通过后 staple 并上传 S3，上传成功才完成；忙碌时立即返回 409；业务失败返回 500 且 data 含任务信息 |
| `GET /api/v1/jobs/{id}/artifact-url` | S3 对象存在时返回 200，data 为 `{url}`，禁止缓存；未完成返回 409，失败任务返回 500；对象不存在返回 410 和文件已过期提示，S3 查询异常返回 502 |
| `GET /api/v1/admin/users` | 可授权用户名数组，包含配置中的管理员和白名单 |
| `GET /api/v1/admin/applications` | 全部应用 |
| `POST /api/v1/admin/applications` | `{identifier, name}`，成功 201 |
| `PATCH /api/v1/admin/applications/{id}` | `{name?, enabled?}`，拒绝修改 identifier |
| `GET /api/v1/admin/applications/{id}/users` | 已授权用户名数组 |
| `PUT /api/v1/admin/applications/{id}/users` | `{usernames: [...]}`，完整替换授权名单；撤销立即影响后续请求 |
| `GET /api/v1/admin/applications/{id}/keys` | Key 元数据，不包含哈希或完整令牌 |
| `POST /api/v1/admin/applications/{id}/keys` | `{name}`，成功 201，返回 `{key: 元数据, token: 明文}`；只展示一次 |
| `POST /api/v1/admin/applications/{id}/keys/{key_id}/revoke` | 吊销，可重复调用；Key 不能转绑应用 |

任务摘要包含所属应用、原始文件名 filename、任务名称 display_name、大小、来源、状态、submission ID、创建/更新时间、最近查询时间及错误信息；详情另含 diagnostics、notary_log、原始及产物 SHA-256。未重命名时 display_name 返回原始文件名；重命名只修改显示名称，不修改本地文件、S3 key、下载文件名或签名去重结果。搜索使用 SQL LIKE，大小写和重音匹配由对应列的数据库排序规则控制，`%` 和 `_` 按字面量匹配。时间为 UTC ISO 8601。无权限的应用、任务或下载返回 404；管理员接口对普通用户返回 403。授权在数据库中执行，分页总数不包含不可见记录。不同应用之间不共享签名去重结果。

签名历史的“耗时”以 `时:分:秒` 展示：准备中和处理中按浏览器当前时间减去 `created_at`，每秒在本地更新，不额外请求接口；已完成和失败按接口返回的 `updated_at - created_at` 显示固定值。耗时不小于零，超过一天仍累计显示小时数；页面没有运行中任务或离开页面时停止计时器。

`notary-details` 仅接受网页登录会话，沿用任务访问权限。返回数据保留 Apple 字段：`archiveFilename`、`jobId`、`logFormatVersion`、`sha256`、`status`、`statusCode`、`statusSummary`、`uploadDate`、`issues` 和 `ticketContents`；票据包含 `path`、`arch`（Apple 未提供时为 null）、`cdhash` 和 `digestAlgorithm`，Apple 新增字段同样保留。数据以完整 JSON 存入已有 `notary_log`，无需迁移；重复票据也原样保存。缓存命中不调用 Apple，已有拒绝日志返回 `data: null`，异常缓存返回 500 且不覆盖；缓存缺失时使用任务锁并二次检查，Redis 不可用或租约丢失返回 503。此接口只补齐详情，不触发签名、staple 或上传，也不修改任务业务状态。

页面首次加载和状态刷新后按需请求详情，获取成功后停止重复请求。签名详情卡片位于处理日志上方，展示 Accepted 公证元信息、SHA-256、警告和文件包含关系拓扑。节点按完整路径合并；同一路径的多架构签名分别显示，完全重复的签名标注出现次数；CDHash 相同的不同路径保持独立。图中目录仅表示票据路径的层级，不代表 Apple 为每个目录签发了票据，也不代表归档内的完整文件清单。支持滚动浏览、鼠标和键盘选择节点；空票据显示空态。

历史列表和任务详情的下载按钮先请求 `artifact-url` 获取 CDN 地址，检查成功后才打开新标签页下载，无需前端获取文件 blob。等待期间只在原页面显示准备状态；查询失败时在当前页面显示错误，不创建或关闭标签页，原有任务和列表保持可见。文件不存在时显示“文件已过期或已被删除，请重新获取制品。”，其他异常显示接口错误；等待期间禁用重复点击，离开页面会取消等待，不再打开下载页。浏览器拦截异步打开的标签页时，在原页面提供“打开下载”链接，用户可手动在新标签页继续下载。

CDN 域名映射到 S3 桶根目录，配置方式 A 鉴权和与后端一致的密钥、参数名，透传对象的下载文件名元数据。每次下载前通过 S3 `HeadObject` 检查对象；对象不存在时返回 HTTP 410 和 `File expired or no longer available.`，S3 查询异常返回 HTTP 502。下载不触发任务刷新，也不依赖本地制品缓存。客户端脚本使用的 OpenAPI 下载接口仍返回 302。S3/CDN 配置入口见 [README](../README.md#配置)。

## 开发与验收

前端使用固定版本的 Biome 统一检查和格式化 JavaScript、TypeScript、TSX、JSON、CSS 与 HTML，保留 TypeScript 7。检查范围包含单元测试、浏览器测试源码及工具配置，排除依赖、构建产物、测试报告和自动生成的依赖锁文件。

`src/web/biome.json` 统一规定：2 空格缩进、LF 换行、80 列目标行宽、文件末尾换行、JS/TS 单引号、JSX 双引号、强制分号及箭头函数参数括号、多行结构尾逗号；对象和数组始终展开，多属性 JSX/HTML 每个属性单独一行，闭合尖括号单独换行，控制语句必须使用大括号。导入由 Biome 统一排序，格式化不读取个人 EditorConfig 设置；仓库 `.editorconfig` 同步编辑器的基本输入习惯。

```sh
npm --prefix src/web run format
```

格式不符合规范时 CI 会失败。可单独执行检查或自动修复：

```sh
npm --prefix src/web run lint
npm --prefix src/web run lint:fix
npm --prefix src/web run typecheck
npm --prefix src/web run format:check
```

`mise run lint` 包含前端代码、格式和导入排序检查；`mise run format-check` 可单独检查格式；`mise run check`（包括 CI）统一执行前端 `lint`，任何警告都会导致检查失败。`format` 只处理排版、大括号和导入排序；`lint:fix` 先统一格式，再应用其他规则的安全修复。

```sh
mise run setup
npm --prefix src/web run dev
mise run check
# 首次准备浏览器运行时；Linux 可增加 --with-deps：
cd src/web
npx playwright install chromium
npm run test:e2e
```

本地 HTTP 开发可使用 `FRONTEND_URL=http://localhost:5173`、`COOKIE_SECURE=false`，将 `public/config.js` 指向 `http://localhost:8000`，回调设置为 `http://localhost:8000/api/v1/auth/callback`，并不设置 Cookie Domain。通过 HTTPS 本地域名反代时，API 配置与回调都使用相同的 HTTPS API 域名并保留 Secure；不要再混用 localhost 入口。OIDC issuer 始终使用身份提供方的实际 issuer。

Playwright 自动构建静态产物，启动仅绑定 loopback 的临时测试后端和静态服务器；使用临时 SQLite、模拟 OIDC 提供方及模拟 Apple/rcodesign，验证浏览器登录、管理授权、Key 创建/吊销、ZIP 上传、下载、历史和深层路由。测试服务位于 tests，未打包进后端镜像。CI 同时对手动初始化的 MySQL 执行数据库集成测试，并输出 `web-dist` 静态产物。
