# MaiBot Android 悬浮桌宠

Android 8.0（API 26）及以上，使用系统 Android System WebView，请保持 WebView 更新。原生 Java 悬浮窗与前台服务负责生命周期，桌宠形象、皮肤、聊天、待办和探索页面复用 `src/desktop_pet/web`，HTTP/WebSocket 与认证流程复用 `pet-clients/desktop/renderer/transport.js`。不内置 Chromium，不需要手机安装 Python、Node.js 或 MaiBot 本体。

## 使用

1. 安装 APK，在服务器对应机器人的 WebUI 中启用桌宠并设置为 `remote`。
2. 输入宿主 WebUI 地址，点击「通过 WebUI 登录」，在页面中完成首次登录。如果有 Nginx HTTP Basic Auth，会先显示代理认证对话框。
3. 选择机器人，点击「启动悬浮桌宠」。按提示开启「显示在其他应用上层」权限；通知权限用于显示运行状态和停止入口。
4. 点击桌宠打开菜单，拖动移动位置；聊天框可调起系统输入法。探索记录在独立全屏页面查看。
5. 从通知或应用返回连接页；点击「停止桌宠」或通知中的「停止」结束连接与前台服务。

手机上的 `127.0.0.1` 指手机自身。连接电脑时使用电脑的局域网 IP 和 WebUI 端口，或使用远端 HTTPS 域名。服务器必须包含 `/api/host/desktop-pet/session` 接口；同一机器人同时使用一个远端桌宠客户端。

WebUI Cookie 保存在应用自己的 WebView 存储中，有效期内重启后可复用，不共享默认浏览器登录；代理认证缓存失效时仍需重新填写。应用不把密码写入设置，关闭 Android 数据备份/设备迁移，设置仅记录服务器、机器人和客户端 ID。HTTPS 证书错误不会被忽略。支持 HTTP 是为了局域网连接，公网应使用 HTTPS。

## 本版范围

- 已实现：WebUI 登录与额外代理认证、悬浮形象与皮肤、拖动、文字聊天、待办、探索与记录、断线重连、登录会话复用、文字复制。
- 暂未实现：手机自动读屏、应用监测、图片复制与系统图片选择器；客户端明确声明不支持读屏，不申请屏幕录制、无障碍或应用列表权限。已有服务器图片和皮肤仍由共用页面呈现。
- Android 13 及以上使用固定画布和独立触摸区域，展开菜单、切换面板时保持形象位置；透明区域可操作下方应用；窗口透明度遵循系统触摸穿透上限（通常 80%），形象和面板会略微透出背景。Android 8–12 使用矩形窗口裁剪，上方空白不拦截触摸。
- 使用用户主动启动的 `specialUse` 前台服务，不在开机后自动启动；系统强制停止或厂商省电策略可能终止它，需要重新打开应用。未上架应用商店。
- 已在 Android 15 模拟器验证；不同厂商真机、旧版本系统尚未逐一实测。

## 构建

需要 JDK 17 或 21、Android SDK Platform 35 与 Build Tools 35.0.0；配置 `JAVA_HOME`、`ANDROID_HOME`，或使用忽略提交的 `local.properties` 设置 `sdk.dir`。

```powershell
cd pet-clients/android
./gradlew.bat :app:assembleDebug
./gradlew.bat :app:lintDebug
```

Linux/macOS 使用 `sh ./gradlew`。Gradle Wrapper 固定 8.9 并校验分发包 SHA-256，Android Gradle Plugin 固定 8.7.3；`preparePetAssets` 构建任务直接复制共用页面与通信代码，生成资源不提交。

发布包需要自己的签名密钥，保持同一密钥才能覆盖升级：

```powershell
$env:MAIBOT_ANDROID_KEYSTORE = '你的密钥路径'
$env:MAIBOT_ANDROID_STORE_PASSWORD = '你的密钥库密码'
$env:MAIBOT_ANDROID_KEY_ALIAS = 'maibot'
# 密钥密码与库密码不同时另设 MAIBOT_ANDROID_KEY_PASSWORD
./scripts/dist.ps1
```

产物：仓库 `dist/android-pet-client/maibot-android-pet-1.0.1.apk` 与同名 `.sha256` 校验文件。Release 关闭 WebView 调试；不配置密钥的 `assembleRelease` 只产生未签名 APK。测试分发可用 `./scripts/dist.ps1 -DebugBuild`，测试签名与发布签名不同，不能直接相互覆盖安装。不要提交密钥或密码。

本次本地构建的发行密钥及密码记录保存在用户目录 `.android/maibot-signing/`，不在 Git 工作区；后续更新须沿用并妥善备份该目录。

## 运行验证

先在 `pet-clients/desktop` 运行 `npm ci`，再启动自己创建的 Android 15 模拟器，安装 Debug 构建：

```powershell
$env:ANDROID_SERIAL = 'emulator-5580'
node scripts/smoke.cjs
```

脚本只允许模拟器，使用本机模拟宿主与虚构账号；会在该模拟器安装/停止测试应用并授予悬浮窗、通知权限。覆盖代理认证、WebUI 登录、实际悬浮窗口、默认形象、聊天、待办、探索记录、拒绝读屏与远端本机权限、重连及进程重启后的 Cookie 复用。截图与失败日志保留在系统临时目录。

前台服务与悬浮窗遵循 [Android 前台服务类型](https://developer.android.com/develop/background-work/services/fgs/service-types#special-use)；本机桥通过 [WebView 的顶层 MessagePort](https://developer.android.com/reference/android/webkit/WebView#postWebMessage(android.webkit.WebMessage,%20android.net.Uri)) 传递，远端 WebUI 仅获得通信权限，不能调用本机桌宠操作。
