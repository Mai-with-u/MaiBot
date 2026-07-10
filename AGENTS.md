# 代码规范
# import 规范
在从外部库进行导入时候，请遵循以下顺序：
1. 对于标准库和第三方库的导入，请按照如下顺序：
    - 需要使用`from ... import ...`语法的导入放在前面。
    - 直接使用`import ...`语法的导入放在后面。
    - 对于使用`from ... import ...`导入的多个项，请**在保证不会引起import错误的前提下**，按照**字母顺序**排列。
    - 对于使用`import ...`导入的多个项，请**在保证不会引起import错误的前提下**，按照**字母顺序**排列。
2. 对于本地模块的导入，请按照如下顺序：
    - 对于同一个文件夹下的模块导入，使用相对导入，排列顺序按照**不发生import错误的前提下**，随便排列。
    - 对于不同文件夹下的模块导入，使用绝对导入。这些导入应该以`from src`开头，并且按照**不发生import错误的前提下**，尽量使得第二层的文件夹名称相同的导入放在一起；第二层文件夹名称排列随机。
3. 标准库和第三方库的导入应该放在本地模块导入的前面。
4. 各个导入块之间应该使用一个空行进行分隔。
5. 对于现有的代码，如果导入顺序不符合上述规范，在重构代码时应该调整导入顺序以符合规范。

## 注释规范
1. 尽量保持良好的注释
2. 如果原来的代码中有注释，则重构的时候，除非这部分代码被删除，否则相同功能的代码应该保留注释（可以对注释进行修改以保持准确性，但不应该删除注释）。
3. 如果原来的代码中没有注释，则重构的时候，如果某个功能块的代码较长或者逻辑较为复杂，则应该添加注释来解释这部分代码的功能和逻辑。
## 类型注解规范
1. 重构代码时，如果原来的代码中有类型注解，则相同功能的代码应该保留类型注解（可以对类型注解进行修改以保持准确性，但不应该删除类型注解）。
2. 重构代码时，如果原来的代码中没有类型注解，则重构的时候，如果某个函数的功能较为复杂或者参数较多，则应该添加类型注解来提高代码的可读性和可维护性。（对于简单的变量，可以不添加类型注解）
3. 对于参数化泛型，应该使用`typing`模块中的类型注解来指定参数化泛型的类型。
    - 例如，使用`List[int]`来表示一个包含整数的列表，使用`Dict[str, Any]`来表示一个键为字符串，值为任意类型的字典。

## 变量规范
1. 当确定某个变量/实例是某种类型的时候（优先按照类型注解确定，除非你分析出类型注解是错误的），可以不必使用`or`进行fallback。
    - 例如，`bot_nickname = (global_config.bot.nickname or "").strip()` 可以改为 `bot_nickname = global_config.bot.nickname.strip()`，前提是我们确定`global_config.bot.nickname`一定是一个字符串。

## 类属性使用规范
1. 应该尽量减少使用getattr和setattr方法，除非是在对一个动态类进行处理或者使用Monkeypatch完成Pytest
2. 在重构代码时，如果遇到getattr和setattr，应该尝试检查这个类实例是否有这个属性，如果有，则直接替换为类属性访问写法。
    - 举例：`v = getattr(instance, "value", "")` 在检查到`instance`有`value`属性后应该改为`v = instance.value`

## debug规范
1. 不要总是想找兜底，一定要精准的找到问题的核心，然后提出建议，兜底是不合适，难以维护的。
2. 不要总是考虑fallback，如果哪里有错误，一定要让他及时完整的暴露，而不是用fall_back兜底掩盖过去

# 运行/调试/构建/测试/依赖
优先使用uv
依赖项以 pyproject.toml 为准，要同步更新requirements.txt
不要总是考虑fallback，如果哪里有错误，一定要让他及时完整的暴露，而不是用fall_back兜底掩盖过去

# 语言规范
项目的首选语言为简体中文，无论是注释语言，日志展示语言，还是 WebUI 展示语言都首要以简体中文为首要实现目标

# 配置文件修改
如果你需要改动配置文件，不需要修改实际的bot_config.toml或者model_config.toml，只需要修改配置文件模版，并新增一个版本号即可，也不必要为配置改动创建测试文件。
除非明确说明，否则不要擅自新增 ConfigUpgradeHook
禁止改动 legacy_migration，此文件以固定

# Webui规范
涉及显示聊天流信息的，优先显示聊天流实际名称（群名称或 xxx的私聊），而不是session_id

如果遇到 UI 高度/布局问题：
对比展开前后 DOM，找新增元素和新增属性。
查 data-dashboard-style 主题样式，尤其是 !important。
查 computed style 的实际 height/min-height，而不是只看 Tailwind class。
如果遇到 UI 底纹、阴影、半透明、模糊或颜色叠加问题，先按 DOM 层级拆分父容器、触发器、内部装饰元素和伪元素，逐层查 computed style 的 background/background-color/background-image/backdrop-filter/box-shadow/opacity，不要只盯着截图中最显眼的子元素或只看 class。
涉及 Tabs/TabsList/TabsTrigger、Radix 或 motion 动画指示器时，要先确认视觉效果来自 TabsList 容器、TabsTrigger 本体、内部 motion/span，还是父级 header/card/dialog 的 backdrop-filter 或主题覆盖，再做最小范围修改。
Radix 组件不随便移出上下文，像 TabsTrigger 必须留在 TabsList 里。

修改完webui，如果是小改动小修复，不用急着npm run build。当完成一个较大功能新增或者较广重构时，才需要运行 npm run build。
WebUI 开发服务固定起到 7999 端口。

# 会话 ID 规范
除聊天流创建/注册链路外，业务模块不应自行调用 `SessionUtils.calculate_session_id` 计算资源归属 ID。表达学习、黑话、记忆、WebUI、配置匹配等模块应通过 `chat_manager` 的内部接口，基于 platform、目标 ID 和聊天类型解析已存在的真实聊天流；如果解析不到真实 `ChatSession.session_id`，不要把自行计算的 fallback hash 写入数据库。

# 关于 A_memorix 修改
如果修改涉及 `src/A_memorix`，请先阅读 `src/A_memorix/MODIFICATION_POLICY.md`。

1. `src/A_memorix` 的实现层改动应优先遵守 `src/A_memorix/MODIFICATION_POLICY.md` 中的归属约束。
2. 不要提交无边界的 `ruff`、格式化、导入整理或大面积实现整理。
3. 本地实验目录或依赖其运行的测试，除非明确说明并确认，否则不要进入共享历史。

# prompt模板、
涉及对prompt模板的修改，要同步修改英文和日文的文件，对齐到中文

# maibot插件开发文档
https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/guide.md

如果你要编写插件，不要改动根目录的.gitignore，而是在/plugins下创建独立仓库，然后进行编写
如果你要编写插件有需求需要改动主程序代码，请你先请求许可。


# 如何提交maibot插件
https://github.com/Mai-with-u/plugin-repo/blob/main/CONTRIBUTING.md

# changelog编写
建议分为两部分，一部分是用户感知功能侧，一部分是开发侧（包含修复和插件sdk,api改动）。最好一个功能一行，按模块分。
一般不写入changelog的内容：
版本号提升或更新项目依赖
