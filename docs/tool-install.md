# 使用 uv tool 安装 MaiBot

从 PyPI 安装正式版：

```bash
uv tool install --python 3.13 maibot
mkdir my-maibot
cd my-maibot
maibot init
```

填写 `config/model_config.toml` 中的模型配置后执行 `maibot run`，按提示确认协议，并打开 `http://127.0.0.1:8001/` 登录 WebUI。

PyPI 来源的 uv 工具安装支持在 WebUI「设置 → 关于 → 主程序更新」检查正式版本、更新并重启。更新前会在 `data/self_update/backups` 备份配置、SQLite 数据库及已安装依赖的版本；安装验证失败时尝试恢复原包和依赖。新版本启动后执行的数据库迁移不会自动回退，如需降级，应恢复对应的数据备份。

自动更新需要通过 `maibot run` 启动，且启动环境的 PATH 中能找到 uv。同一工具环境还有其他实例运行时，先停止其他实例再更新。自定义依赖约束、本地 wheel、URL 和可编辑安装继续通过原安装方式更新。

也可以停止使用该工具环境的所有实例后，执行 `uv tool upgrade maibot`。如果安装时固定了版本，WebUI 更新会同步固定到你确认的新版本；要恢复命令行跟随正式版，执行 `uv tool install --force --python 3.13 maibot`。

## 本地构建和安装

在源码仓库目录构建：

```bash
uv build
```

选择 `dist` 中生成的 wheel 安装，例如当前版本：

```bash
uv tool install --python 3.12 ./dist/maibot-1.3.8-py3-none-any.whl
```

如果命令目录还不在 PATH 中，按 uv 的提示执行 `uv tool update-shell`，然后重新打开终端。

初始化和运行实例：

```bash
maibot init --directory ./my-maibot
maibot run --directory ./my-maibot
```

`init` 创建 `config`、`data`、`logs`、`plugins`、`temp`，只生成缺失的配置文件，不覆盖已有配置，不创建数据库，也不代替用户确认协议。运行前在 `config/model_config.toml` 中配置模型；首次 `run` 按原有流程确认协议，并启动 WebUI。消息平台仍需通过适配器接入。

省略 `--directory` 时使用当前目录。实例目录可以包含空格和中文；需要在命令行中使用引号。多个实例可以使用不同目录，但同一台机器上的监听端口需要分别配置。同一个 uv 工具环境中的实例共享主程序版本和 Python 插件依赖。

程序源码、默认提示词、翻译和内置插件来自安装包；配置、数据库、第三方插件、自定义提示词及运行缓存写入实例目录。卸载工具或替换 wheel 不删除实例目录。

本地升级时，先停止所有使用该工具环境的 MaiBot 实例，再安装新 wheel：

```bash
uv tool install --force --reinstall-package maibot --python 3.12 ./dist/maibot-1.3.8-py3-none-any.whl
```

将上例中的文件名替换为新构建的 wheel。下次启动会按插件声明检查和补装缺失依赖。本地 wheel 安装不从 PyPI 自动更新；要使用 WebUI 自动更新，先停止实例，再执行 `uv tool install --force --python 3.13 maibot` 切换为 PyPI 安装。

源码部署和 OneKey 继续运行原来的 `python bot.py`。没有设置 `MAIBOT_INSTANCE_DIR` 时，配置和数据仍放在仓库目录；该环境变量供 CLI 传递实例目录给子进程使用。

打包实现保留 `src.*` 导入路径。运行所需的仓库级只读资源由构建步骤复制到 `src/_resources`，不会复制现有 `config`、`data`、`logs` 或第三方插件。

A_Memorix 的 `paths.py` 包含最小安装兼容补丁，将宿主实例目录与包内 schema 资源分开定位。该耦合修改需后续同步上游 `MaiBot_branch`，遵循其修改归属约定。
