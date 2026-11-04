# MaiBot 远端桌宠客户端

运行在本地 Windows 桌面的轻量客户端，连接远端 MaiBot。只安装桌宠依赖，不安装 MaiBot 的模型、数据库、记忆或其他服务依赖。需要 Windows、WebView2 Runtime 和 Python 3.12+；推荐使用 uv 管理 Python 与依赖。

## 使用分发包

**免安装版**：解压 `maibot-desktop-pet-client-1.0.0-windows-standalone.zip`，双击 `MaiBotDesktopPet.exe`。完整保留同目录下的 `_internal` 文件夹；该版本包含 Python 与第三方依赖，只需系统已安装 WebView2 Runtime。

**轻量启动包**：

解压 `maibot-desktop-pet-client-1.0.0-windows.zip` 到任意目录，安装 uv 后双击 `start.cmd`。第一次启动会自动安装客户端依赖，随后显示登录窗口。

填写 MaiBot 宿主 WebUI 地址和登录密码，点击「读取机器人」，按名称选择目标机器人后启动桌宠。服务器须启用桌宠并将运行模式设为 `remote`。公网地址使用 `https://`；客户端据此使用 `wss://` 连接。登录窗口记住服务器地址和上次选择的机器人，密码不写入磁盘。

命令行也可以启动；将示例中的 32 位十六进制 Bot ID 替换为宿主中的真实 ID。没有通过环境变量提供密码时会隐藏输入提示：

```powershell
uv run --project . maibot-desktop-pet --server https://bot.example.com --bot-id 0123456789abcdef0123456789abcdef
```

如需本机截图和持续阅读，在本地登录窗口授权或在命令行增加 `--allow-screen-read`，并同时在服务器桌宠设置中允许读取桌面。未授权时服务器无法通过桌宠读取本机画面。桌宠支持 Alt+F4 退出。

## 安装 wheel

```powershell
uv tool install --python 3.12 .\maibot_desktop_pet_client-1.0.0-py3-none-any.whl
maibot-desktop-pet
```

无需解压或安装完整 MaiBot 源码。客户端窗口位置、缓存保存在本机，聊天、待办和探索记录由连接的服务器管理。断线时窗口保留并显示状态，恢复网络后自动重连；未确认的操作不会自动重放。

## 从 MaiBot 源码构建

本目录是分发配置，实际客户端代码复用 `src/desktop_pet`，不维护第二份实现。在 MaiBot 仓库根目录运行：

```powershell
uv run --no-project --python 3.12 scripts/build_desktop_pet_client.py
```

脚本生成 `dist/desktop-pet-client/` 下的 wheel、Windows 启动 ZIP 和 SHA256 校验文件。ZIP 包含客户端源码、资源和启动器；Python 与第三方依赖在首次运行时由 uv 安装。构建不读取或打包 Bot 配置、登录密码、聊天数据库或用户数据。

在 Windows 上增加 `--executable` 可生成免安装版。构建脚本在隔离环境中安装 PyInstaller 和客户端依赖，不修改主程序环境。
