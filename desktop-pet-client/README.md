# MaiBot Electron 桌宠客户端

独立的 Electron 桌宠，连接本机或远端 MaiBot 宿主，提供 Windows、macOS 和 Linux 客户端构建。分发包自带 Electron 运行时，使用时不需要安装 Python、WebView2 或完整 MaiBot。

## 连接 MaiBot

1. 启动 MaiBot 宿主和要使用的机器人。
2. 在该机器人的 WebUI「桌宠」设置中启用桌宠，将 `desktop_pet.mode` 设为 `remote`。
3. 启动 Electron 客户端，填写宿主 WebUI 地址和密码后点击「连接」；只有多个机器人时才需要选择目标。

本机连接也使用宿主地址，例如 `http://127.0.0.1:8001`，端口以实际 WebUI 地址为准。`remote` 表示使用独立客户端，并不要求 MaiBot 部署在另一台电脑。公网连接使用 `https://` 地址，客户端使用相应的 HTTPS/WSS 通道。一个 Bot 同时连接一个桌宠客户端。

聊天、待办、探索等数据保存在对应 Bot 的服务器端，窗口位置等本地设置保存在客户端。连接中断时显示离线状态并尝试重连，未确认的写入操作不会自动重复提交。客户端密码只用于当前登录，不写入本地配置文件。

需要更换服务器、机器人或屏幕授权时，右键系统托盘中的桌宠图标，选择「更换连接 / 屏幕授权」。也可以从托盘显示桌宠或退出。

主程序原有的 Python 内嵌桌宠仍可通过 `desktop_pet.mode = "local"` 使用；这一模式运行于 Windows 本机，由主程序负责启动和回收窗口。

## 从源码运行

安装 Node.js 22 或更高版本及 npm，在仓库根目录执行：

```sh
cd desktop-pet-client
npm ci
npm start
```

Windows 也可以在完成 `npm ci` 后双击 `start.cmd`。启动器会检查 npm 和 Electron 依赖是否存在；尚未安装时会提示先执行 `npm ci`。

## 构建分发包

在 `desktop-pet-client` 目录执行对应平台的命令：

```sh
npm run dist:win
npm run dist:mac
npm run dist:linux
```

产物输出到仓库的 `dist/desktop-pet-client/`：Windows ZIP、macOS DMG 和 Linux AppImage。建议在目标操作系统构建并验证对应产物；macOS 签名、公证和 Linux 桌面集成需要在相应环境完成。构建使用共用桌宠网页资源，不打包 Bot 配置、聊天数据库或模型依赖。

## 桌面权限与平台差异

截图和持续阅读需要同时开启客户端的本地授权及服务器「允许读取桌面」。实际读取的是客户端电脑，断开连接或关闭读取后停止采集。

- **Windows**：Electron 自带浏览器运行时，无需 WebView2；应用条件可匹配进程名和窗口标题。
- **macOS**：读取屏幕还需要系统的「屏幕录制」权限；拒绝后无法截图，调整权限后可能需要重新启动客户端。应用条件当前只匹配进程名。
- **Linux**：客户端使用 X11 窗口模式；Wayland 会话需要可用的 XWayland。截图仍受桌面会话及 PipeWire/系统选择器限制，不能保证无人值守地枚举或固定读取所有显示器。应用条件当前只匹配进程名，透明窗口、置顶、位置控制和鼠标穿透需要在目标桌面实测。

目前在 Windows 上进行运行验证；macOS/Linux 提供构建配置，尚未完成实机验证。屏幕来源和系统授权限制参见 [Electron desktopCapturer 文档](https://www.electronjs.org/docs/latest/api/desktop-capturer)。
