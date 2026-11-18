# 手动构建桌宠安装包

工作流 `.github/workflows/publish-pet-clients.yml` 独立于主程序发布流程，仅支持手动启动。

1. 将工作流合并到默认分支。
2. 在仓库 **Settings → Secrets and variables → Actions** 配置 Android 签名 secrets：

   | Secret | 内容 |
   | --- | --- |
   | `MAIBOT_ANDROID_KEYSTORE_BASE64` | 已有正式签名 keystore 文件的 Base64 内容 |
   | `MAIBOT_ANDROID_STORE_PASSWORD` | keystore 密码 |
   | `MAIBOT_ANDROID_KEY_ALIAS` | 签名密钥别名 |
   | `MAIBOT_ANDROID_KEY_PASSWORD` | 密钥密码；与 keystore 密码相同可不配置 |

   使用已有 Android 发布包的签名密钥，保证用户能够覆盖升级。不要把签名文件或密码提交进仓库。
3. 进入 **Actions → Build Pet Clients (Manual) → Run workflow**，选择默认分支运行。

运行开始时查询 GitHub 最新正式 Release（排除草稿和预发布），锁定它的 tag 和源码提交。两个 Windows runner 分别构建 Windows x64 安装程序与签名 Android APK，互不等待；Android 同时执行 lint 和 APK 签名验证。客户端版本号沿用该 tag 中各客户端自己的版本配置。

构建结束后自动把安装包及各自的 `.sha256` 文件挂到锁定的 Release。期间即使发布了更新的 Release，也不会改变本次上传目标。构建源码来自目标 Release，所以未进入该 tag 的本地或分支修改不会包含在安装包里。

一个平台失败不会取消另一个平台，成功平台的包仍会上传；失败原因可在 Actions 日志查看。构建产物也会作为 Actions artifacts 保留 30 天。同名同内容附件会跳过，同名不同内容则明确报错，避免覆盖已发布安装包。需要发布变更时，应提升对应客户端版本并使其进入目标 Release。

这个工作流不创建 Release、不修改发布说明，也不会触发或阻塞原有主程序发布工作流。

## CNB Release 与附件同步

独立工作流 `.github/workflows/cnb-releases.yml`（**Sync Releases to CNB**）在桌宠构建或主程序发布工作流结束后运行，也支持 GitHub Release 发布/编辑事件和手动运行。即使桌宠只有一个平台成功，已挂到 GitHub Release 的附件也会进入同步。

同步复用现有 `CNB_TOKEN`、`CNB_GROUP` 和可选的 `CNB_MAIBOT_REPO` 配置。`CNB_TOKEN` 除原有代码镜像权限外，还需目标仓库的 **`repo-release:rw`** 权限。工作流必须合并到默认分支才能接收 `workflow_run` 事件。

同步先复用代码镜像脚本准备分支、标签及 LFS，再扫描 GitHub 所有非草稿 Release，创建或更新 CNB 对应版本的标题、说明、预发布状态和 latest 标记，并补齐全部 Release 附件（包括桌宠 EXE、APK 和校验文件）。扫描所有版本可避免并发发布时漏掉先前构建锁定的旧版本。GitHub Release 是唯一数据来源，不读取触发构建的 artifacts 或执行该分支的脚本；始终执行 main 上的同步代码。

附件按 SHA256 校验，同内容跳过，同名不同内容报错；不会删除 CNB 上已有版本或附件。上传使用 CNB 官方 OpenAPI 的预签名地址，完成后确认并校验，附件永久保存。接口文档：<https://api.cnb.cool/>。

同步失败会在独立工作流和 `cnb-release-sync-status` artifact 中报告，不影响 GitHub 发布和桌宠构建结果。修复权限或网络问题后，可手动运行 **Sync Releases to CNB** 补齐；不需要重新构建安装包。该流程与代码镜像共用并发锁，以避免标签同步和 Release 创建互相竞争。
