# 多副本与滚动发布

所有副本必须共用同一 MySQL 数据库、同一 Redis 逻辑 DB，以及挂载到 `DATA_DIR` 的 ReadWriteMany PVC。保持凭据、OIDC、Cookie 与分发配置一致。每个容器继续运行一个 Uvicorn worker，上传仍同步签名和提交，任务状态和登录会话继续保存在 MySQL，无需数据库结构升级。

## Redis 连接与 DB

只配置 `REDIS_URL`，由 redis-py 原生解析连接、认证、TLS 与逻辑 DB：

```dotenv
# DB 3，密码包含特殊字符时先进行 URL 编码。
REDIS_URL=redis://:password@redis.example.internal:6379/3
# ACL 用户和 TLS 示例：
# REDIS_URL=rediss://signer:encoded-password@redis.example.internal:6380/3
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=3700
```

省略 DB 使用 0；也支持 `?db=3`，查询参数中的 DB 优先于 URL 路径，详见 [redis-py 连接文档](https://redis.readthedocs.io/en/stable/connections.html#redis.Redis.from_url)。没有独立的 `REDIS_DB` 环境变量。该连接需支持 `SELECT`、`PING`、锁使用的读写及 Lua 脚本命令；需要非零 DB 时，使用支持逻辑 DB 的 Redis 接入地址。

Redis 用于跨实例任务互斥，key 前缀固定为 `apple-remote-sign:lock:`。同一套服务的所有版本使用相同 DB；不同环境使用不同 DB 或 Redis 实例。滚动期间保持 Redis 接入地址和 DB 不变。

锁租期为 60 秒，持有期间每 20 秒校验令牌并续租；释放也验证令牌。Redis 连接默认超时和读写超时均为 3 秒，命令不自动重放。Redis 不可用时启动失败，运行期间协调失败或失锁返回 HTTP 503；已创建任务时，响应 `data` 保留任务 ID。保留该 ID，待服务恢复后查询，避免直接重新上传。执行中的 rcodesign 会响应失锁退出，后续步骤和任务状态写回停止。

后台每 30 秒检查未保存 submission ID 的未完成任务，取得对应任务锁并重新核对数据库状态后才标记中断；仍被其他副本持锁的任务保持原状。实例异常退出后，其租约过期，后续巡检再处理遗留记录。已保存 submission ID 的任务保留原有查询流程，服务不自动重复提交 Apple。

## 平台探测与退出宽限

- 存活探测：`GET /health`，不检查外部依赖。
- 就绪探测：`GET /ready`，初始化完成且 MySQL、Redis 可用才返回 200，否则为 503；无需认证，不检查 Apple 或 S3。
- 发出 SIGTERM 后，Uvicorn 停止接收新连接，在配置的退出宽限内等待在途请求完成。锁续租保持到请求离开任务处理作用域，随后停止恢复线程并关闭依赖连接。

将以下字段合并到现有 Kubernetes Deployment；镜像、Secret、Service、Ingress 与 PVC 沿用现有部署配置：

```yaml
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  template:
    spec:
      terminationGracePeriodSeconds: 3900
      containers:
        - name: signer
          env:
            - name: GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
              value: "3700"
          startupProbe:
            httpGet:
              path: /ready
              port: 8000
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 60
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            periodSeconds: 5
            timeoutSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 10
            timeoutSeconds: 2
          lifecycle:
            preStop:
              exec:
                command: ["/bin/sh", "-c", "sleep 10"]
```

Pod 终止时，平台先移除就绪路由，`preStop` 的 10 秒用于等待流量摘除传播，随后向进程发送 SIGTERM。Kubernetes 的终止宽限从 `preStop` 执行前开始计时，默认 3900 秒覆盖 10 秒缓冲、3700 秒请求排空及清理余量。若提高命令或退出超时，应同步增加平台宽限；Ingress、负载均衡与客户端超时也应覆盖上传、签名及提交耗时。超过平台宽限仍会被强制终止。

## 首次迁移与后续发布

当前旧版只有进程内任务锁，无法识别新版 Redis 租约。首次升级先从入口摘除旧实例流量，等待已接受的上传、刷新等请求完成，再停止旧版并启动新版；该次切换需要排空窗口。完成迁移后，新版副本可以重叠运行，由平台在新副本就绪后终止旧副本。

滚动结束后，`/ready` 应保持 200，旧副本在在途请求完成后退出；任务 ID、submission ID、历史记录、会话与下载地址沿用原有行为。回滚到无 Redis 协调的旧版也需要先排空所有新版副本。
