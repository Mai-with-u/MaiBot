# 导入与重构规范
标准库和第三方库的导入放在本地模块之前，先写 from ... import ...，再写 import ...，两组各自在确保导入正常的前提下按字母排序。同目录的本地模块用相对导入，跨目录用 from src... 绝对导入，并尽量按 src 下的一级目录归组。在确保导入正常的前提下，同目录的相对导入顺序和各一级目录分组之间的顺序均不限。重构时，只要对应功能仍保留，就应保留原有注释和类型注解，并按需更新以保持准确，但是更新不要留下兜底或者反复修改痕迹的防御性说明，为逻辑复杂或参数较多且缺少类型注解的函数补充注解，简单变量可省略。参数化泛型使用 typing 中的类型，如 List[int] 和 Dict[str, Any]。判断变量或实例的类型时优先依据类型注解，除非分析确认注解有误。确认值的类型后，可以省略仅为保证类型而写的 or 兜底。例如，确认 global_config.bot.nickname 一定是字符串时，可将 (global_config.bot.nickname or "").strip() 简化为 global_config.bot.nickname.strip()。应该尽量减少使用getattr和setattr方法，除非是在对一个动态类进行处理或者使用Monkeypatch完成Pytest。在重构代码时，如果遇到getattr和setattr，应该尝试检查这个类实例是否有这个属性，如果有，则直接替换为类属性访问写法。举例：`v = getattr(instance, "value", "")` 在检查到`instance`有`value`属性后应该改为`v = instance.value`

# 运行/调试/构建/测试/依赖
1. 不要总是想找兜底，一定要精准的找到问题的核心，然后提出建议，兜底是不合适，难以维护的。
2. 不要总是考虑fallback，如果哪里有错误，一定要让他及时完整的暴露，而不是用fall_back兜底掩盖过去
优先使用uv，依赖项以 pyproject.toml 为准，如有修改要同步更新requirements.txt

# Webui规范和设计规范
对于dashboard的前端测试，在细节改动，改动范围小时，别测试，别build，直接快速做，避免无谓的时间浪费和代码浪费
修改完webui，如果是小改动小修复，不用急着npm run build。当完成一个较大功能新增或者较广重构时，才需要运行 npm run build。
WebUI 开发服务固定起到 7999 端口。
webui中设计聊天会话流的，涉及显示聊天流信息的，优先显示聊天流实际名称（群名称或 xxx的私聊），而不是session_id
Webui页面优先使用公共组件库内已有的元素，如果无法满足需求，可以先编写公共组件然后再使用并阐述使用了什么新组件。

# 配置文件修改
如果你需要改动配置文件，不需要修改实际的bot_config.toml或者model_config.toml，只需要修改配置文件模版，并新增一个版本号即可，也不必要为配置改动创建测试文件。
除非明确说明，否则不要擅自新增 ConfigUpgradeHook
禁止改动 legacy_migration，此文件已固定

# 事件循环规范
不要阻塞事件循环。同一进程里有两个事件循环（bot 主循环、WebUI 独立线程的循环），任何一个被同步工作占住，界面上对应的一侧就会整体卡住。

- 路由处理器只做同步工作（查数据库、读文件、算数据）时写成普通 `def`，不要写 `async def`：FastAPI 会把 `def` 端点交给线程池执行，而 `async def` 会占用事件循环。只有确实需要 `await`，或需要「当前有运行中的事件循环」（`asyncio.create_task`、`asyncio.get_running_loop` 等）时，才写 `async def`。
- `async def` 函数体内不要直接调用同步阻塞接口：`get_db_session()`、`session.exec()`、`find_messages()`、`metadata_store.query()`、批量文件读写等，应放进 `asyncio.to_thread(...)`。
- WebUI 同步端点的并发数由 `src/webui/app.py` 的 `limit_sync_endpoint_concurrency()` 限制在 SQLite 连接池容量以内，新增同步端点不需要再单独控制并发。
- 主循环与 WebUI 循环各挂一个卡顿看门狗（`src/common/event_loop_watchdog.py`）。日志里出现「事件循环卡顿: loop=... 迟到=...s」即可看出是哪一侧被阻塞、卡了多久。

# 会话 ID 规范
除聊天流创建/注册链路外，业务模块不应自行调用 `SessionUtils.calculate_session_id` 计算资源归属 ID。表达学习、黑话、记忆、WebUI、配置匹配等模块应通过 `chat_manager` 的内部接口，基于 platform、目标 ID 和聊天类型解析已存在的真实聊天流；如果解析不到真实 `ChatSession.session_id`，不要把自行计算的 fallback hash 写入数据库。

# A_memorix 修改
如果修改涉及 `src/A_memorix`，请先阅读 `src/A_memorix/MODIFICATION_POLICY.md`。
1. `src/A_memorix` 的实现层改动应优先遵守 `src/A_memorix/MODIFICATION_POLICY.md` 中的归属约束。
2. 本地实验目录或依赖其运行的测试，除非明确说明并确认，否则不要进入共享历史。

# prompt模板、
涉及对prompt模板的修改，要同步修改英文和日文的文件，对齐到中文

# maibot插件开发文档
https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/guide.md

如果你要编写插件，不要改动根目录的.gitignore，而是在/plugins下创建独立仓库，然后进行编写
如果你要编写插件有需求需要改动主程序代码，请你先请求许可。
如果要提交maibot插件，请参考 https://github.com/Mai-with-u/plugin-repo/blob/main/CONTRIBUTING.md

# changelog编写
建议分为两部分，一部分是主要功能，一部分是细节（包含修复和插件sdk,api改动）。最好一个功能一行，按模块分。
一般不写入changelog的内容：
版本号提升或更新项目依赖
