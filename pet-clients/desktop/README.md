# MaiBot Tauri 2 桌宠客户端

独立的 Tauri 2 桌宠，连接本机或远端 MaiBot 宿主，使用 Rust 处理桌面能力、系统 WebView 显示共用桌宠界面。安装包不包含完整浏览器运行时，使用时不需要安装 Python、Node.js、Rust 或完整 MaiBot，但需要对应系统的 WebView。

## 连接 MaiBot

1. 启动 MaiBot 宿主和要使用的机器人。
2. 在该机器人的 WebUI「桌宠」设置中启用桌宠，将 `desktop_pet.mode` 设为 `remote`。
3. 启动客户端，填写宿主 WebUI 地址，按需勾选「允许读取本机屏幕」，点击「通过 WebUI 连接」。
4. 首次连接会打开该宿主的 WebUI 登录页，在页面中完成登录；只有多个机器人时才需要按名称选择目标，再点击「启动桌宠」。

本机连接也使用宿主地址，例如 `http://127.0.0.1:8001`，端口以实际 WebUI 地址为准。`remote` 表示使用独立客户端，并不要求 MaiBot 部署在另一台电脑。公网连接使用 `https://` 地址，客户端使用相应的 HTTPS/WSS 通道。一个 Bot 同时连接一个桌宠客户端。

客户端没有独立密码框，不在配置文件中保存密码。Windows、Linux 和 macOS 14 及以上为各服务器使用独立 WebView 数据存储；macOS 10.15～13 使用默认 WebKit 数据存储，遵循浏览器的 Cookie 域和路径隔离规则。宿主当前会话有效期为 8 小时，有效期内再次连接会复用会话。会话过期、被撤销或因宿主认证状态变化失效时，会重新显示 WebUI 登录页。该会话属于桌宠内的 WebView，不与另外打开的浏览器自动共享。

认证完成后，客户端切换到同源的独立轻量连接页，维持桌宠通信，不持续加载完整 WebUI。服务器也必须更新到提供 `/api/host/desktop-pet/session` 的版本；该页面仍要求有效宿主会话，反向代理的认证规则同样适用。

若 Nginx 等反向代理启用了 HTTP Basic Auth，原生 WebView 的认证对话框还可能要求输入代理账号密码。其缓存由 WebView 和代理管理，缓存失效或重新启动后可能需要重新认证，不能保证永久免输。

聊天、待办、探索等数据保存在对应 Bot 的服务器端，窗口位置等本地设置保存在客户端。连接中断时显示离线状态并尝试重连，未确认的写入操作不会自动重复提交。

需要更换服务器、机器人或屏幕授权时，右键系统托盘中的桌宠图标，选择「更换连接 / 屏幕授权」。也可以从托盘显示桌宠、打开 WebUI 登录页或退出。屏幕授权仅对本次连接有效。

主程序原有的 Python 内嵌桌宠仍可通过 `desktop_pet.mode = "local"` 使用；这一模式运行于 Windows 本机，由主程序负责启动和回收窗口。

## 从源码运行

安装 Node.js 22 或更高版本、npm、Rust stable 工具链及对应平台的编译依赖。当前 Rust 依赖至少需要 1.90，建议使用最新 stable：

- Windows：Visual Studio Build Tools 的「使用 C++ 的桌面开发」、Windows SDK、MSVC Rust 工具链及 WebView2 Runtime。
- macOS：Xcode Command Line Tools；使用系统 WKWebView。
- Linux：C/C++ 编译工具、pkg-config、WebKitGTK 4.1、GTK、托盘库及其开发包；截图依赖还需要 XCB/XRandR、D-Bus、PipeWire、Clang 等开发包。按发行版安装 [Tauri 前置依赖](https://v2.tauri.app/start/prerequisites/) 和 [xcap Linux 依赖](https://github.com/nashaofu/xcap#linux-system-requirements)。

在仓库根目录执行：

```sh
cd pet-clients/desktop
npm ci
npm start
```

Windows 也可以在准备好上述工具链并完成 `npm ci` 后双击 `start.cmd`。启动器会检查 npm 和 Tauri CLI 是否存在；首次启动会编译 Rust 原生程序。

## 构建分发包

在 `pet-clients/desktop` 目录执行对应平台的命令：

```sh
npm run dist:win
npm run dist:mac
npm run dist:linux
```

原始安装包位于 `src-tauri/target/release/bundle/`，构建脚本同时收集到仓库的 `dist/desktop-pet-client/`。Windows 产物为 NSIS 安装器 `*-setup.exe`，macOS 为 DMG，Linux 为 DEB、RPM 和 AppImage。默认构建当前工具链的单一架构，请在目标操作系统和架构上构建、验证；macOS 签名、公证需要对应环境。只编译原生程序可运行 `npm run pack`，产物位于 `src-tauri/target/release/`。

Windows 安装器使用 LZMA 压缩，安装到当前用户目录；检测到缺少 WebView2 Runtime 时通过引导程序在线安装，因此首次安装可能需要联网。构建复用桌宠网页、字体和皮肤资源，不打包 Bot 配置、密码、聊天数据库或模型依赖。JavaScript 依赖由 `package-lock.json` 管理，Rust 依赖由 `src-tauri/Cargo.lock` 管理。

本次 Windows x64 构建的安装包约 2.04 MiB，程序本体约 5.36 MiB，不包含共享 WebView2 运行时及使用过程中产生的浏览器缓存；后续构建大小以实际产物为准。

## 桌面权限与平台差异

截图和持续阅读需要同时开启客户端的本地授权及服务器「允许读取桌面」。实际读取的是客户端电脑的主屏，截图最长边为 1920 像素；无法确定主屏时会报错。断开连接或关闭读取后停止后续采集。

- **Windows**：使用 WebView2 Runtime；应用条件可匹配进程名和窗口标题。鼠标穿透使用原生窗口区域，随桌宠、气泡和面板更新，避免鼠标移入、移出时切换透明窗口样式导致画面消失。
- **macOS**：读取屏幕还需要系统的「屏幕录制」权限；拒绝后无法截图，调整权限后可能需要重新启动客户端。应用条件当前只匹配进程名。
- **Linux**：使用 WebKitGTK 4.1 和 X11 窗口模式；Wayland 会话需要可用的 XWayland。xcap 在 Wayland 上可能使用桌面截图门户，系统可能每次要求确认，不能保证无人值守连续读屏。GNOME/门户路径可能生成系统临时 PNG，xcap 在读取后执行清理；纯 X11、Windows 和 macOS 的截图路径在内存中处理。应用条件当前只匹配进程名，透明窗口、置顶、位置控制和鼠标穿透需要在目标桌面实测。

目前在 Windows 上进行运行验证；macOS/Linux 提供构建配置，尚未完成实机验证。macOS 最低配置版本为 10.15；Linux 运行依赖以发行版包管理器及实际桌面环境为准。截图平台支持范围参见 [xcap 文档](https://github.com/nashaofu/xcap#implementation-status)。

Windows 冒烟检查运行 `npm run test:smoke`；设置 `PET_EXECUTABLE` 可指定待测程序。检查鼠标穿透和透明窗口时，同时设置 `PET_INSPECT_WINDOW=1`，脚本会在连接测试机器人后暂停，供实际鼠标移入、移出、打开菜单和拖动，再在终端按 Enter 继续。CDP 的鼠标事件不移动系统指针，不能替代这项检查。
