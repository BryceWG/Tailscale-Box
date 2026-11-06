# Tailscale Box 1.1.2 (ARM64)

Android Root 模块，使用原生 tailscale0 TUN：应用 → 系统路由 → tailscaled → 对端。
使用 tailscaled 用户态 WireGuard，不使用 Android VPNService。仅支持 ARM64。

## 安装与两种包

GitHub Release 提供以下两种 ZIP，均可由模块管理器直接安装：

| 包 | 内容 | 安装后操作 |
| --- | --- | --- |
| `tailscale-box-版本-minimal-arm64.zip` | 模块脚本和 WebUI，不含 Tailscale | 点击 Action 或网页「安装 / 更新」，再启动和登录 |
| `tailscale-box-版本-bundled-arm64.zip` | 模块与构建时官方 stable ARM64 二进制 | 重启后进入网页登录 |

安装前停用 Tailscale App VPN。若有旧 Magisk-Tailscaled，卸载并重启，检查其 service.d 遗留脚本；本模块不会自动迁移旧身份。模块升级时安装 minimal 包会保留本模块现有二进制。卸载保留 `/data/adb/tailscale-direct` 下的身份和配置，确认不用后再手动删除。

首次从空包装好 Tailscale 后，点击网页「启动服务」或重启手机。更新 Action 更新的是 Tailscale 二进制，模块本身升级需安装新 ZIP。

## 管理器网页

入口为 `webroot/index.html`，在 KernelSU / APatch 等兼容管理器里打开模块的 WebUI。
官方 Magisk 管理器没有原生 WebUI 入口；Magisk 用户需使用支持 KernelSU exec 桥接的 WebUI 宿主（如兼容的 WebUI X），或使用下方命令行。宿主兼容性需实机验证。
[KernelSU WebUI 文档](https://kernelsu.org/guide/module-webui.html)。

页面由 Pi 设计，资源随模块提供，无 CDN。通过管理器 root exec 桥接调用 `webui.sh` 的固定操作，不开放 HTTP 服务端口。

页面支持查看当前版本、服务/登录状态、Tailnet IP、设备与 Peer 在线状态、直连/中继信息，以及 `tailscale netcheck` 的 UDP、NAT 和 DERP 区域延迟。延迟是设备到中继区域的测量，并非每台 Peer 的端到端延迟；没有活动连接的 Peer 可能没有直连信息。

版本旁的「检查更新」只查询官方 stable ARM64 版本，不下载或替换二进制，支持已有的本地 control-proxy 配置。设备行的 Ping 按钮持续发起单次 Tailscale Ping，显示响应延迟、平均值和丢失统计；中继响应也计为成功。停止按钮或离开页面后不再发起下一次请求。

桥接新增 `webui.sh check-update`，返回 `{current, latest, available, output, exitCode}`；空安装的 current 为空，查询失败的 latest 为空。`webui.sh ping <ip>` 返回 `{output, exitCode}`，执行 `tailscale ping --c 1 --timeout=3s --until-direct=false <ip>`，外层限制 6 秒。两者均不占用全局操作锁，Ping 参数只接受 IP 字符并作为单一参数传递，实际地址有效性由 Tailscale 校验。

登录流程为：启动 → 登录 → 打开页面返回的 Tailscale 授权链接 → 浏览器完成授权 → 返回刷新，必要时点击修复路由。授权链接通过 Tailscale CLI 获取，最长约 60 秒返回；登录等待超时仍可能已生成有效链接。不要分享该链接。

网页未运行在 root 宿主中时会显示连接提示。启动、停止、修复和安装更新均调用现有控制脚本。长操作期间不要重复点击或同时使用其他管理器发起更新。

## 命令行

Root shell：

```sh
/data/adb/modules/tailscale_direct/ctl.sh start
/data/adb/modules/tailscale_direct/ctl.sh login
/data/adb/modules/tailscale_direct/ctl.sh status
/data/adb/modules/tailscale_direct/ctl.sh repair
/data/adb/modules/tailscale_direct/ctl.sh ts ping 对端IP
/data/adb/modules/tailscale_direct/update.sh
```

首次登录后才有 native route。普通应用仍需实测 HTTP/SSH，tailscale ping 成功不能单独证明系统路由正常。

## 路由与代理共存

模块只添加 priority 70、目的 `100.64.0.0/10` 和 `fd7a:115c:a1e0::/48` → table 52 的规则。
必须已有 tailscaled 原生路由，priority 70 被其他规则占用会报错。登录固定 `accept-dns=false`、`accept-routes=false`、`netfilter-mode=off`，不修改系统 DNS、SELinux、fwmark、默认路由或 Box 链。

TanakaLun cgroup eBPF：保持 `bypass-private-address: true`，Tailnet 地址不要配置 bypass-exclude，使其进入系统路由。按实际进程识别添加 `PROCESS-NAME,tailscaled,DIRECT`；不要排除整个 UID 0。

Box TPROXY：核实规则旁路 Tailnet 网段，避免二次截获。仅配置 mihomo DIRECT 不足以保证。priority 70 通常在 Box priority 100 前，以设备实际 `ip rule` 为准。

Linux 静态二进制没有 Android resolver 接口。需要控制面代理时，可写 `/data/adb/tailscale-direct/control-proxy`，内容为 `http://127.0.0.1:7890`（换成实际 HTTP/mixed 端口），然后重启。仅支持本地无认证 HTTP 代理。该环境变量不替代 P2P UDP，不保证所有 DERP/DNS 请求经过代理。

## 更新与排错

Action 调用 `update.sh`，从官方 stable API 获取 ARM64 归档，校验 SHA256，测试候选二进制，再备份现有文件和身份后替换。没有安装 Tailscale 时也能运行。
原来在运行的服务会重启；原来停止的服务保持停止。替换或新版本启动失败时恢复旧二进制。
身份保留在原路径；备份位于数据目录的 `backup-*`。安装来源写入 `installed-provenance.txt`。

`ctl.sh diagnose` 提供状态、netcheck 和 daemon 日志。服务日志在 `/data/adb/tailscale-direct/daemon.log`；Action 日志为 `action.log`。不要公开 `tailscaled.state`、授权链接或身份凭证。

## 构建与发布

```sh
uv run --no-project python scripts/build.py --variant both
uv run --no-project python scripts/build.py --variant minimal
uv run --no-project python -m unittest discover -s tests
```

输出在 `dist/`。bundled 在每次构建时请求[官方 stable 元数据](https://pkgs.tailscale.com/stable/?mode=json)，不复用仓库里旧的 `bin/`；校验官方 SHA256，ZIP 的 `PROVENANCE.txt` 记录版本、来源与哈希。minimal 不包含仓库现有二进制。

`.github/workflows/build.yml` 在 push 改动 `module.prop` 时比较推送前后的 `version` 和 `versionCode`，任一变化才打包。请发布时同时递增两者。首次推送也构建；仅改描述不构建。支持 workflow_dispatch 手动构建，全部运行提供 Actions artifact；默认分支版本更新还会创建 `v版本` Release，附双 ZIP 与 SHA256SUMS。同名 Release 已存在会报错，发布必须使用新版本号。

构建按包类型写入 `module.prop` 的 `updateJson`：minimal 指向最新 Release 的 `update.json`，bundled 指向 `update-bundled.json`。两个 JSON 的 `zipUrl` 分别指向同一版本对应类型的 ARM64 ZIP，因此管理器更新会保持包类型。minimal 升级保留已安装的 Tailscale 二进制，bundled 使用新包附带的版本；两者均保留身份数据。更新检查使用 versionCode 比较，发版需递增。手动 workflow_dispatch 只构建 artifact，不发布更新源。

首次使用需初始化 Git、推送到 GitHub，并在仓库设置中允许 Actions 发布 Release（contents: write）。本地任务没有自动添加远程地址或推送。

## 验证范围与限制

旧版本有用户在一台 Android 16 ARM64、mihomo TPROXY 场景报告联网正常。本次构建/WebUI/更新改动还需要 Android 实机验证，主机测试不能验证管理器桥接、TUN、SELinux、熄屏和网络切换。

不支持自动子网路由、MagicDNS、Exit Node、手机网关或无限保活。异常退出用 restart；控制服务器可达也不能保证端到端路径与省电表现。遇错检查 diagnose，不要关闭 SELinux 或卸载 netd BPF。

## 来源与许可证

脚本为本项目实现，WebUI 由 Pi 设计。新 bundled 包使用官方 Linux 静态 Tailscale ARM64 发行归档；随包 `PROVENANCE.txt` 为该包实际来源。工作目录原有 `bin/` 的历史来源仍记录于仓库 `PROVENANCE.txt`，构建不将它们作为最新版本。
Tailscale 及第三方组件许可证见 `LICENSE.tailscale`、`LICENSE.reference` 与上游发行内容。
