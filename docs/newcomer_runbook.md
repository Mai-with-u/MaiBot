# 麦麦 MaiBot 新手 Runbook

> 面向第一次部署 / 第一次接手运维麦麦的人。目标是：**照着做一遍就能跑起来，出问题知道去哪儿看。**
>
> 适用版本：MaiBot 1.2.x（`pyproject.toml` 中的 `version`）。
> 完整功能文档见 [docs.mai-mai.org](https://docs.mai-mai.org)，本文只覆盖「跑起来 + 日常运维 + 排障」。

---

## 0. 先搞清楚这几个名词

| 名词 | 是什么 | 在哪儿 |
| :--- | :--- | :--- |
| MaiCore | 麦麦本体，也就是这个仓库，入口是 `bot.py` | 项目根目录 |
| 适配器（Adapter） | 把 QQ / 其他平台的消息翻译成麦麦能懂的格式。现在是**插件形态**，随主进程一起跑 | `plugins/napcat_adapter/` |
| NapCat | 第三方 QQ 协议端，负责真正登录 QQ。**不在本仓库**，要单独装 | 独立进程 / 独立容器 |
| WebUI | 浏览器管理后台：改配置、看日志、管插件、管记忆 | 默认 `http://127.0.0.1:8001` |
| Planner / Replyer | 麦麦的两个核心模型任务：Planner 决定「要不要说、说什么类型」，Replyer 负责真正生成回复 | `config/model_config.toml` |
| A_Memorix | 长期记忆系统（画像、片段、检索） | `config/bot_config.toml` 的 `[a_memorix]` |

消息链路：

```
QQ ──> NapCat ──WebSocket(默认 3001)──> napcat_adapter 插件 ──> MaiCore ──> 模型 API
```

---

## 1. 前置条件

- **Python 3.12+**（`requires-python = ">=3.12"`）
- **uv**（本项目统一用 uv 管理依赖，不要用 pip 直接装）
- **一个能用的模型 API Key**：至少要有一个 Chat 模型，最好再有一个 Embedding 模型
- **NapCat**（如果你要接 QQ）：需要一个能登录的 QQ 小号
- 磁盘：预留几个 GB（表情包、图片缓存、日志、SQLite 数据库都会长）

> 只想先看看效果、不接 QQ？可以跳过 NapCat，用第 4 步的**终端对话模式**验证。

---

## 2. 首次部署（源码方式，推荐新手用这个）

### 2.1 拉代码 + 装依赖

```bash
git clone https://github.com/Mai-with-u/MaiBot.git && cd MaiBot
```

```bash
uv sync
```

`main` 是稳定分支，`dev` 是开发分支。新手先用 `main`。

### 2.2 第一次启动，生成配置

```bash
uv run python bot.py
```

第一次启动会发生三件事：

1. **要你同意协议**：终端会打印 EULA 和隐私协议的哈希，需要手动输入 `同意` 然后回车。
   同意结果写在根目录的 `eula.confirmed` / `privacy.confirmed`。
   非交互环境（容器、CI）用环境变量 `EULA_AGREE` / `PRIVACY_AGREE` 填对应哈希跳过。
2. **自动生成配置**：`config/bot_config.toml` 和 `config/model_config.toml` 缺失时会按默认值生成，日志里会打印「配置文件缺失，正在生成默认配置」。
3. **自动建库**：`data/MaiBot.db`（SQLite，带 `-wal` / `-shm`）。

此时因为还没填模型，麦麦跑起来也不会正常说话。**Ctrl+C 停掉**，去填配置。

### 2.3 填模型（`config/model_config.toml`）

这是新手最容易卡住的一步。三段结构，按顺序填：

1. `[[api_providers]]`：服务商。填 `name`（自己起名）、`base_url`、`api_key`、`client_type`（`openai` / `openai_responses` / `gemini`）。
2. `[[models]]`：具体模型。`model_identifier` 是服务商那边的真实模型名，`name` 是你自己起的别名，`api_provider` 要对上第 1 步的 `name`。
3. `[model_task_config.*]`：把别名填进各个任务的 `model_list`。

**必须配的任务**：

| 任务 | 用途 | 不配的后果 |
| :--- | :--- | :--- |
| `replyer` | 生成回复 | 不会说话 |
| `planner` | 决定要不要说话、用什么动作 | 完全不动 |
| `utils` | 概括、整理等小任务 | 大量子功能报错 |
| `embedding` | 记忆/检索的向量化，**必须是 embedding 类模型，不能填 LLM** | 长期记忆不可用 |

> ⚠️ 只配 `embedding` 还不够：长期记忆的总开关是 `config/bot_config.toml` 里的 `[a_memorix.plugin] enabled`，**默认是 `false`**，不改成 `true` 的话长期记忆照样完全不工作。

**建议配的任务**：`vlm`（识图，不配启动时会有 `未配置视觉识图模型` 的 WARNING）、`voice`（语音识别）。
其余任务（`memory`、`mid_memory`、`learner`、`expression_use`、`emoji`）留空会自动回退到 planner / utils。

> 也可以先启动，然后在 WebUI 里用表单填模型，不必硬啃 TOML。

### 2.4 填人设（`config/bot_config.toml`）

最少改这几项就够开跑：

- `[bot] nickname` / `alias_names`：麦麦的名字和别名（影响「有没有被叫到」的判断）
- `[personality] personality` / `behavior_style` / `reply_style`：人格、行为准则、说话风格
- `[chat.reply_timing] talk_value`：说话频率，越小越安静。新群建议先调低观察

### 2.5 接 QQ（`plugins/napcat_adapter/config.toml`）

先把 NapCat 装好并登录 QQ，在 NapCat 里开一个 **WebSocket 服务器**（默认端口 3001，可设 token）。然后：

```toml
[napcat_server]
host = "127.0.0.1"   # NapCat 所在地址
port = 3001          # 对上 NapCat 的 WS 服务端口
token = ""           # NapCat 配了 token 就填上

[chat]
group_list_type = "whitelist"
group_list = ["你的群号"]      # 白名单模式下，不填就一个群都不理
private_list_type = "whitelist"
private_list = []
```

> **白名单是新手最常见的「麦麦没反应」原因**：默认是 `whitelist`，没加群号就等于全部屏蔽。

### 2.6 正式启动

```bash
uv run python bot.py
```

启动后进程是**两层**的：外层 Runner 守护，内层 Worker 干活。Worker 以退出码 `42` 退出表示「请求重启」，Runner 会自动把它拉起来（WebUI 上的重启按钮就是走这条路）。Ctrl+C 会把两层一起停掉。

---

## 3. Docker 部署（可选）

仓库自带 `docker-compose.yml`，一把起 `core` + `napcat` + `sqlite-web`：

```bash
docker compose up -d
```

要点：

- 配置持久化在 `./docker-config/mmc`，不是仓库里的 `config/`
- WebUI 端口映射是 `18001:8001`，所以浏览器访问 `http://宿主机:18001`
- 容器里必须 `WEBUI_HOST=0.0.0.0`，否则宿主机访问不到
- compose 里已经预置了 `EULA_AGREE` / `PRIVACY_AGREE` / `MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED`，容器内不会卡在交互确认
- NapCat 的扫码登录走 `http://宿主机:6099`

---

## 4. 怎么确认「真的跑起来了」

按顺序验收，哪一步断了就知道问题在哪一层：

1. **启动日志**：出现初始化完成的横幅，没有连续 ERROR。
2. **WebUI 能进**：浏览器打开 `http://127.0.0.1:8001`。
   登录 Token 在**启动日志**里打印，也存在 `data/webui.json` 的 `access_token`。
   没配固定 Token 时，每次启动会重新生成临时 Token。
3. **不接 QQ 也能对话**：把 `config/bot_config.toml` 里的 `[debug] enable_console_input` 改成 `true`，在**交互式终端**里启动，就能直接在终端跟麦麦说话，还能用 `/pm` 等指令，`exit()` 退出终端输入。
   （终端里的 `/clear` 不受 `[debug] enable_clear_context_command` 限制，可以直接用；那个开关（默认关闭）管的是群聊/私聊里的 `/clear`。）
   这是验证「模型配置对不对」最快的办法，可以完全绕开 NapCat。
4. **适配器连上了**：日志里 napcat_adapter 插件没有反复重连报错。
5. **群里有反应**：在白名单群里 @ 一下麦麦（`[chat.reply_timing] inevitable_at_reply` 默认 `true`，被 @ 会尽量回复）。

---

## 5. 目录地图（出事先看这几个地方）

| 路径 | 内容 | 能不能删 |
| :--- | :--- | :--- |
| `config/bot_config.toml` | 行为、人设、频率、记忆、WebUI、端口 | ❌ 核心配置 |
| `config/model_config.toml` | 服务商、模型、各任务模型分配 | ❌ 核心配置 |
| `config/old/` | 配置自动升级时的旧版本备份 | ✅ 可清理，但建议留几份 |
| `data/MaiBot.db` | 主数据库：聊天流、人物、表达、统计 | ❌ 删了等于失忆 |
| `data/webui.json` | WebUI 访问 Token 和首次配置状态 | ⚠️ 删了 Token 会重生成 |
| `data/emoji/` | 表情包库 | ⚠️ 删了要重新攒 |
| `data/plugins/` | 插件持久化数据 | ⚠️ 看插件 |
| `logs/app_*.log.jsonl` | 主日志（JSONL），自动轮转 | ✅ |
| `logs/maisaka_prompt/planner`、`/replyer` | Prompt 预览，排查「为什么这么回复」用 | ✅ |
| `logs/plugin_runtime_debug/` | 插件运行时 RPC 调试日志 | ✅ |
| `plugins/` | 插件源码（含适配器） | ❌ |
| `maibot_statistics.html` | 模型调用与花费统计报告，可用 `MAIBOT_STATISTICS_REPORT_PATH` 改路径 | ✅ |

---

## 6. 日常操作

**启动 / 停止**

```bash
uv run python bot.py
```

停止：在终端里 Ctrl+C（会走优雅关停）。

**改配置**

`config/` 下的配置文件有文件监听，保存后会尝试热重载，日志里会打印热重载完成。
涉及端口、WebUI 开关、插件运行时这类启动期绑定的改动，**还是要重启**才生效。

**改语言**

```bash
MAIBOT_LOCALE=en-US uv run python bot.py
```

默认 `zh-CN`。

**看日志**

日志是 JSONL，可以直接 `tail`，也可以在 WebUI 的日志页看实时流。
调试期把 `[log] console_log_level` 调成 `DEBUG`；文件日志默认已经是 `DEBUG`。
第三方库噪音在 `[log] suppress_libraries` 和 `library_log_levels` 里控制。

**版本升级**

1. 停服
2. 备份 `config/`、`data/`（数据库要连 `-wal`、`-shm` 一起，最好停机后再复制）
3. `git pull` + `uv sync`
4. 启动。配置版本变了会**自动升级**，旧文件挪到 `config/old/` 带时间戳备份
5. 启动后翻一遍日志，确认没有升级相关的 ERROR

回滚：切回旧 tag，然后把 `config/old/` 里对应时间戳的配置拷回去。**注意数据库结构是单向的，回滚前务必有备份。**

---

## 7. 排障速查

排查顺序永远是：**日志 → 哪一层断了 → 对应配置**。不要一上来就重装。

### 启动就退出，提示协议
终端输入 `同意` 回车。非交互环境用 `EULA_AGREE` / `PRIVACY_AGREE` 环境变量。
EULA.md / PRIVACY.md 改动后哈希会变，需要重新确认一次。

### 端口被占用
日志里的报错会直接带上要改哪个配置项（`config_hint`）：
- WebUI → `[webui] port`（默认 8001）
- 旧版 WS → `[maim_message] ws_server_port`（默认 8000）
- 新版 API Server → `[maim_message] api_server_port`（默认 8090，且默认不开启）

### WebUI 打不开
1. `[webui] enabled` 是不是 `true`
2. `[webui] host` 默认只绑 `127.0.0.1` 和 `::1`，远程访问要加 `0.0.0.0`
3. `[webui] allowed_ips` 默认只放行 `127.0.0.1`，远程访问要把来源 IP 加进去
4. 走反代时要配 `trusted_proxies` + `trust_xff`，否则拿到的是代理 IP
5. Docker 记得是 `18001` 端口

> ⚠️ 把 WebUI 暴露到公网前，先配好 HTTPS（`secure_cookie = true`）和 IP 白名单。它能改配置、能看聊天记录。

### 登不上 WebUI / Token 找不到
看启动日志里打印的 Token，或读 `data/webui.json` 的 `access_token`。
没配固定 Token 时是「每次启动一换」的临时 Token。

### 模型报错（401 / 404 / 超时）
- 401/403 → `[[api_providers]]` 的 `api_key`、`auth_type`（`bearer` / `header` / `query` / `none`）
- 404 → `base_url` 或 `model_identifier` 写错
- 一直超时 → 看任务的 `hard_timeout`，以及是不是上游代理在排队；`slow_threshold` 超了日志会有慢请求警告
- 模型不支持非流式 → 该模型加 `force_stream_mode = true`

### 麦麦完全不说话
按这个顺序查：
1. 适配器有没有连上 NapCat（日志里有没有重连循环）
2. 群号在不在 `plugins/napcat_adapter/config.toml` 的 `group_list` 白名单里
3. `[chat.reply_timing] talk_value` 是不是被调得太低
4. 是不是进了不回复退避：连续不回复会按 `no_action_backoff_*` 逐步拉长检查间隔
5. 翻 `logs/maisaka_prompt/planner`，看 Planner 到底决定了什么

### 图片识别不了
启动日志有 `未配置视觉识图模型` 就是没配 `[model_task_config.vlm]`。

### 长期记忆没生效
先看总开关 `[a_memorix.plugin] enabled` 是不是 `true`（默认是 `false`）。
再看 `[model_task_config.embedding]` 必须配**真正的 embedding 模型**，填 LLM 会直接不可用。

### 插件反复重启
看 `logs/plugin_runtime_debug/`，以及 `[plugin_runtime]` 的 `max_restart_attempts`、`runner_spawn_timeout_sec`。
插件需要网页渲染时，浏览器会懒加载下载到 `data/playwright-browsers`；Linux 上缺系统依赖要先 `playwright install-deps chromium`。

### 磁盘涨得快
- 图片缓存：`[visual.image_cache_cleanup]`
- 表情缓存：`[emoji.cache_cleanup]`
- 日志：`[log] log_cleanup_days`、`max_log_files`、`log_file_max_bytes`
- `[database] save_binary_data` 开着会存语音原文件，很占空间

---

## 8. 安全提醒

- `config/model_config.toml` 里有明文 API Key，**不要提交、不要截图、不要贴群里**
- WebUI 默认只监听本机是有意为之，往外开之前先想清楚
- `[telemetry] enable` 控制匿名统计，介意就关掉，不影响功能

---

## 9. 如果你是来改代码的

- 依赖以 `pyproject.toml` 为准，改动要同步 `requirements.txt`
- 装开发依赖：`uv sync --group dev`
- Lint / 格式化：`uv run ruff check --fix` 和 `uv run ruff format`（已配 pre-commit）
- 跑测试：`uv run pytest -q pytests/...`
- 改配置项时**不要直接改 `config/*.toml`**，改配置模型定义（`src/config/official_configs.py` / `model_configs.py`）并提升版本号，实际配置文件会自动升级生成
- WebUI 开发服务固定起在 **7999** 端口
- 其余约定见根目录 `AGENTS.md`（`CLAUDE.md` 是它的软链）

---

## 10. 卡住了去哪儿问

- 官方文档：<https://docs.mai-mai.org>
- 部署教程：<https://docs.mai-mai.org/manual/deployment/>
- 一键启动器（Windows/Mac）：<https://github.com/Mai-with-u/MaiBotOneKey/releases/>
- 社区群：见仓库 [README](../README.md) 的「讨论与社区」

提问时请带上：**版本号 / 分支、运行方式（源码还是 Docker）、完整报错日志、你改过哪些配置**。
