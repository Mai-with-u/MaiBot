# DSH / ACP 连接

独立 Agent 和实验性外接 Planner 共用 `[agent]` 的连接设置。模型、凭据及 ACP 会话由外部 Agent 管理。外部程序需要支持 ACP v1 和 HTTP MCP；恢复已保存的会话还需要 `session/resume`。

## 本机 dsh CLI

CLI 模式不需要 `dsh-maibot-bridge`。先在运行麦麦的同一用户环境中确认 `dsh --profile acp` 可以启动，再配置：

```toml
[agent]
enabled = true
connection = "dsh"
acp_command = "dsh"
```

Windows 可将 `acp_command` 设置为 `dsh.cmd` 或实际程序的绝对路径。启动程序字段只填程序，参数放在 `acp_args`；该参数列表仅用于 `custom` 模式。`acp_env` 会覆盖子进程环境，程序查找也使用覆盖后的 `PATH`。

## 通过 SSH 连接远程 dsh CLI

麦麦运行在 A，dsh 运行在 B 时，需要同时建立两个方向的连接：

- A 通过 SSH 的标准输入输出调用 B 的 ACP。
- B 通过 SSH 反向转发访问 A 的 MCP 工具服务。

先为运行麦麦的用户配置 SSH 密钥和已确认的主机密钥，并在 B 上创建会话目录。以下示例假设 B 为 Linux，`dsh` 位于 `/usr/local/bin/dsh`：

```toml
[agent]
enabled = true
connection = "custom"
acp_command = "ssh"
acp_args = [
    "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-R", "127.0.0.1:{mcp_port}:127.0.0.1:{mcp_port}",
    "user@server-b",
    "/usr/local/bin/dsh", "--profile", "acp",
]
acp_cwd = ""
acp_remote_cwd = "/srv/maibot-agent"
mcp_port = 0
```

`acp_cwd` 是 A 上启动 SSH 的目录，留空由麦麦创建本机工作目录。`acp_remote_cwd` 是 B 上已存在的绝对目录，原样传入 ACP 的 `session/new` / `session/resume`，不会在 A 上创建或解析；不要填写 `~` 或相对路径。SSH 命令自身如需先切换目录，应在远端命令中显式处理。

麦麦先启动工具服务，再将 `acp_args` 中的 `{mcp_port}` 替换为本次实际端口。两侧端口必须相同，才能让传给 dsh 的 `http://127.0.0.1:<port>/mcp` 通过隧道回到麦麦。其他花括号保持原样。SSH 服务端需允许 TCP 转发；远端端口占用时连接直接失败并报告原因，不会重试任务。

外接 Planner 每个连接都会创建工具服务，保留 `mcp_port = 0` 可避免本机固定端口冲突。切换远程工作目录后不能继续恢复原会话；Planner 可通过清空对应聊天建立新会话。跨主机切换前，应使用独立麦麦实例保存各自会话状态，不能将一个 Agent 的会话 ID 用于另一个 Host。

远程 dsh 的文件系统与麦麦的文件系统彼此独立。工具在麦麦主机执行，`agent_talk` 附图路径也必须在麦麦本机允许的工作目录内；需要传文件时应先显式同步，不能把 B 上的文件路径直接当作 A 上的路径。

## dsh Desktop

Desktop 模式需要在 Desktop 侧安装 `dsh-maibot-bridge`，启动后生成 `~/.dsh/maibot-desktop.json`。麦麦选择 `connection = "dsh_desktop"`，`desktop_manifest` 留空使用上述默认文件，也可指定绝对路径。

桥接插件仓库及 [v0.1.1 安装包](https://github.com/SengokuCola/dsh-maibot-bridge/releases/download/v0.1.1/dsh-maibot-bridge-0.1.1.tgz) 已公开，无需 GitHub 登录即可下载。主程序不包含该插件；使用 Desktop 自带的 dsh 命令安装后重启 Desktop，也可在 Desktop 的插件安装入口选择本地源码目录。

```powershell
dsh plugin --profile desktop add https://github.com/SengokuCola/dsh-maibot-bridge/releases/download/v0.1.1/dsh-maibot-bridge-0.1.1.tgz
```

Desktop 桥接和麦麦工具服务都仅监听回环地址。远程 Desktop 可以使用 SSH 隧道，无需修改为公网监听：

1. 在麦麦设置 `acp_remote_cwd` 为 Desktop 主机上的绝对目录，并设置 `mcp_port = 48123`（选择空闲端口）。固定端口适用于单个连接，不应与多个外接 Planner 连接共用。
2. 假设 Desktop manifest 中桥接端口为 `51234`，在麦麦主机另行保持以下隧道运行：

   ```sh
   ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:48124:127.0.0.1:51234 -R 127.0.0.1:48123:127.0.0.1:48123 user@desktop-host
   ```

3. 将 Desktop manifest 安全复制到麦麦主机，保留 `version` 和 `token`，把 `host` 设为 `127.0.0.1`、`port` 设为本地转发端口 `48124`，用 `desktop_manifest` 指向副本。

Desktop 重启后桥接端口和令牌可能改变，需要更新隧道和 manifest 副本。不要分享 manifest 或其中的令牌。关闭麦麦只断开桥接，不关闭 Desktop。

## 排查连接失败

- 日志中的 `ACP stderr:` 实时显示外部进程诊断，连接超时也附带最近 20 行 stderr。先检查 SSH 认证、端口转发、dsh 模型凭据及远端目录。
- ACP stdout 只能包含 JSON-RPC 协议行，启动脚本的调试信息应输出到 stderr；SSH 不要分配伪终端。
- Agent 的单轮错误会在 WebUI 展示，并通知发起传话的聊天；后续新请求仍可执行。断线、超时或连接配置错误会暂停，修复后可发送新请求或在 WebUI 恢复；不会自动重发执行结果未知的任务。
- MCP 工具服务保留独立 Bearer 认证和 Host 校验。修改 MCP 监听地址不是建立远程连接的必要条件。

## 回复、身份与暂停

外部 Agent 的文本通过 ACP `session/update` 流式通知保存到执行记录，并显示在独立 Agent 的 WebUI 中。`session/prompt` 的最终回执提供停止原因，不承载完整回复正文。向普通聊天传话时，需要显式调用 `agent_talk` 并指定真实会话 ID；直接文本不会被自动转发，传话回执也不等于 IM 已发言。

接入说明将麦麦人格标记为通信对象资料，外部 Agent 的身份仍由其 harness 配置决定。说明内容发生变化后，会在下个有输入的回合同步到已有 ACP 会话。

正常结束原因为 `end_turn`。其他停止原因（如 `refusal`、`max_tokens`），以及 dsh 通过 ACP 返回的模型请求错误，会保留已经收到的输出、清除已结束回合的在途标记，并报告单轮失败，不暂停整个队列。来源聊天会收到包含任务编号及原始错误的内部通知，由其 Planner 向用户说明；这不等于 IM 已发言。通知失败单独记录日志，不覆盖原始错误。

换模型本身不会触发麦麦重试。换好模型后发送“继续”或新请求，会进入同一个外部会话，旧失败任务不会自动重放。执行中的回合失败后，已经到达的后续请求也可继续处理；成功后清除当前错误状态。

重启保留暂停状态。旧版本记录的故障暂停可由新的用户请求或 WebUI 恢复，系统输入不能解除暂停。手动暂停始终需要在 WebUI 恢复，暂停期间传话的回执会明确提示仅入队。MCP 已通过工具 schema 校验参数，未知工具会返回错误。结果不确定时应检查真实错误与外部会话，避免重复执行已完成的操作。
