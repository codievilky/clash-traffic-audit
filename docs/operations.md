# 安装与运维

## 依赖与控制接口

需要 Python 3.9+ 和可读取的 Mihomo Unix socket。默认适配 Clash Verge Rev 的当前用户路径：

```text
/var/run/clash-verge-service/users/<UID>/verge-mihomo.sock
```

其他布局通过 `--socket` 指定。无需为采集器额外开放局域网控制 API。Python 没有第三方包依赖。

## 安装和管理

```sh
python3 install.py install --dry-run
python3 install.py install
python3 install.py status
```

安装器复制程序和网页至 `~/Library/Application Support/clash-audit/`，生成 `~/Library/LaunchAgents/cn.codievilky.clash-audit.plist`，然后执行 `launchctl bootstrap gui/<UID>`。

以当前已登录用户运行，不要 sudo。自定义参数示例：

```sh
python3 install.py install --base "$HOME/Library/Application Support/my-clash-audit" \
  --python /absolute/path/to/python3 --socket /path/to/mihomo.sock --port 18797
```

```sh
python3 install.py stop       # 停止并禁用自动启动
python3 install.py start      # 重新启用并运行
python3 install.py uninstall  # 移除程序和启动项，保留历史数据
```

自定义安装目录也要传给管理命令。一个用户目前只使用一个固定标签的启动项。

## 自启动的含义

这是用户级 **LaunchAgent**：用户登录 macOS 后运行，尚未登录时不运行，退出登录后不保证继续采集。

| 参数 | 用途 |
| --- | --- |
| `RunAtLoad=true` | 加载启动项时启动 |
| `KeepAlive=true` | 退出后重新拉起 |
| `ThrottleInterval=10` | 限制过于频繁的启动 |
| `ProcessType=Background`、`Nice=10` | 后台优先级 |
| `LowPriorityIO=true` | 较低 I/O 优先级 |

标准输出和错误输出丢弃到 `/dev/null`，程序自己的日志轮转保存。Clash 尚未运行时会显示不可用并等待恢复，不会自行启动或更改 Clash。休眠期间不承诺持续采集。

## 远程查看

假设另一台自己的 Mac 使用 SSH 别名 `audit-host`：

```sh
ssh -N -L 127.0.0.1:18798:127.0.0.1:18797 audit-host
```

当前电脑打开 `http://127.0.0.1:18798/`，看到的是 **audit-host 的流量**。

Python 服务监听远端 `18797`；当前电脑的 `18798` 是 SSH 转发。关闭 SSH 只影响查看，不会停止远端采集。安装器没有为查看通道配置自启动，也不要把无登录认证的页面公开到公网。

## 应用代理切换

安装统计器不会自动改变应用的代理路径。需要切换时：

1. 备份应用原配置，限制备份访问权限。
2. 确认本机 Clash 的实际 mixed-port，不假定每台机器都相同。
3. 修改明确的代理项，例如 `http_proxy` 和 `https_proxy` 指向 `http://127.0.0.1:<mixed-port>`。
4. 正常重启需要重新加载配置的应用。
5. 核对操作系统连接表、Mihomo 活动连接和审计增量。

系统代理、shell 变量、应用 `.env` 和旧进程可能不同。回退时也应先检查是否有后续配置编辑，避免覆盖新改动。

## 更新、备份、回退

`git pull --ff-only` 后重新运行安装器。它正常停止旧进程、更新程序，再启动；数据库不被替换。安装失败时会尝试恢复原代码和启动项。

安装器记录 Python 的路径。如果包管理器移除了该解释器，需重新安装以更新启动项。

获得一致的数据库备份，可先停止服务再复制 `data/audit.sqlite3`，或使用 SQLite 在线备份 API。不要直接复制正在写入的数据库和回滚日志来假装得到一致快照。

回退代码可检出旧 Git 版本再安装；首版没有跨版本数据库迁移承诺。备份和 CSV 不应提交 Git；自存备份不计入运行目录预算。

## 排障

- **只有未知进程**：内核未提供进程信息，`strict` 模式下可能出现。域名和节点统计仍可用。
- **没有昨天的数据**：尚未采集过，无法回填历史。
- **总量大于明细**：检查未归属差额和采样间隙，短连接可能已经关闭。
- **重启内核后没有补入其全部累计量**：新实例只建立基线，这是预期行为。
- **页面打不开**：检查 `status`、Python 路径、端口占用及 `data/collector.log`；远程查看还需 SSH 通道。
- **文件不变小**：旧记录已删除但 SQLite 在复用空闲页，不要求文件立即缩小。
