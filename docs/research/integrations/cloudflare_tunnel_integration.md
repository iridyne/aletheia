# Cloudflare Tunnel 集成调研

> 状态（2026-06-30 更新）：**功能完整，默认禁用，需手动配置后才会激活。**
> 原"暂不实施"描述已过期——进程管理、CLI、API 端点、lifecycle 联动均已完整实现。
> 用户无需做任何代码改动，按下方"用户配置流程"完成 cloudflared 注册后即可使用。

## 结论

最少 3 步手动操作无法省略：安装 cloudflared → `tunnel login` → 创建 tunnel + DNS。
之后 `aletheia tunnel setup` 可一键完成剩余配置。

## 已实现的代码模块

- `core/aletheia/config/schema.py` — `TunnelConfig`（enabled/tunnel_id/hostname/credentials_path/cloudflared_path/metrics_port）
- `core/aletheia/gateway/tunnel.py` — 进程管理（start/stop/status/setup），pidfile 跟踪
- `core/aletheia/cli/tunnel.py` — CLI 命令（setup/start/stop/status/enable/disable）
- `core/aletheia/gateway/lifecycle.py` — gateway start/stop 自动联动 tunnel
- `core/aletheia/server/routes/settings.py` — API 端点 `/tunnel/status`、`/tunnel/start`、`/tunnel/stop`

## 关键设计决策（参考 hAPI tiann/hapi）

1. **`--protocol http2`**：避免 SSE/长连接在 QUIC 上超时（hAPI 文档明确指出）
2. **容错启动**：tunnel 失败不阻塞 gateway（"Hub continuing without tunnel" 模式）
3. **逆序关闭**：stop 时先关 tunnel 再关 gateway
4. **Named Tunnel 必需**：Quick Tunnel (trycloudflare.com) 不支持 SSE，URL 每次变
5. **认证用 Cloudflare Access**：Zero Trust 在边缘做身份验证，应用层不改

## 安全注意事项

> 2026-10-09 更新：下述鉴权策略按 `core/aletheia/server/app.py` 的 `_tunnel_auth_guard` 中间件转述。

鉴权分三种模式：

- **`tunnel.enabled=True`，或 `remote_control.enabled=True` 且 `require_token=True`**：除匿名白名单
  `_PUBLIC_PATHS`（`/api/ready`、`/_infra/livez`、`/_infra/readyz`）外，所有 `/api/*` 与
  `/_infra/*` 路由（包括读取）都要求有效的 `X-Aletheia-Token` owner 头；缺失时 owner 路由
  fail closed。受监管的 Operator Shell 可以用独立的 service credential 认证，但只限
  `server/operator_service_auth.py` 的路由/方法白名单，且不会获得 owner 权限。
- **Remote Control 开启但未开 `require_token`**：私网 API 访问不鉴权（MVP 便利）。
- **两者都关闭**：不鉴权，保持本地优先。

期望的 token 值优先取 secret `remote_control_api_token`，未配置时退回 `tunnel_api_token`：

```sh
aletheia secrets set remote_control_api_token <强随机字符串>
```

然后在 Cloudflare Access 策略里将同一 token 配置为允许通过的 Service Token，
或者在调用端的请求头里加 `X-Aletheia-Token: <token>`。

## 用户配置流程

> 下列 PowerShell / `winget` 步骤只适用于 Windows；Linux 上用发行版包管理器安装 `cloudflared`，其余 `cloudflared` 命令相同。

```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel login                    # 浏览器授权
cloudflared tunnel create aletheia-os       # 得到 tunnel ID
cloudflared tunnel route dns aletheia-os aletheia.yourdomain.com
aletheia tunnel setup --tunnel-id <ID> --hostname aletheia.yourdomain.com
aletheia gateway start                      # tunnel 自动随 gateway 启动
```

## 打包方案（如果将来要做即装即用）

hAPI 把自研 tunwg 二进制打包在 `~/.hapi/runtime/<version>/tools/tunwg/` 下，
按 platform+arch 分发。Aletheia 如果走同样路线需要 bundle cloudflared (~30MB)，
且仍需用户有 CF 账号。ROI 不高，暂搁置。

## 参考

- hAPI tunnel 源码：`github.com/tiann/hapi` → `hub/src/tunnel/tunnelManager.ts`
- Cloudflare Named Tunnel 文档：https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
