# 开发者接入文档

本服务提供 ZIP 上传签名、公证状态查询和 CDN 制品下载跳转。ZIP 中支持一个完整 `.app` 目录或一个 `.dmg` 文件。服务端不主动轮询；调用方在上传成功后主动查询，或直接使用配套脚本。最终 ZIP 成功上传 S3 后任务才完成。

## 接口地址与鉴权

业务接口根路径为 `/openapi/v1`，以下示例假设：

```sh
export API_BASE_URL='https://sign.example.com/openapi/v1'
export API_TOKEN='由管理员在网页签发的应用 Key'
```

`/openapi/v1` 下的所有接口，包括 OpenAPI schema，均携带：

```http
Authorization: Bearer <API_TOKEN>
```

调用方无需提供 Apple 凭据 JSON、p12 或 Apple 账号信息。这些凭据由服务部署者挂载到容器中，JSON 路径通过服务端 `APPLE_KEY_FILE` 配置。Token 缺失或不匹配返回 HTTP 401。

每个 Key 绑定一个应用，上传的任务自动归入该应用，无需再提交 identifier。查询和下载只能访问该应用的任务；其他应用的任务统一返回 404。Key 吊销或应用停用后返回 401。服务端不再支持固定环境变量 Token；脚本的 `API_TOKEN` 仅用于传入网页生成的客户端凭据，不是服务端 Key 配置。

## 健康检查

**`GET /health`** 无需认证，位于服务根路径，用于进程存活探测，不检查数据库或 Apple 服务状态。

**`GET /ready`** 无需认证，初始化完成且 MySQL、Redis 可用时返回 200 和 `{"status":"ok"}`，否则返回 503 和 `{"status":"unavailable"}`，用于发布平台就绪探测。探测及退出宽限配置见 [滚动发布文档](rolling-release.md)。

业务接口在 Redis 协调失败或任务失锁时返回 HTTP 503，沿用 `data` / `error_message` 响应结构；已知任务 ID 会保留在 `data` 中。保存该 ID，待服务恢复后查询现有任务，避免直接重新上传。

```sh
curl --fail --silent --show-error 'https://sign.example.com/health'
```

成功时返回 HTTP 200：

```json
{"status": "ok"}
```

## 响应与状态

业务 JSON 顶层只有 `data` 和 `error_message`。上传和查询的 HTTP **200** 表示本次操作成功，`error_message` 为空字符串；这些接口非 200 表示失败，原因在 `error_message`。下载接口成功返回 **302**，详见下载章节。新任务上传成功时已经提交 Apple，**不表示公证已经完成**；相同内容和文件名复用历史任务时，以返回的 `status` 为准。

任务数据固定包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 服务任务 UUID；后续查询、下载使用这个 ID |
| `status` | string | 下表中的一个英文状态 |
| `submission_id` | string / null | Apple submission ID；尚未创建时为空，不代替服务任务 ID |

| `status` | 含义 | 调用方行为 |
| --- | --- | --- |
| `preparing` | 接收、保存文件 | 等待后查询 |
| `processing` | 签名、提交、Apple 公证处理、staple 或 S3 上传中 | 等待后查询 |
| `completed` | Apple 公证通过、staple 成功且最终 ZIP 已上传 S3 | 请求下载跳转 |
| `failed` | 任务失败 | 停止自动轮询，查看错误原因 |

成功示例：

```json
{
  "data": {
    "id": "498bd264-2807-48c9-ad28-ccfe491b0ce6",
    "status": "processing",
    "submission_id": "1c14321c-5f4b-4e55-abec-73f869282581"
  },
  "error_message": ""
}
```

**查询到失败任务也返回 HTTP 500**，同时保留任务信息：

```json
{
  "data": {
    "id": "498bd264-2807-48c9-ad28-ccfe491b0ce6",
    "status": "failed",
    "submission_id": "1c14321c-5f4b-4e55-abec-73f869282581"
  },
  "error_message": "Apple notarization Invalid. The executable does not have the hardened runtime enabled."
}
```

尚无任务信息时，错误响应的 `data` 为 `null`。不要仅解析 HTTP 200 的响应体；HTTP 500 的 JSON 中可能包含需要保留的任务 ID。

## 上传、签名与提交

**`POST /openapi/v1/jobs`**

- 请求类型：`multipart/form-data`。
- 必填字段：`file`，一个 ZIP 文件，内部包含一个顶层 `.app` 目录或 `.dmg` 文件。
- 不接收工具命令、凭据、任意服务端路径或额外签名参数。
- 新任务同步执行文件保存、签名和提交 Apple，成功返回 HTTP 200 和 `processing` 状态。
- 此请求不等待 Apple 公证结果，也不触发公证状态查询。

服务在 Key 绑定的应用内按上传内容计算 SHA-256。若存在相同 hash、文件名完全相同且状态为 `preparing`、`processing` 或 `completed` 的历史任务，直接返回该任务，HTTP 仍为 200，不再签名或提交；只有匹配任务全部为 `failed`（或没有历史任务）才新建。文件名比较区分大小写、重音和首尾空格。

因此重复上传可能返回 `preparing` 或直接返回 `completed`，任务 ID 与历史请求相同；文件名不同但内容相同会新建任务，分别保留各自的文件名。返回 `completed` 时可直接下载，其他运行状态按下文轮询。原任务仍在签名时，重复上传不会等待它执行完成。并发上传相同内容和文件名由服务端合并。

原件、签名 ZIP、staple 工作副本、最终 ZIP 及下载附件均保留上传的 ZIP 文件名。仅移除文件名中的客户端路径部分，不自动删除空格或点号，不替换字符或截短名称。名称必须以 `.zip` 结尾（后缀不区分大小写），且包含匹配 `\d+\.\d+\.\d+` 的版本号；`v` 前缀可选，预发布等后缀不限。缺少版本号、空文件名、`.`、`..`、控制字符和超过 255 个 UTF-8 字节的名称返回 HTTP 422，不创建任务。

```sh
curl --silent --show-error \
  --header "Authorization: Bearer $API_TOKEN" \
  --form 'file=@./MyApp-v1.2.3.zip' \
  --output upload-response.json \
  --write-out 'HTTP %{http_code}\n' \
  "$API_BASE_URL/jobs"
cat upload-response.json
JOB_ID=$(jq -r '.data.id // empty' upload-response.json)
```

实际程序应检查 HTTP 状态码后再进入轮询；上面的命令仅展示单个接口调用。

**只接受 ZIP**，服务校验文件名后缀、ZIP 文件头及压缩包结构；不能直接上传 `.app`、`.dmg` 或将其改名为 `.zip`。格式不符返回 HTTP 422，`data=null`，并在 `error_message` 中提示先打包 ZIP，不创建任务。空文件、损坏的 ZIP、无支持的内容或多个顶层 App/DMG 同样被拒绝。ZIP 大小和条目数还受服务端 `MAX_UPLOAD_BYTES`、`MAX_EXTRACTED_BYTES` 和 `MAX_ARCHIVE_ENTRIES` 限制；普通成员的 CRC 错误在解包阶段报告为任务失败。

### 打包 App 或 DMG

先在 macOS 上打包，保留 App 顶层目录、执行权限和符号链接，再上传 ZIP：

```sh
ditto -c -k --keepParent \
  './MyApp.app' \
  './MyApp-v1.2.3.zip'

curl --silent --show-error \
  --header "Authorization: Bearer $API_TOKEN" \
  --form 'file=@"./MyApp-v1.2.3.zip"' \
  --output upload-response.json \
  --write-out 'HTTP %{http_code}\n' \
  "$API_BASE_URL/jobs"
```

使用 `--form` 时让 curl 自动生成 `Content-Type` 和 multipart boundary，不要手动写入 `boundary=<calculated when request is sent>`。

DMG 也必须先打包为 ZIP，上传方式相同：

```sh
ditto -c -k './MyApp.dmg' './MyApp-v1.2.3.zip'
```

ZIP 文件名可以是 `DEEIX Chat-1.2.3.zip`、`MyApp-v1.2.3-alpha.1.zip`、`MyApp-v1.2.3-beta.2.zip` 等，只检查是否包含三段数字版本号，不限制预发布等后缀。内部必须只有一个顶层 `.app` 目录或一个顶层 `.dmg` 文件，不能额外套一层文件夹，内部 App/DMG 名称不要求包含版本号。App 的 `Info.plist` 中声明的主程序必须存在且保留执行权限。服务清理顶层 `__MACOSX`、`.DS_Store` 及带 AppleDouble 文件头的 `._*` 元数据（不保留这些扩展属性），保留 App 内部文件及相对符号链接；拒绝不安全路径、循环/包外/悬空链接、特殊文件、加密或非 stored/deflate 压缩的 ZIP。

服务端依次解包、签名 App/DMG、重新打包 ZIP 并提交 Apple。公证通过后，对已签名 ZIP 的临时副本中的 App/DMG 执行 staple，再打包供下载；最终产物仍为 ZIP，文件名与上传一致，解压后得到已签名且附带公证票据的 App/DMG。失败的 staple 可用同一任务重试，上传原件和已提交 ZIP 不会被修改。

配套快捷脚本直接接受原始 `.app` 或 `.dmg`，自动完成打包 ZIP、上传、轮询、下载和解压；不要把 ZIP 传给脚本：

```sh
bash support-files/scripts/sign-and-notarize.sh \
  './DEEIX Chat-v1.2.3.app' './DEEIX Chat-notarized.app'
```

DMG ZIP 只处理 DMG 外层，不挂载或修改 DMG 内部内容，不会为其中的 App 补签。DMG 内 App 应先完成代码签名，再制作 DMG 并按上文打包上传。

上传可能持续较长时间。签名、提交默认各允许 1800 秒，客户端和代理超时还需要覆盖文件上传耗时。网络断开或客户端超时不代表服务端没有提交；保存已获得的任务 ID，不要自动重传。若没有拿到 ID，由部署者通过数据库和诊断日志确认任务。

## 查询状态与触发 staple

**`GET /openapi/v1/jobs/{id}`**

`id` 为上传返回的服务任务 UUID。已有 Apple submission ID 时，每次调用向 Apple 查询一次；未取得 submission ID 的任务只读取本地状态。

```sh
curl --silent --show-error \
  --header "Authorization: Bearer $API_TOKEN" \
  --output query-response.json \
  --write-out 'HTTP %{http_code}\n' \
  "$API_BASE_URL/jobs/$JOB_ID"
cat query-response.json
```

若 Apple 仍处理中，返回 `processing`。若 Apple 接受公证，服务端在本次查询请求内执行 staple，再上传最终 ZIP 至 S3；上传成功并保存对象 key 后返回 `completed`。查询耗时还包含 S3 分片上传，默认 staple 最长 120 秒。并发查询同一任务会串行处理，不重复 staple 或上传已完成的产物。

Apple 拒绝公证、staple 失败或 S3 上传失败时返回 HTTP 500、`status=failed` 和原因。Apple 网络异常或未知响应返回 HTTP 502，保留数据库中此前的任务状态；这种响应中的 `data.status` 可能仍是 `processing`。

staple 失败后，可以由开发者确认原因，再显式调用查询接口重试：服务端重新查询 Apple，并从原提交文件的副本执行 staple，不会重新签名或提交公证。配套脚本遇到失败会退出，不会自动执行这种重试。

S3 上传失败同样可显式查询重试。服务校验已保存本地制品的 SHA-256，通过时仅重传 S3；本地制品缺失或损坏时才重新 staple。上传成功后数据库提交失败也可查询同一任务恢复，不要重新提交原文件。

## 下载产物

**`GET /openapi/v1/jobs/{id}/artifact`**

仅当任务为 `completed` 且 S3 `HeadObject` 确认已保存的对象 key 仍存在时返回 HTTP **302**，`Location` 为本次下载新生成的鉴权 CDN URL，并返回 `Cache-Control: no-store`。API 不返回 ZIP 内容，也不触发 Apple 查询或检查本地制品。客户端应跟随 `Location` 下载；最终文件响应、Range 支持由 CDN 负责，对象元数据保留原始 ZIP 下载文件名。

CDN 默认参数为 `eo_sign=timestamp-rand-0-md5hash`。签名按 EdgeOne 方式 A 计算：`MD5(Path-timestamp-rand-0-密钥)`；Path 为最终 URL 中以 `/` 开头的百分号编码路径。时间戳为签发时的 Unix 秒，有效期由 CDN 配置。链接不入库，每次请求都会生成新的随机串。

```sh
REDIRECT=$(curl --silent --show-error \
  --header "Authorization: Bearer $API_TOKEN" \
  --output download-response.json \
  --write-out '%{http_code}\n%{redirect_url}' \
  "$API_BASE_URL/jobs/$JOB_ID/artifact") || exit 1
[[ "${REDIRECT%%$'\n'*}" == 302 ]] || { cat download-response.json; exit 1; }
CDN_URL="${REDIRECT#*$'\n'}"
# 独立请求 CDN，不携带 API Bearer。
curl --fail --silent --show-error --location --max-redirs 5 \
  --proto '=http,https' --proto-redir '=http,https' \
  --output MyApp-notarized.dmg.zip.part -- "$CDN_URL" &&
  mv MyApp-notarized.dmg.zip.part MyApp-notarized.dmg.zip
```

尚未完成时返回 HTTP 409 和 JSON 错误；失败任务返回 HTTP 500。S3 对象不存在时返回 HTTP 410，`error_message` 为 `File expired or no longer available.`；S3 权限、连接或其他查询异常返回 HTTP 502。下载检查不改变任务完成状态。请勿把错误 JSON 保存成正式制品。配套脚本已处理临时文件、状态码校验、下载失败清理和原子发布。

CDN 返回 403（例如链接过期或鉴权不匹配）、404 或网络错误时下载失败；CDN 错误不使用本服务的 JSON envelope。可以重新请求 API 获取新链接，不要缓存 302 或将 API Bearer 转发到 CDN。

## 推荐轮询流程

1. 上传文件，检查 HTTP 200，保存服务任务 ID。
2. 若上传已返回 `completed`，直接下载；否则立即调用查询接口。
3. HTTP 200 且状态为 `preparing` 或 `processing`：等待 **5 秒**，再查询。
4. HTTP 200 且状态为 `completed`：请求下载接口，取得 HTTP 302 后独立下载 CDN 文件；最终 HTTP 200 且传输完整结束后视为成功。
5. 上传/查询非 200、`failed`、下载跳转或 CDN 错误、网络错误或不认识的状态：停止自动流程，保留任务 ID 并报告错误。

两次查询之间的等待从前一次响应结束后开始。没有服务端定时任务或回调；不调用查询接口时，服务不会主动获取 Apple 结果或 staple。客户端关闭后，可以凭任务 ID 稍后手动继续查询。

## 一条命令完成打包、上传、轮询与解压

脚本会在启动时校验系统，仅允许在 macOS 上运行。准备 Bash、curl 7.71+、jq、Python 3.9+（仅使用标准库）和系统 `ditto`，并保留脚本同目录下的 `archive-helper.py`，或配置 `RESULT_HELPER` / `RESULT_HELPER_URL`，然后执行：

```sh
export API_BASE_URL='https://sign.example.com/openapi/v1'
export API_TOKEN='由管理员在网页签发的应用 Key'
bash support-files/scripts/sign-and-notarize.sh \
  ./MyApp-v1.2.3.dmg \
  ./MyApp-notarized.dmg

# App 目录的用法相同，输出也是可直接使用的 App 目录。
bash support-files/scripts/sign-and-notarize.sh \
  './My App-v1.2.3.app' \
  './My App-notarized.app'
```

脚本只接受 `.app` 目录或非空 `.dmg` 普通文件，后缀不区分大小写，不接受 ZIP 或输入符号链接。ZIP 名称取自输入文件名，输入名称必须包含匹配 `\d+\.\d+\.\d+` 的版本号，否则在打包和上传前报错；`v` 前缀可选，预发布等后缀不限。脚本自动打包一个顶层 App/DMG，保留可执行权限、内部相对符号链接及中文文件名；原始输入不变。上传后打印任务 ID，默认每 5 秒查询一次，完成后下载 ZIP，校验内容并自动解压，将 App/DMG 保存到第二个参数指定的路径。

| 配置 | 默认值 / 行为 |
| --- | --- |
| `API_BASE_URL` | `http://localhost:8000/openapi/v1`，必须包含接口前缀 |
| `API_TOKEN` | 必填 |
| `POLL_INTERVAL` | `5` 秒，可配置正整数或小数 |
| `REQUEST_TIMEOUT` | `0`，curl 单次请求不设置总时限；连接超时为 10 秒 |
| `RESULT_HELPER` | 脚本同目录下的 `archive-helper.py`，可指定其他本地 helper 路径 |
| `RESULT_HELPER_URL` | 默认未配置；仅在 `RESULT_HELPER` 文件不存在时下载到临时目录，支持 HTTP 重定向；下载失败或内容为空时退出 |
| 输入路径 | 第一个参数，必须是可读的 `.app` 目录或可读、非空的 `.dmg` 普通文件 |
| 输出路径 | 第二个参数，后缀需与输入类型一致（`.app` / `.dmg`）；父目录必须存在，目标必须尚不存在，且不能在输入 App 内部 |

例如改为 10 秒查询一次：

```sh
POLL_INTERVAL=10 bash support-files/scripts/sign-and-notarize.sh ./MyApp-v1.2.3.dmg ./result.dmg
```

若只分发了 Shell 脚本，可配置 helper 下载地址：

```sh
RESULT_HELPER_URL='https://sign.example.com/scripts/archive-helper.py' \
  bash support-files/scripts/sign-and-notarize.sh ./MyApp-v1.2.3.dmg ./result.dmg
```

下载的 helper 由 Python 执行，地址应指向可信的 `archive-helper.py`。下载沿用 `REQUEST_TIMEOUT`，不会携带 API Bearer Token；本地 helper 存在时不会发起下载。

所有 HTTP 请求（helper 下载、创建 job、任务查询、获取产物下载地址和 CDN 下载）均启用重试：网络或传输错误（包括连接中断、下载不完整），以及 curl 支持重试的临时 HTTP 错误（如 408、429、500、502、503、504），最多额外重试 3 次，每次间隔 2 秒；各次请求沿用 `REQUEST_TIMEOUT`。helper 下载的其他 HTTP 错误也最多重试 3 次。创建 job 重试会重复上传同一个 ZIP，服务端根据文件内容 SHA-256 和文件名复用任务。

脚本成功退出码为 `0`；接口失败、异常响应或网络错误以非零退出，打印可用的错误详情及任务 ID。Ctrl-C 退出码为 `130`，仅停止客户端，不取消服务端任务。

默认没有总轮询次数限制，会持续到完成或失败。脚本不覆盖已有输出；下载的 helper、上传 ZIP、下载 ZIP 和解压内容都位于输出父目录中的临时目录。下载先由 API 返回 302，再独立请求 CDN（最多跟随 5 次 HTTP(S) 重定向），不携带 API Bearer。只有 CDN 最终 HTTP 200、下载完整、ZIP 内容符合预期且解压成功后，才原子发布 App/DMG；即使目标在等待期间被其他进程创建，也会失败而不会覆盖或合并。完成、失败或中断时均清理临时文件，失败时保留任务 ID，便于后续排查。

## HTTP 错误速查

| HTTP 状态码 | 含义 | 建议处理 |
| --- | --- | --- |
| `302` | 下载成功，Location 为新的 CDN 鉴权 URL | 独立请求 CDN，不转发 API Bearer |
| `401` | Bearer Token 缺失或错误 | 检查 Token |
| `404` | 服务任务不存在 | 检查是否误用了 Apple submission ID |
| `409` | 产物尚未就绪 | 先调用查询接口 |
| `410` | 制品已过期或已从 S3 删除 | 文件已不可下载，重新获取制品 |
| `422` | 字段缺失、UUID 格式错误、非 ZIP 上传、ZIP 文件名缺少版本号、空文件、ZIP 结构或大小不合法 | 修正请求；App 或 DMG 先按上文打包 ZIP，文件名包含三段数字版本号 |
| `500` | 任务失败或服务内部错误 | 读取 `error_message`，保留 `data.id`；必要时查看 DB |
| `502` | Apple 或 S3 查询失败或返回异常数据 | 停止当前自动流程，稍后凭原任务 ID 重试 |

OpenAPI schema 获取方式：

```sh
curl --silent --show-error \
  --header "Authorization: Bearer $API_TOKEN" \
  "$API_BASE_URL/openapi.json" > openapi.json
```

schema 返回标准 OpenAPI 文档，不使用业务 JSON 信封。服务不提供 Swagger UI、登录或管理页面。
