# Cloudflare Tunnel 集成调研

> 状态（2026-06-30 更新）：**功能完整，默认禁用，需手动配置后才会激活。**
> 原"暂不实施"描述已过期——进程管理、CLI、API 端点、lifecycle 联动均已完整实现。
> 用户无需做任何代码改动，按下方"用户配置流程"完成 cloudflared 注册后即可使用。

## 结论

最少 3 步手动操作无法省略：安装 cloudflared → `tunnel login` → 创建 tunnel + DNS。
之后 `aletheia tunnel setup` 可一键完成剩余配置。

## 已实现的代码模块

- `aletheia/config/schema.py` — `TunnelConfig`（enabled/tunnel_id/hostname/credentials_path/cloudflared_path/metrics_port）
- `aletheia/gateway/tunnel.py` — 进程管理（start/stop/status/setup），pidfile 跟踪
- `aletheia/cli/tunnel.py` — CLI 命令（setup/start/stop/status/enable/disable）
- `aletheia/gateway/lifecycle.py` — gateway start/stop 自动联动 tunnel
- `aletheia/server/routes/settings.py` — API 端点 `/tunnel/status`、`/tunnel/start`、`/tunnel/stop`

## 关键设计决策（参考 hAPI tiann/hapi）

1. **`--protocol http2`**：避免 SSE/长连接在 QUIC 上超时（hAPI 文档明确指出）
2. **容错启动**：tunnel 失败不阻塞 gateway（"Hub continuing without tunnel" 模式）
3. **逆序关闭**：stop 时先关 tunnel 再关 gateway
4. **Named Tunnel 必需**：Quick Tunnel (trycloudflare.com) 不支持 SSE，URL 每次变
5. **认证用 Cloudflare Access**：Zero Trust 在边缘做身份验证，应用层不改

## 安全注意事项

启用 tunnel（`enabled=True`）后，必须同时配置 `tunnel_api_token` secret，否则 gateway
会对 `/api/settings/live-unlock` 和 `/api/risk/kill-switch` 等 trust-boundary 端点返回 401，
拒绝所有请求（fail-closed 设计）。配置方式：

```powershell
aletheia secrets set tunnel_api_token <强随机字符串>
```

然后在 Cloudflare Access 策略里将同一 token 配置为允许通过的 Service Token，
或者在调用端的请求头里加 `X-Aletheia-Token: <token>`。

## 用户配置流程

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
