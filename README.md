# Clash Traffic Audit

轻量的 **Clash Verge / Mihomo 本地流量审计**：按域名、出口节点和进程查看上下行排行，保留一个月以上的历史，并显示无法归属的流量差额。

Python 标准库实现，无第三方 Python 依赖。当前支持 macOS 上的 Clash Verge Rev / Mihomo Unix 控制接口，提供仅监听本机的中文网页。

## 功能

- 每秒读取活动连接，按天累计上下行，每分钟批量写入 SQLite。
- 今日、昨日、近 7 天、近 30 天和全部保留数据的排行。
- 区分代理、经 Clash 处理的直连、阻止和未知路径。
- 独立显示内核总量、已归属流量、未归属差额与覆盖率。
- 每日聚合保留 **35 个日历日（含今天）**，小时总量保留 7 天。
- 数据库、回滚日志和应用日志的设计预算低于 **128 MiB**。
- 用户级 macOS LaunchAgent，登录后自启；支持 CSV 导出和原子检查点恢复。

**这不是精确账单系统。** 内核提供的是活动连接快照，已结束的短连接可能漏记细分；差额会保留为“未归属”，不会强行分摊给某个域名。每个 HTTP 请求的路径、响应内容和聊天正文不在采集范围内。

## 快速开始

需要 macOS、Python 3.9+ 和已启动的 Clash Verge Rev。默认适配当前用户的 Unix socket，已在 Mihomo `v1.19.31` 上验证。

```sh
git clone https://github.com/codievilky/clash-traffic-audit.git
cd clash-traffic-audit
python3 install.py install --dry-run  # 先查看计划，不修改系统
python3 install.py install            # 使用当前登录用户，不要 sudo
```

打开 **http://127.0.0.1:18797/**。

默认安装目录：`~/Library/Application Support/clash-audit/`。安装器只安装采集器和页面，不会修改系统代理、Clash 节点或其他应用的代理配置。

自定义控制接口或网页端口：

```sh
python3 install.py install --socket /path/to/mihomo.sock --port 18797
```

也可从源码前台运行，不安装启动项：

```sh
python3 clash_audit.py --base "$PWD"
```

此时数据库位于源码目录的 `data/`，已被 `.gitignore` 排除。按 Ctrl-C 正常退出。采集从首个有效快照建立基线，不回填之前的历史。

## 管理

```sh
python3 install.py status
python3 install.py stop       # 停止并禁用自动启动，保留数据
python3 install.py start      # 启用自动启动并运行
python3 install.py uninstall  # 移除程序与启动项，保留 data/ 历史
```

更新：`git pull --ff-only` 后再运行 `python3 install.py install`。使用自定义目录时，管理命令也需传相同的 `--base`。

## 如何理解数字

| 指标 | 含义 |
| --- | --- |
| Clash 总流量 | 内核累计计数器增量，含经过 Clash 的 DIRECT 流量 |
| 已经归属到连接 | 被采样到的连接计数增量 |
| 尚未归属的差额 | 总量减去已归属量的正差，含短连接、间隙和快照时序差异 |
| 确认经过代理的流量 | 已识别代理路径的连接增量，不含未归属部分 |

域名、节点和进程是同一批流量的不同视角，**不能相加**。页面显示前 100 项；CSV 最多导出前 2000 项，但占比分母包含选定范围内的全部已归属量。

所有日期按 **UTC+8 / 北京时间** 划分。进程信息取决于内核 metadata；`find-process-mode: strict` 下可能为空，此时显示“未知”。项目不会自动更改进程识别模式。

## 文档

- [项目背景与排查经过](docs/background.md)
- [架构、统计口径与容量设计](docs/accounting.md)
- [安装、自启动、远程查看和运维](docs/operations.md)
- [验证与资源测量](docs/validation.md)
- [贡献指南](CONTRIBUTING.md)、[安全与隐私](SECURITY.md)、[更新记录](CHANGELOG.md)

## 开发验证

```sh
python3 -m unittest discover -v
python3 test_clash_audit.py --capacity
python3 install.py install --dry-run
node --check app.js  # 可选开发检查，Node 不是运行依赖
```

GitHub Actions 在 Linux/macOS、Python 3.9/3.13 上测试核心逻辑。Linux CI 不代表支持 Linux 自启动安装。

## 隐私与许可

数据保存在本地，没有云端上传或遥测。采集器只对 Mihomo 发起 GET 请求，不抓原始包、不解密 TLS，也不读取代理订阅或账户凭证。

域名统计仍可能反映使用习惯。分享问题时请脱敏，不上传数据库、CSV、`.env`、订阅或完整日志。本项目与 Clash Verge、Mihomo 或任何 AI 客户端官方团队无隶属关系。

[MIT License](LICENSE).
