# Mai NEXT Todo list
version 0.3.0 - 2026-01-11

## 配置文件设计
- [x] 使用 `toml` 作为配置文件格式
- [x] <del>合理使用注释说明当前配置作用</del>（提案）
- [x] 使用 python 方法作为配置项说明（提案）
    - [x] 取消`bot_config_template.toml`
    - [x] 取消`model_config_template.toml`
    - [ ] 取消`template_env`
- [x] 配置类中的所有原子项目应该只包含以下类型: `str`, `int`, `float`, `bool`, `list`, `dict`, `set`
    - [x] 禁止使用 `Union` 类型
    - [x] 禁止使用`tuple`类型，使用嵌套`dataclass`替代
    - [x] 复杂类型使用嵌套配置类实现
- [x] 配置类中禁止使用除了`model_post_init`的方法
- [x] 取代了部分与标准函数混淆的命名
    - [x] `id` -> `item_id`

### BotConfig 设计
- [ ] 精简了配置项，现在只有Nickname和Alias Name了（预期将判断提及移到Adapter端）

### ChatConfig
- [x] 迁移了原来在`ChatConfig`中的方法到一个单独的临时类`TempMethodsHFC`中
    - [x] _parse_range
    - [x] get_talk_value
    - [x] 其他上面两个依赖的函数已经合并到这两个函数中

### ExpressionConfig
- [x] 迁移了原来在`ExpressionConfig`中的方法到一个单独的临时类`TempMethodsExpression`中
    - [x] get_expression_config_for_chat
    - [x] 其他上面依赖的函数已经合并到这个函数中

### ModelConfig
- [x] 迁移了原来在`ModelConfig`中的方法到一个单独的临时类`TempMethodsLLMUtils`中
    - [x] get_model_info
    - [x] get_provider