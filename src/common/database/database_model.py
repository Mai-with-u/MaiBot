from peewee import Model, DoubleField, IntegerField, BooleanField, TextField, FloatField, DateTimeField
from .database import db
import datetime
from src.common.logger import get_logger

logger = get_logger("database_model")
# 请在此处定义您的数据库实例。
# 您需要取消注释并配置适合您的数据库的部分。
# 例如，对于 SQLite:
# db = SqliteDatabase('MaiBot.db')
#
# 对于 PostgreSQL:
# db = PostgresqlDatabase('your_db_name', user='your_user', password='your_password',
#                         host='localhost', port=5432)
#
# 对于 MySQL:
# db = MySQLDatabase('your_db_name', user='your_user', password='your_password',
#                    host='localhost', port=3306)


# 定义一个基础模型是一个好习惯，所有其他模型都应继承自它。
# 这允许您在一个地方为所有模型指定数据库。


class BaseModel(Model):
    class Meta:
        # 将下面的 'db' 替换为您实际的数据库实例变量名。
        database = db  # 例如: database = my_actual_db_instance
        pass  # 在用户定义数据库实例之前，此处为占位符


class ChatStreams(BaseModel):
    """
    用于存储流式记录数据的模型，类似于提供的 MongoDB 结构。
    """

    # stream_id: "a544edeb1a9b73e3e1d77dff36e41264"
    # 假设 stream_id 是唯一的，并为其创建索引以提高查询性能。
    stream_id = TextField(unique=True, index=True)

    # create_time: 1746096761.4490178 (时间戳，精确到小数点后7位)
    # DoubleField 用于存储浮点数，适合此类时间戳。
    create_time = DoubleField()

    # group_info 字段:
    #   platform: "qq"
    #   group_id: "941657197"
    #   group_name: "测试"
    group_platform = TextField(null=True)  # 群聊信息可能不存在
    group_id = TextField(null=True)
    group_name = TextField(null=True)

    # last_active_time: 1746623771.4825106 (时间戳，精确到小数点后7位)
    last_active_time = DoubleField()

    # platform: "qq" (顶层平台字段)
    platform = TextField()

    # user_info 字段:
    #   platform: "qq"
    #   user_id: "1787882683"
    #   user_nickname: "墨梓柒(IceSakurary)"
    #   user_cardname: ""
    user_platform = TextField()
    user_id = TextField()
    user_nickname = TextField()
    # user_cardname 可能为空字符串或不存在，设置 null=True 更具灵活性。
    user_cardname = TextField(null=True)

    class Meta:
        # 如果 BaseModel.Meta.database 已设置，则此模型将继承该数据库配置。
        # 如果不使用带有数据库实例的 BaseModel，或者想覆盖它，
        # 请取消注释并在下面设置数据库实例：
        # database = db
        table_name = "chat_streams"  # 可选：明确指定数据库中的表名


class LLMUsage(BaseModel):
    """
    用于存储 API 使用日志数据的模型。
    """

    model_name = TextField(index=True)  # 添加索引
    model_assign_name = TextField(null=True)  # 添加索引
    model_api_provider = TextField(null=True)  # 添加索引
    user_id = TextField(index=True)  # 添加索引
    request_type = TextField(index=True)  # 添加索引
    endpoint = TextField()
    prompt_tokens = IntegerField()
    completion_tokens = IntegerField()
    total_tokens = IntegerField()
    cost = DoubleField()
    time_cost = DoubleField(null=True)
    status = TextField()
    timestamp = DateTimeField(index=True)  # 更改为 DateTimeField 并添加索引

    class Meta:
        # 如果 BaseModel.Meta.database 已设置，则此模型将继承该数据库配置。
        # database = db
        table_name = "llm_usage"


class Emoji(BaseModel):
    """表情包"""

    full_path = TextField(unique=True, index=True)  # 文件的完整路径 (包括文件名)
    format = TextField()  # 图片格式
    emoji_hash = TextField(index=True)  # 表情包的哈希值
    description = TextField()  # 表情包的描述
    query_count = IntegerField(default=0)  # 查询次数（用于统计表情包被查询描述的次数）
    is_registered = BooleanField(default=False)  # 是否已注册
    is_banned = BooleanField(default=False)  # 是否被禁止注册
    # emotion: list[str]  # 表情包的情感标签 - 存储为文本，应用层处理序列化/反序列化
    emotion = TextField(null=True)
    record_time = FloatField()  # 记录时间（被创建的时间）
    register_time = FloatField(null=True)  # 注册时间（被注册为可用表情包的时间）
    usage_count = IntegerField(default=0)  # 使用次数（被使用的次数）
    last_used_time = FloatField(null=True)  # 上次使用时间

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "emoji"


class Messages(BaseModel):
    """
    用于存储消息数据的模型。
    """

    message_id = TextField(index=True)  # 消息 ID (更改自 IntegerField)
    time = DoubleField()  # 消息时间戳

    chat_id = TextField(index=True)  # 对应的 ChatStreams stream_id

    reply_to = TextField(null=True)

    interest_value = DoubleField(null=True)
    key_words = TextField(null=True)
    key_words_lite = TextField(null=True)

    is_mentioned = BooleanField(null=True)
    is_at = BooleanField(null=True)
    reply_probability_boost = DoubleField(null=True)
    # 从 chat_info 扁平化而来的字段
    chat_info_stream_id = TextField()
    chat_info_platform = TextField()
    chat_info_user_platform = TextField()
    chat_info_user_id = TextField()
    chat_info_user_nickname = TextField()
    chat_info_user_cardname = TextField(null=True)
    chat_info_group_platform = TextField(null=True)  # 群聊信息可能不存在
    chat_info_group_id = TextField(null=True)
    chat_info_group_name = TextField(null=True)
    chat_info_create_time = DoubleField()
    chat_info_last_active_time = DoubleField()

    # 从顶层 user_info 扁平化而来的字段 (消息发送者信息)
    user_platform = TextField(null=True)
    user_id = TextField(null=True)
    user_nickname = TextField(null=True)
    user_cardname = TextField(null=True)

    processed_plain_text = TextField(null=True)  # 处理后的纯文本消息
    display_message = TextField(null=True)  # 显示的消息

    priority_mode = TextField(null=True)
    priority_info = TextField(null=True)

    additional_config = TextField(null=True)
    is_emoji = BooleanField(default=False)
    is_picid = BooleanField(default=False)
    is_command = BooleanField(default=False)
    is_no_read_command = BooleanField(default=False)
    is_notify = BooleanField(default=False)

    selected_expressions = TextField(null=True)

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "messages"


class ActionRecords(BaseModel):
    """
    用于存储动作记录数据的模型。
    """

    action_id = TextField(index=True)  # 消息 ID (更改自 IntegerField)
    time = DoubleField()  # 消息时间戳

    action_reasoning = TextField(null=True)

    action_name = TextField()
    action_data = TextField()
    action_done = BooleanField(default=False)

    action_build_into_prompt = BooleanField(default=False)
    action_prompt_display = TextField()

    chat_id = TextField(index=True)  # 对应的 ChatStreams stream_id
    chat_info_stream_id = TextField()
    chat_info_platform = TextField()

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "action_records"


class Images(BaseModel):
    """
    用于存储图像信息的模型。
    """

    image_id = TextField(default="")  # 图片唯一ID
    emoji_hash = TextField(index=True)  # 图像的哈希值
    description = TextField(null=True)  # 图像的描述
    path = TextField(unique=True)  # 图像文件的路径
    # base64 = TextField()  # 图片的base64编码
    count = IntegerField(default=1)  # 图片被引用的次数
    timestamp = FloatField()  # 时间戳
    type = TextField()  # 图像类型，例如 "emoji"
    vlm_processed = BooleanField(default=False)  # 是否已经过VLM处理

    class Meta:
        table_name = "images"


class ImageDescriptions(BaseModel):
    """
    用于存储图像描述信息的模型。
    """

    type = TextField()  # 类型，例如 "emoji"
    image_description_hash = TextField(index=True)  # 图像的哈希值
    description = TextField()  # 图像的描述
    timestamp = FloatField()  # 时间戳

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "image_descriptions"


class EmojiDescriptionCache(BaseModel):
    """
    存储表情包的详细描述和情感标签缓存
    """

    emoji_hash = TextField(unique=True, index=True)
    description = TextField()  # 详细描述
    emotion_tags = TextField(null=True)  # 情感标签，逗号分隔
    timestamp = FloatField()

    class Meta:
        table_name = "emoji_description_cache"


class OnlineTime(BaseModel):
    """
    用于存储在线时长记录的模型。
    """

    # timestamp: "$date": "2025-05-01T18:52:18.191Z" (存储为字符串)
    timestamp = TextField(default=datetime.datetime.now)  # 时间戳
    duration = IntegerField()  # 时长，单位分钟
    start_timestamp = DateTimeField(default=datetime.datetime.now)
    end_timestamp = DateTimeField(index=True)

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "online_time"


class PersonInfo(BaseModel):
    """
    用于存储个人信息数据的模型。
    """

    is_known = BooleanField(default=False)  # 是否已认识
    person_id = TextField(unique=True, index=True)  # 个人唯一ID
    person_name = TextField(null=True)  # 个人名称 (允许为空)
    name_reason = TextField(null=True)  # 名称设定的原因
    platform = TextField()  # 平台
    user_id = TextField(index=True)  # 用户ID
    nickname = TextField(null=True)  # 用户昵称
    group_nick_name = TextField(null=True)  # 群昵称列表 (JSON格式，存储 [{"group_id": str, "group_nick_name": str}])
    memory_points = TextField(null=True)  # 个人印象的点
    know_times = FloatField(null=True)  # 认识时间 (时间戳)
    know_since = FloatField(null=True)  # 首次印象总结时间
    last_know = FloatField(null=True)  # 最后一次印象总结时间

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "person_info"


class GroupInfo(BaseModel):
    """
    用于存储群组信息数据的模型。
    """

    group_id = TextField(unique=True, index=True)  # 群组唯一ID
    group_name = TextField(null=True)  # 群组名称 (允许为空)
    platform = TextField()  # 平台
    group_impression = TextField(null=True)  # 群组印象
    member_list = TextField(null=True)  # 群成员列表 (JSON格式)
    topic = TextField(null=True)  # 群组基本信息

    create_time = FloatField(null=True)  # 创建时间 (时间戳)
    last_active = FloatField(null=True)  # 最后活跃时间
    member_count = IntegerField(null=True, default=0)  # 成员数量

    class Meta:
        # database = db # 继承自 BaseModel
        table_name = "group_info"


class Expression(BaseModel):
    """
    用于存储表达风格的模型。
    """

    situation = TextField()
    style = TextField()

    # new mode fields
    context = TextField(null=True)
    up_content = TextField(null=True)

    content_list = TextField(null=True)
    count = IntegerField(default=1)
    last_active_time = FloatField()
    chat_id = TextField(index=True)
    create_date = FloatField(null=True)  # 创建日期，允许为空以兼容老数据
    checked = BooleanField(default=False)  # 是否已检查
    rejected = BooleanField(default=False)  # 是否被拒绝但未更新

    class Meta:
        table_name = "expression"


class Jargon(BaseModel):
    """
    用于存储俚语的模型
    """

    content = TextField()
    raw_content = TextField(null=True)
    meaning = TextField(null=True)
    chat_id = TextField(index=True)
    is_global = BooleanField(default=False)
    count = IntegerField(default=0)
    is_jargon = BooleanField(null=True)  # None表示未判定，True表示是黑话，False表示不是黑话
    last_inference_count = IntegerField(null=True)  # 最后一次判定的count值，用于避免重启后重复判定
    is_complete = BooleanField(default=False)  # 是否已完成所有推断（count>=100后不再推断）
    inference_with_context = TextField(null=True)  # 基于上下文的推断结果（JSON格式）
    inference_content_only = TextField(null=True)  # 仅基于词条的推断结果（JSON格式）

    class Meta:
        table_name = "jargon"


class ChatHistory(BaseModel):
    """
    用于存储聊天历史概括的模型
    """

    chat_id = TextField(index=True)  # 聊天ID
    start_time = DoubleField()  # 起始时间
    end_time = DoubleField()  # 结束时间
    original_text = TextField()  # 对话原文
    participants = TextField()  # 参与的所有人的昵称，JSON格式存储
    theme = TextField()  # 主题：这段对话的主要内容，一个简短的标题
    keywords = TextField()  # 关键词：这段对话的关键词，JSON格式存储
    summary = TextField()  # 概括：对这段话的平文本概括
    key_point = TextField(null=True)  # 关键信息：话题中的关键信息点，JSON格式存储
    count = IntegerField(default=0)  # 被检索次数
    forget_times = IntegerField(default=0)  # 被遗忘检查的次数

    class Meta:
        table_name = "chat_history"


class ThinkingBack(BaseModel):
    """
    用于存储记忆检索思考过程的模型
    """

    chat_id = TextField(index=True)  # 聊天ID
    question = TextField()  # 提出的问题
    context = TextField(null=True)  # 上下文信息
    found_answer = BooleanField(default=False)  # 是否找到答案
    answer = TextField(null=True)  # 答案内容
    thinking_steps = TextField(null=True)  # 思考步骤（JSON格式）
    create_time = DoubleField()  # 创建时间
    update_time = DoubleField()  # 更新时间

    class Meta:
        table_name = "thinking_back"


MODELS = [
    ChatStreams,
    LLMUsage,
    Emoji,
    Messages,
    Images,
    ImageDescriptions,
    EmojiDescriptionCache,
    OnlineTime,
    PersonInfo,
    Expression,
    ActionRecords,
    Jargon,
    ChatHistory,
    ThinkingBack,
]


def create_tables():
    """
    创建所有在模型中定义的数据库表。
    """
    with db:
        db.create_tables(MODELS)


def initialize_database(sync_constraints=False):
    """
    检查所有定义的表是否存在，如果不存在则创建它们。
    检查所有表的所有字段是否存在，如果缺失则自动添加。

    Args:
        sync_constraints (bool): 是否同步字段约束。默认为 False。
                               如果为 True，会检查并修复字段的 NULL 约束不一致问题。
    """

    try:
        with db:  # 管理 table_exists 检查的连接
            for model in MODELS:
                table_name = model._meta.table_name
                if not db.table_exists(model):
                    logger.warning(f"表 '{table_name}' 未找到，正在创建...")
                    db.create_tables([model])
                    logger.info(f"表 '{table_name}' 创建成功")
                    continue

                # 检查字段
                cursor = db.execute_sql(f"PRAGMA table_info('{table_name}')")
                existing_columns = {row[1] for row in cursor.fetchall()}
                model_fields = set(model._meta.fields.keys())

                if missing_fields := model_fields - existing_columns:
                    logger.warning(f"表 '{table_name}' 缺失字段: {missing_fields}")

                for field_name, field_obj in model._meta.fields.items():
                    if field_name not in existing_columns:
                        logger.info(f"表 '{table_name}' 缺失字段 '{field_name}'，正在添加...")
                        field_type = field_obj.__class__.__name__
                        sql_type = {
                            "TextField": "TEXT",
                            "IntegerField": "INTEGER",
                            "FloatField": "FLOAT",
                            "DoubleField": "DOUBLE",
                            "BooleanField": "INTEGER",
                            "DateTimeField": "DATETIME",
                        }.get(field_type, "TEXT")
                        alter_sql = f"ALTER TABLE {table_name} ADD COLUMN {field_name} {sql_type}"
                        alter_sql += " NULL" if field_obj.null else " NOT NULL"
                        if hasattr(field_obj, "default") and field_obj.default is not None:
                            # 正确处理不同类型的默认值，跳过lambda函数
                            default_value = field_obj.default
                            if callable(default_value):
                                # 跳过lambda函数或其他可调用对象，这些无法在SQL中表示
                                pass
                            elif isinstance(default_value, str):
                                alter_sql += f" DEFAULT '{default_value}'"
                            elif isinstance(default_value, bool):
                                alter_sql += f" DEFAULT {int(default_value)}"
                            else:
                                alter_sql += f" DEFAULT {default_value}"
                        try:
                            db.execute_sql(alter_sql)
                            logger.info(f"字段 '{field_name}' 添加成功")
                        except Exception as e:
                            logger.error(f"添加字段 '{field_name}' 失败: {e}")

                # 检查并删除多余字段（新增逻辑）
                extra_fields = existing_columns - model_fields
                if extra_fields:
                    logger.warning(f"表 '{table_name}' 存在多余字段: {extra_fields}")
                for field_name in extra_fields:
                    try:
                        logger.warning(f"表 '{table_name}' 存在多余字段 '{field_name}'，正在尝试删除...")
                        db.execute_sql(f"ALTER TABLE {table_name} DROP COLUMN {field_name}")
                        logger.info(f"字段 '{field_name}' 删除成功")
                    except Exception as e:
                        logger.error(f"删除字段 '{field_name}' 失败: {e}")

        # 如果启用了约束同步，执行约束检查和修复
        if sync_constraints:
            logger.debug("开始同步数据库字段约束...")
            sync_field_constraints()
            logger.debug("数据库字段约束同步完成")

    except Exception as e:
        logger.exception(f"检查表或字段是否存在时出错: {e}")
        # 如果检查失败（例如数据库不可用），则退出
        return

    logger.info("数据库初始化完成")


def sync_field_constraints():
    """
    同步数据库字段约束，确保现有数据库字段的 NULL 约束与模型定义一致。
    如果发现不一致，会自动修复字段约束。
    """

    try:
        with db:
            for model in MODELS:
                table_name = model._meta.table_name
                if not db.table_exists(model):
                    logger.warning(f"表 '{table_name}' 不存在，跳过约束检查")
                    continue

                logger.debug(f"检查表 '{table_name}' 的字段约束...")

                # 获取当前表结构信息
                cursor = db.execute_sql(f"PRAGMA table_info('{table_name}')")
                current_schema = {
                    row[1]: {"type": row[2], "notnull": bool(row[3]), "default": row[4]} for row in cursor.fetchall()
                }

                # 检查每个模型字段的约束
                constraints_to_fix = []
                for field_name, field_obj in model._meta.fields.items():
                    if field_name not in current_schema:
                        continue  # 字段不存在，跳过

                    current_notnull = current_schema[field_name]["notnull"]
                    model_allows_null = field_obj.null

                    # 如果模型允许 null 但数据库字段不允许 null，需要修复
                    if model_allows_null and current_notnull:
                        constraints_to_fix.append(
                            {
                                "field_name": field_name,
                                "field_obj": field_obj,
                                "action": "allow_null",
                                "current_constraint": "NOT NULL",
                                "target_constraint": "NULL",
                            }
                        )
                        logger.warning(f"字段 '{field_name}' 约束不一致: 模型允许NULL，但数据库为NOT NULL")

                    # 如果模型不允许 null 但数据库字段允许 null，也需要修复（但要小心）
                    elif not model_allows_null and not current_notnull:
                        constraints_to_fix.append(
                            {
                                "field_name": field_name,
                                "field_obj": field_obj,
                                "action": "disallow_null",
                                "current_constraint": "NULL",
                                "target_constraint": "NOT NULL",
                            }
                        )
                        logger.warning(f"字段 '{field_name}' 约束不一致: 模型不允许NULL，但数据库允许NULL")

                # 修复约束不一致的字段
                if constraints_to_fix:
                    logger.info(f"表 '{table_name}' 需要修复 {len(constraints_to_fix)} 个字段约束")
                    _fix_table_constraints(table_name, model, constraints_to_fix)
                else:
                    logger.debug(f"表 '{table_name}' 的字段约束已同步")

    except Exception as e:
        logger.exception(f"同步字段约束时出错: {e}")


def _fix_table_constraints(table_name, model, constraints_to_fix):
    """
    修复表的字段约束。
    对于 SQLite，由于不支持直接修改列约束，需要重建表。
    """
    try:
        # 备份表名
        backup_table = f"{table_name}_backup_{int(datetime.datetime.now().timestamp())}"

        logger.info(f"开始修复表 '{table_name}' 的字段约束...")

        # 1. 创建备份表
        db.execute_sql(f"CREATE TABLE {backup_table} AS SELECT * FROM {table_name}")
        logger.info(f"已创建备份表 '{backup_table}'")

        # 2. 删除原表
        db.execute_sql(f"DROP TABLE {table_name}")
        logger.info(f"已删除原表 '{table_name}'")

        # 3. 重新创建表（使用当前模型定义）
        db.create_tables([model])
        logger.info(f"已重新创建表 '{table_name}' 使用新的约束")

        # 4. 从备份表恢复数据
        # 获取字段列表
        fields = list(model._meta.fields.keys())
        fields_str = ", ".join(fields)

        # 对于需要从 NOT NULL 改为 NULL 的字段，直接复制数据
        # 对于需要从 NULL 改为 NOT NULL 的字段，需要处理 NULL 值
        insert_sql = f"INSERT INTO {table_name} ({fields_str}) SELECT {fields_str} FROM {backup_table}"

        # 检查是否有字段需要从 NULL 改为 NOT NULL
        null_to_notnull_fields = [
            constraint["field_name"] for constraint in constraints_to_fix if constraint["action"] == "disallow_null"
        ]

        if null_to_notnull_fields:
            # 需要处理 NULL 值，为这些字段设置默认值
            logger.warning(f"字段 {null_to_notnull_fields} 将从允许NULL改为不允许NULL，需要处理现有的NULL值")

            # 构建更复杂的 SELECT 语句来处理 NULL 值
            select_fields = []
            for field_name in fields:
                if field_name in null_to_notnull_fields:
                    field_obj = model._meta.fields[field_name]
                    # 根据字段类型设置默认值
                    if isinstance(field_obj, (TextField,)):
                        default_value = "''"
                    elif isinstance(field_obj, (IntegerField, FloatField, DoubleField)):
                        default_value = "0"
                    elif isinstance(field_obj, BooleanField):
                        default_value = "0"
                    elif isinstance(field_obj, DateTimeField):
                        default_value = f"'{datetime.datetime.now()}'"
                    else:
                        default_value = "''"

                    select_fields.append(f"COALESCE({field_name}, {default_value}) as {field_name}")
                else:
                    select_fields.append(field_name)

            select_str = ", ".join(select_fields)
            insert_sql = f"INSERT INTO {table_name} ({fields_str}) SELECT {select_str} FROM {backup_table}"

        db.execute_sql(insert_sql)
        logger.info(f"已从备份表恢复数据到 '{table_name}'")

        # 5. 验证数据完整性
        original_count = db.execute_sql(f"SELECT COUNT(*) FROM {backup_table}").fetchone()[0]
        new_count = db.execute_sql(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        if original_count == new_count:
            logger.info(f"数据完整性验证通过: {original_count} 行数据")
            # 删除备份表
            db.execute_sql(f"DROP TABLE {backup_table}")
            logger.info(f"已删除备份表 '{backup_table}'")
        else:
            logger.error(f"数据完整性验证失败: 原始 {original_count} 行，新表 {new_count} 行")
            logger.error(f"备份表 '{backup_table}' 已保留，请手动检查")

        # 记录修复的约束
        for constraint in constraints_to_fix:
            logger.info(
                f"已修复字段 '{constraint['field_name']}': "
                f"{constraint['current_constraint']} -> {constraint['target_constraint']}"
            )

    except Exception as e:
        logger.exception(f"修复表 '{table_name}' 约束时出错: {e}")
        # 尝试恢复
        try:
            if db.table_exists(backup_table):
                logger.info(f"尝试从备份表 '{backup_table}' 恢复...")
                db.execute_sql(f"DROP TABLE IF EXISTS {table_name}")
                db.execute_sql(f"ALTER TABLE {backup_table} RENAME TO {table_name}")
                logger.info(f"已从备份恢复表 '{table_name}'")
        except Exception as restore_error:
            logger.exception(f"恢复表失败: {restore_error}")


def check_field_constraints():
    """
    检查但不修复字段约束，返回不一致的字段信息。
    用于在修复前预览需要修复的内容。
    """

    inconsistencies = {}

    try:
        with db:
            for model in MODELS:
                table_name = model._meta.table_name
                if not db.table_exists(model):
                    continue

                # 获取当前表结构信息
                cursor = db.execute_sql(f"PRAGMA table_info('{table_name}')")
                current_schema = {
                    row[1]: {"type": row[2], "notnull": bool(row[3]), "default": row[4]} for row in cursor.fetchall()
                }

                table_inconsistencies = []

                # 检查每个模型字段的约束
                for field_name, field_obj in model._meta.fields.items():
                    if field_name not in current_schema:
                        continue

                    current_notnull = current_schema[field_name]["notnull"]
                    model_allows_null = field_obj.null

                    if model_allows_null and current_notnull:
                        table_inconsistencies.append(
                            {
                                "field_name": field_name,
                                "issue": "model_allows_null_but_db_not_null",
                                "model_constraint": "NULL",
                                "db_constraint": "NOT NULL",
                                "recommended_action": "allow_null",
                            }
                        )
                    elif not model_allows_null and not current_notnull:
                        table_inconsistencies.append(
                            {
                                "field_name": field_name,
                                "issue": "model_not_null_but_db_allows_null",
                                "model_constraint": "NOT NULL",
                                "db_constraint": "NULL",
                                "recommended_action": "disallow_null",
                            }
                        )

                if table_inconsistencies:
                    inconsistencies[table_name] = table_inconsistencies

    except Exception as e:
        logger.exception(f"检查字段约束时出错: {e}")

    return inconsistencies


def fix_image_id():
    """
    修复表情包的 image_id 字段
    """
    import uuid

    try:
        with db:
            for img in Images.select():
                if not img.image_id:
                    img.image_id = str(uuid.uuid4())
                    img.save()
                    logger.info(f"已为表情包 {img.id} 生成新的 image_id: {img.image_id}")
    except Exception as e:
        logger.exception(f"修复 image_id 时出错: {e}")


# 模块加载时调用初始化函数
initialize_database(sync_constraints=True)
fix_image_id()
