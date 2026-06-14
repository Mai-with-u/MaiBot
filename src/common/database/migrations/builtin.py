"""数据库迁移内置版本与默认注册表。"""

from typing import List, Optional

from .legacy_v1_to_v2 import migrate_legacy_v1_to_v2
from .models import DatabaseSchemaSnapshot, MigrationStep
from .registry import MigrationRegistry
from .resolver import BaseSchemaVersionDetector, SchemaVersionResolver
from .schema import SQLiteSchemaInspector
from .v2_to_v3 import migrate_v2_to_v3
from .v3_to_v4 import migrate_v3_to_v4
from .v4_to_v5 import migrate_v4_to_v5
from .v5_to_v6 import migrate_v5_to_v6
from .v6_to_v7 import migrate_v6_to_v7
from .v7_to_v8 import migrate_v7_to_v8
from .v8_to_v9 import migrate_v8_to_v9
from .v9_to_v10 import migrate_v9_to_v10
from .v10_to_v11 import migrate_v10_to_v11
from .v11_to_v12 import migrate_v11_to_v12
from .v12_to_v13 import migrate_v12_to_v13
from .v13_to_v14 import migrate_v13_to_v14
from .v14_to_v15 import migrate_v14_to_v15
from .v15_to_v16 import migrate_v15_to_v16
from .v16_to_v17 import migrate_v16_to_v17
from .v17_to_v18 import migrate_v17_to_v18
from .v18_to_v19 import migrate_v18_to_v19
from .v19_to_v20 import migrate_v19_to_v20
from .v20_to_v21 import migrate_v20_to_v21
from .v21_to_v22 import LEGACY_V1_CLEANUP_TABLES, migrate_v21_to_v22
from .v22_to_v23 import migrate_v22_to_v23
from .v23_to_v24 import migrate_v23_to_v24
from .v24_to_v25 import migrate_v24_to_v25
from .v25_to_v26 import migrate_v25_to_v26
from .v26_to_v27 import migrate_v26_to_v27
from .version_store import SQLiteUserVersionStore

EMPTY_SCHEMA_VERSION = 0
LEGACY_V1_SCHEMA_VERSION = 1
V2_SCHEMA_VERSION = 2
V3_SCHEMA_VERSION = 3
V4_SCHEMA_VERSION = 4
V5_SCHEMA_VERSION = 5
V6_SCHEMA_VERSION = 6
V7_SCHEMA_VERSION = 7
V8_SCHEMA_VERSION = 8
V9_SCHEMA_VERSION = 9
V10_SCHEMA_VERSION = 10
V11_SCHEMA_VERSION = 11
V12_SCHEMA_VERSION = 12
V13_SCHEMA_VERSION = 13
V14_SCHEMA_VERSION = 14
V15_SCHEMA_VERSION = 15
V16_SCHEMA_VERSION = 16
V17_SCHEMA_VERSION = 17
V18_SCHEMA_VERSION = 18
V19_SCHEMA_VERSION = 19
V20_SCHEMA_VERSION = 20
V21_SCHEMA_VERSION = 21
V22_SCHEMA_VERSION = 22
V23_SCHEMA_VERSION = 23
V24_SCHEMA_VERSION = 24
V25_SCHEMA_VERSION = 25
V26_SCHEMA_VERSION = 26
V27_SCHEMA_VERSION = 27
LATEST_SCHEMA_VERSION = 27

_LEGACY_V1_EXCLUSIVE_TABLES = (
    "chat_streams",
    "emoji",
    "emoji_description_cache",
    "expression",
    "group_info",
    "image_descriptions",
    "jargon",
    "messages",
    "thinking_back",
)
_COMMON_MARKER_TABLES = (
    "mai_messages",
    "chat_sessions",
    "expressions",
    "jargons",
    "tool_records",
)
def _detect_v13_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足 v13 共有结构条件。"""

    if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
        return False
    if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
        return False
    if snapshot.has_table("action_records"):
        return False
    if snapshot.has_table("thinking_questions"):
        return False
    if snapshot.has_column("images", "emotion"):
        return False
    if not snapshot.has_column("images", "image_hash"):
        return False
    if not snapshot.has_column("images", "full_path"):
        return False
    if not snapshot.has_column("images", "image_type"):
        return False
    if snapshot.has_table("chat_history") and not snapshot.has_column("chat_history", "session_id"):
        return False
    if not snapshot.has_column("person_info", "user_nickname"):
        return False
    if not snapshot.has_column("chat_sessions", "account_id"):
        return False
    if not snapshot.has_column("chat_sessions", "scope"):
        return False
    if not snapshot.has_column("chat_sessions", "user_nickname"):
        return False
    if not snapshot.has_column("chat_sessions", "user_cardname"):
        return False
    if not snapshot.has_column("chat_sessions", "group_name"):
        return False
    if snapshot.has_column("expressions", "rejected"):
        return False
    if snapshot.has_column("mai_messages", "display_message"):
        return False
    if not snapshot.has_table("statistics_message_hourly"):
        return False
    if not snapshot.has_table("statistics_tool_hourly"):
        return False
    if not snapshot.has_table("statistics_model_hourly"):
        return False
    if not snapshot.has_table("statistics_aggregation_cursors"):
        return False
    if not snapshot.has_column("jargons", "created_timestamp"):
        return False
    if not snapshot.has_column("jargons", "updated_timestamp"):
        return False
    if snapshot.has_column("jargons", "inference_with_context"):
        return False
    if snapshot.has_column("jargons", "inference_with_content_only"):
        return False
    if not snapshot.has_column("jargons", "created_by"):
        return False
    return True


def _detect_v18_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足 v18 共有结构条件。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_patterns"):
        return False
    if not snapshot.has_column("behavior_patterns", "trigger"):
        return False
    if not snapshot.has_column("behavior_patterns", "action"):
        return False
    if not snapshot.has_column("behavior_patterns", "outcome"):
        return False
    if not snapshot.has_column("behavior_patterns", "score"):
        return False
    if not snapshot.has_column("behavior_patterns", "enabled"):
        return False
    return True


def _detect_v18_common_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足 v18 之后行为学习以外的共有结构条件。"""

    if not _detect_v13_base_schema(snapshot):
        return False
    if not snapshot.has_column("mai_messages", "reply_frequency"):
        return False
    if not snapshot.has_column("llm_usage", "task_name"):
        return False
    if not snapshot.has_column("llm_usage", "prompt_cache_hit_tokens"):
        return False
    if not snapshot.has_column("llm_usage", "prompt_cache_miss_tokens"):
        return False
    if not snapshot.has_column("llm_usage", "prompt_cache_enabled"):
        return False
    if not snapshot.has_table("high_frequency_terms"):
        return False
    if not snapshot.has_column("high_frequency_terms", "term"):
        return False
    if not snapshot.has_column("high_frequency_terms", "normalized_term"):
        return False
    if not snapshot.has_column("high_frequency_terms", "updated_at"):
        return False
    return True


def _detect_v19_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足 v19 节点化行为经验路径结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if snapshot.has_table("command_records"):
        return False
    if snapshot.has_table("behavior_patterns"):
        return False
    if snapshot.has_table("behavior_pattern_scene_links"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "action_node_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "outcome_node_id"):
        return False
    if not snapshot.has_table("behavior_experience_scene_links"):
        return False
    if not snapshot.has_column("behavior_experience_scene_links", "behavior_experience_path_id"):
        return False
    if not snapshot.has_table("behavior_scene_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_edges"):
        return False
    if not snapshot.has_table("behavior_action_nodes"):
        return False
    if snapshot.has_column("behavior_action_nodes", "normalized_action"):
        return False
    if not snapshot.has_table("behavior_outcome_nodes"):
        return False
    if snapshot.has_column("behavior_outcome_nodes", "normalized_outcome"):
        return False
    if not snapshot.has_table("behavior_scene_action_edges"):
        return False
    if not snapshot.has_column("behavior_scene_action_edges", "behavior_experience_path_id"):
        return False
    if snapshot.has_column("behavior_scene_action_edges", "behavior_pattern_id"):
        return False
    if not snapshot.has_table("behavior_action_outcome_edges"):
        return False
    if not snapshot.has_column("behavior_action_outcome_edges", "behavior_experience_path_id"):
        return False
    if snapshot.has_column("behavior_action_outcome_edges", "behavior_pattern_id"):
        return False
    return True


def _detect_v20_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足独立场景簇行为经验路径结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "tag_distribution"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "normalized_tags"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "scene_cluster_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "actor_type"):
        return False
    if snapshot.has_column("behavior_experience_paths", "learning_type"):
        return False
    if snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if not snapshot.has_table("behavior_experience_scene_links"):
        return False
    if not snapshot.has_table("behavior_scene_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_edges"):
        return False
    if not snapshot.has_table("behavior_action_nodes"):
        return False
    if not snapshot.has_table("behavior_outcome_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_action_edges"):
        return False
    if not snapshot.has_table("behavior_action_outcome_edges"):
        return False
    return True


def _detect_v21_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足区分观察学习与自身反馈的行为路径结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "tag_distribution"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "normalized_tags"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "scene_cluster_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "actor_type"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "learning_type"):
        return False
    if snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if not snapshot.has_table("behavior_experience_scene_links"):
        return False
    if not snapshot.has_table("behavior_scene_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_edges"):
        return False
    if not snapshot.has_table("behavior_action_nodes"):
        return False
    if not snapshot.has_table("behavior_outcome_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_action_edges"):
        return False
    if not snapshot.has_table("behavior_action_outcome_edges"):
        return False
    return True


def _detect_v22_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足合并后的行为场景 tag 簇索引结构。"""

    if not _detect_v21_base_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_tag_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "cluster_key"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "normalized_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "display_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "cluster_name"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "tag_members"):
        return False
    if snapshot.has_column("behavior_scene_nodes", "normalized_name"):
        return False
    if not snapshot.has_table("behavior_scene_node_tags"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "scene_node_id"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "cluster_key"):
        return False
    return True


def _detect_v23_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足移除场景簇冗余字段后的行为场景结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_clusters"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "name"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "normalized_tags"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "tag_distribution"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "scene_cluster_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "actor_type"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "learning_type"):
        return False
    if snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if not snapshot.has_table("behavior_experience_scene_links"):
        return False
    if not snapshot.has_table("behavior_scene_nodes"):
        return False
    if snapshot.has_column("behavior_scene_nodes", "normalized_name"):
        return False
    if not snapshot.has_table("behavior_scene_edges"):
        return False
    if not snapshot.has_table("behavior_action_nodes"):
        return False
    if not snapshot.has_table("behavior_outcome_nodes"):
        return False
    if not snapshot.has_table("behavior_scene_action_edges"):
        return False
    if not snapshot.has_table("behavior_action_outcome_edges"):
        return False
    if not snapshot.has_table("behavior_scene_tag_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "cluster_key"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "normalized_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "display_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "cluster_name"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "tag_members"):
        return False
    if not snapshot.has_table("behavior_scene_node_tags"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "scene_node_id"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "cluster_key"):
        return False
    return True


def _detect_v24_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足收敛后的行为动作/结果实体结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_clusters"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "name"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "normalized_tags"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "tag_distribution"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "scene_cluster_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "action_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "outcome_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "action_node_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "outcome_node_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "actor_type"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "learning_type"):
        return False
    if snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if not snapshot.has_table("behavior_experience_scene_links"):
        return False
    if not snapshot.has_table("behavior_scene_nodes"):
        return False
    if snapshot.has_column("behavior_scene_nodes", "normalized_name"):
        return False
    if not snapshot.has_table("behavior_scene_edges"):
        return False
    if not snapshot.has_table("behavior_actions"):
        return False
    if not snapshot.has_column("behavior_actions", "action_hash"):
        return False
    if snapshot.has_column("behavior_actions", "normalized_action"):
        return False
    if snapshot.has_table("behavior_action_nodes"):
        return False
    if not snapshot.has_table("behavior_outcomes"):
        return False
    if not snapshot.has_column("behavior_outcomes", "outcome_hash"):
        return False
    if snapshot.has_column("behavior_outcomes", "normalized_outcome"):
        return False
    if snapshot.has_table("behavior_outcome_nodes"):
        return False
    if snapshot.has_table("behavior_scene_action_edges"):
        return False
    if snapshot.has_table("behavior_action_outcome_edges"):
        return False
    if not snapshot.has_table("behavior_scene_tag_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "cluster_key"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "normalized_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "display_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "cluster_name"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "tag_members"):
        return False
    if not snapshot.has_table("behavior_scene_node_tags"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "scene_node_id"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_node_tags", "cluster_key"):
        return False
    return True


def _detect_v26_base_schema(snapshot: DatabaseSchemaSnapshot) -> bool:
    """判断数据库是否满足移除 scene node 图层后的行为学习结构。"""

    if not _detect_v18_common_schema(snapshot):
        return False
    if not snapshot.has_table("behavior_scene_clusters"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "name"):
        return False
    if snapshot.has_column("behavior_scene_clusters", "normalized_tags"):
        return False
    if not snapshot.has_column("behavior_scene_clusters", "tag_distribution"):
        return False
    if not snapshot.has_table("behavior_experience_paths"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "scene_cluster_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "action_id"):
        return False
    if not snapshot.has_column("behavior_experience_paths", "outcome_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "action_node_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "outcome_node_id"):
        return False
    if snapshot.has_column("behavior_experience_paths", "start_scene_node_id"):
        return False
    if snapshot.has_table("behavior_experience_scene_links"):
        return False
    if snapshot.has_table("behavior_scene_node_tags"):
        return False
    if snapshot.has_table("behavior_scene_edges"):
        return False
    if snapshot.has_table("behavior_scene_nodes"):
        return False
    if not snapshot.has_table("behavior_actions"):
        return False
    if not snapshot.has_column("behavior_actions", "action_hash"):
        return False
    if snapshot.has_column("behavior_actions", "normalized_action"):
        return False
    if snapshot.has_table("behavior_action_nodes"):
        return False
    if not snapshot.has_table("behavior_outcomes"):
        return False
    if not snapshot.has_column("behavior_outcomes", "outcome_hash"):
        return False
    if snapshot.has_column("behavior_outcomes", "normalized_outcome"):
        return False
    if snapshot.has_table("behavior_outcome_nodes"):
        return False
    if snapshot.has_table("behavior_scene_action_edges"):
        return False
    if snapshot.has_table("behavior_action_outcome_edges"):
        return False
    if not snapshot.has_table("behavior_scene_tag_clusters"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag_kind"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "tag"):
        return False
    if not snapshot.has_column("behavior_scene_tag_clusters", "cluster_key"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "normalized_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "display_tag"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "cluster_name"):
        return False
    if snapshot.has_column("behavior_scene_tag_clusters", "tag_members"):
        return False
    return True


class LatestSchemaVersionDetector(BaseSchemaVersionDetector):
    """当前最新 schema 结构探测器。"""

    @property
    def name(self) -> str:
        """返回探测器名称。

        Returns:
            str: 当前探测器名称。
        """

        return "latest_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否已经是当前最新结构。

        Args:
            snapshot: 当前数据库结构快照。

        Returns:
            Optional[int]: 若识别为最新结构则返回最新版本号，否则返回 ``None``。
        """

        if not _detect_v26_base_schema(snapshot):
            return None
        if snapshot.has_column("behavior_scene_clusters", "score"):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return LATEST_SCHEMA_VERSION


class V27SchemaVersionDetector(BaseSchemaVersionDetector):
    """v27 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v27_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v27 结构。"""

        if not _detect_v26_base_schema(snapshot):
            return None
        if snapshot.has_column("behavior_scene_clusters", "score"):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V27_SCHEMA_VERSION


class V24SchemaVersionDetector(BaseSchemaVersionDetector):
    """v24 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v24_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v24 结构。"""

        if not _detect_v24_base_schema(snapshot):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V24_SCHEMA_VERSION


class V26SchemaVersionDetector(BaseSchemaVersionDetector):
    """v26 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v26_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v26 结构。"""

        if not _detect_v26_base_schema(snapshot):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V26_SCHEMA_VERSION


class V25SchemaVersionDetector(BaseSchemaVersionDetector):
    """v25 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v25_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v25 结构。"""

        if not _detect_v24_base_schema(snapshot):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V25_SCHEMA_VERSION


class V23SchemaVersionDetector(BaseSchemaVersionDetector):
    """v23 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v23_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v23 结构。"""

        if not _detect_v23_base_schema(snapshot):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V23_SCHEMA_VERSION


class V22SchemaVersionDetector(BaseSchemaVersionDetector):
    """v22 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v22_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v22 结构。"""

        if not _detect_v22_base_schema(snapshot):
            return None
        if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
            return None
        return V22_SCHEMA_VERSION


class V21SchemaVersionDetector(BaseSchemaVersionDetector):
    """v21 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v21_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v21 结构。"""

        if _detect_v22_base_schema(snapshot):
            if any(snapshot.has_table(table_name) for table_name in LEGACY_V1_CLEANUP_TABLES):
                return V21_SCHEMA_VERSION
            return None
        if not _detect_v21_base_schema(snapshot):
            return None
        if snapshot.has_table("behavior_scene_tag_clusters"):
            return None
        return V21_SCHEMA_VERSION


class V20SchemaVersionDetector(BaseSchemaVersionDetector):
    """v20 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v20_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v20 结构。"""

        if not _detect_v20_base_schema(snapshot):
            return None
        return V20_SCHEMA_VERSION


class V19SchemaVersionDetector(BaseSchemaVersionDetector):
    """v19 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v19_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v19 结构。"""

        if not _detect_v19_base_schema(snapshot):
            return None
        return V19_SCHEMA_VERSION


class V18SchemaVersionDetector(BaseSchemaVersionDetector):
    """v18 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v18_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v18 结构。"""

        if not _detect_v18_base_schema(snapshot):
            return None
        if not snapshot.has_table("command_records"):
            return None
        return V18_SCHEMA_VERSION


class V17SchemaVersionDetector(BaseSchemaVersionDetector):
    """v17 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v17_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v17 结构。"""

        if not _detect_v13_base_schema(snapshot):
            return None
        if not snapshot.has_column("mai_messages", "reply_frequency"):
            return None
        if not snapshot.has_column("llm_usage", "task_name"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_hit_tokens"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_miss_tokens"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_enabled"):
            return None
        if not snapshot.has_table("behavior_patterns"):
            return None
        if not snapshot.has_column("behavior_patterns", "trigger"):
            return None
        if not snapshot.has_column("behavior_patterns", "action"):
            return None
        if not snapshot.has_column("behavior_patterns", "outcome"):
            return None
        if not snapshot.has_column("behavior_patterns", "score"):
            return None
        if not snapshot.has_column("behavior_patterns", "enabled"):
            return None
        if snapshot.has_table("high_frequency_terms"):
            return None
        return V17_SCHEMA_VERSION


class V16SchemaVersionDetector(BaseSchemaVersionDetector):
    """v16 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v16_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v16 结构。"""

        if not _detect_v13_base_schema(snapshot):
            return None
        if not snapshot.has_column("mai_messages", "reply_frequency"):
            return None
        if not snapshot.has_column("llm_usage", "task_name"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_hit_tokens"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_miss_tokens"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_enabled"):
            return None
        if snapshot.has_table("behavior_patterns"):
            return None
        return V16_SCHEMA_VERSION


class V15SchemaVersionDetector(BaseSchemaVersionDetector):
    """v15 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v15_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v15 结构。"""

        if not _detect_v13_base_schema(snapshot):
            return None
        if not snapshot.has_column("mai_messages", "reply_frequency"):
            return None
        if not snapshot.has_column("llm_usage", "task_name"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_hit_tokens"):
            return None
        if not snapshot.has_column("llm_usage", "prompt_cache_miss_tokens"):
            return None
        if snapshot.has_column("llm_usage", "prompt_cache_enabled"):
            return None
        return V15_SCHEMA_VERSION


class V14SchemaVersionDetector(BaseSchemaVersionDetector):
    """v14 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v14_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v14 结构。"""

        if not _detect_v13_base_schema(snapshot):
            return None
        if not snapshot.has_column("mai_messages", "reply_frequency"):
            return None
        if not snapshot.has_column("llm_usage", "task_name"):
            return None
        if snapshot.has_column("llm_usage", "prompt_cache_hit_tokens"):
            return None
        if snapshot.has_column("llm_usage", "prompt_cache_miss_tokens"):
            return None
        return V14_SCHEMA_VERSION


class V13SchemaVersionDetector(BaseSchemaVersionDetector):
    """v13 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v13_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v13 结构。"""

        if not _detect_v13_base_schema(snapshot):
            return None
        if snapshot.has_column("mai_messages", "reply_frequency"):
            return None
        if snapshot.has_column("llm_usage", "task_name"):
            return None
        return V13_SCHEMA_VERSION


class V12SchemaVersionDetector(BaseSchemaVersionDetector):
    """v12 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v12_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v12 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "account_id"):
            return None
        if not snapshot.has_column("chat_sessions", "scope"):
            return None
        if not snapshot.has_column("chat_sessions", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "user_cardname"):
            return None
        if not snapshot.has_column("chat_sessions", "group_name"):
            return None
        if snapshot.has_column("expressions", "rejected"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        if not snapshot.has_table("statistics_message_hourly"):
            return None
        if not snapshot.has_table("statistics_tool_hourly"):
            return None
        if not snapshot.has_table("statistics_model_hourly"):
            return None
        if not snapshot.has_table("statistics_aggregation_cursors"):
            return None
        if not snapshot.has_column("jargons", "created_timestamp"):
            return None
        if not snapshot.has_column("jargons", "updated_timestamp"):
            return None
        if snapshot.has_column("jargons", "inference_with_context"):
            return None
        if snapshot.has_column("jargons", "inference_with_content_only"):
            return None
        if snapshot.has_column("jargons", "created_by"):
            return None
        return V12_SCHEMA_VERSION


class V11SchemaVersionDetector(BaseSchemaVersionDetector):
    """v11 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v11_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v11 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "account_id"):
            return None
        if not snapshot.has_column("chat_sessions", "scope"):
            return None
        if not snapshot.has_column("chat_sessions", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "user_cardname"):
            return None
        if not snapshot.has_column("chat_sessions", "group_name"):
            return None
        if snapshot.has_column("expressions", "rejected"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        if not snapshot.has_table("statistics_message_hourly"):
            return None
        if not snapshot.has_table("statistics_tool_hourly"):
            return None
        if not snapshot.has_table("statistics_model_hourly"):
            return None
        if not snapshot.has_table("statistics_aggregation_cursors"):
            return None
        if not snapshot.has_column("jargons", "created_timestamp"):
            return None
        if not snapshot.has_column("jargons", "updated_timestamp"):
            return None
        has_jargon_inference_cache = snapshot.has_column(
            "jargons", "inference_with_context"
        ) or snapshot.has_column("jargons", "inference_with_content_only")
        if not has_jargon_inference_cache:
            return None
        return V11_SCHEMA_VERSION


class V10SchemaVersionDetector(BaseSchemaVersionDetector):
    """v10 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v10_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v10 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "account_id"):
            return None
        if not snapshot.has_column("chat_sessions", "scope"):
            return None
        if not snapshot.has_column("chat_sessions", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "user_cardname"):
            return None
        if not snapshot.has_column("chat_sessions", "group_name"):
            return None
        if snapshot.has_column("expressions", "rejected"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        if not snapshot.has_table("statistics_message_hourly"):
            return None
        if not snapshot.has_table("statistics_tool_hourly"):
            return None
        if not snapshot.has_table("statistics_model_hourly"):
            return None
        if not snapshot.has_table("statistics_aggregation_cursors"):
            return None
        has_jargon_created_timestamp = snapshot.has_column("jargons", "created_timestamp")
        has_jargon_updated_timestamp = snapshot.has_column("jargons", "updated_timestamp")
        if has_jargon_created_timestamp and has_jargon_updated_timestamp:
            return None
        return V10_SCHEMA_VERSION


class V9SchemaVersionDetector(BaseSchemaVersionDetector):
    """v9 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v9_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v9 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "account_id"):
            return None
        if not snapshot.has_column("chat_sessions", "scope"):
            return None
        if snapshot.has_column("chat_sessions", "user_nickname"):
            return None
        if snapshot.has_column("chat_sessions", "user_cardname"):
            return None
        if snapshot.has_column("chat_sessions", "group_name"):
            return None
        if snapshot.has_column("expressions", "rejected"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        if not snapshot.has_table("statistics_message_hourly"):
            return None
        if not snapshot.has_table("statistics_tool_hourly"):
            return None
        if not snapshot.has_table("statistics_model_hourly"):
            return None
        if not snapshot.has_table("statistics_aggregation_cursors"):
            return None
        return V9_SCHEMA_VERSION


class V6SchemaVersionDetector(BaseSchemaVersionDetector):
    """v6 schema 缁撴瀯鎺㈡祴鍣ㄣ€?"""

    @property
    def name(self) -> str:
        return "v6_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """妫€娴嬫暟鎹簱鏄惁涓?v6 缁撴瀯銆?"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("chat_sessions", "account_id"):
            return None
        if not snapshot.has_column("chat_sessions", "scope"):
            return None
        if not snapshot.has_column("expressions", "rejected"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        return V6_SCHEMA_VERSION


class V5SchemaVersionDetector(BaseSchemaVersionDetector):
    """v5 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v5_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v5 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if snapshot.has_column("mai_messages", "display_message"):
            return None
        if snapshot.has_column("chat_sessions", "account_id") or snapshot.has_column("chat_sessions", "scope"):
            return None
        return V5_SCHEMA_VERSION


class V3SchemaVersionDetector(BaseSchemaVersionDetector):
    """v3 schema 结构探测器。"""

    @property
    def name(self) -> str:
        return "v3_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v3 结构。"""

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if snapshot.has_table("action_records"):
            return None
        if snapshot.has_table("thinking_questions"):
            return None
        if snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("images", "image_hash"):
            return None
        if not snapshot.has_column("images", "full_path"):
            return None
        if not snapshot.has_column("images", "image_type"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        if not snapshot.has_column("mai_messages", "display_message"):
            return None
        return V3_SCHEMA_VERSION


class V2SchemaVersionDetector(BaseSchemaVersionDetector):
    """v2 schema 结构探测器。"""

    @property
    def name(self) -> str:
        """返回探测器名称。

        Returns:
            str: 当前探测器名称。
        """

        return "v2_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为 v2 结构。

        Args:
            snapshot: 当前数据库结构快照。

        Returns:
            Optional[int]: 若识别为 v2 结构则返回 ``2``，否则返回 ``None``。
        """

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return None
        if not all(snapshot.has_table(table_name) for table_name in _COMMON_MARKER_TABLES):
            return None
        if not snapshot.has_table("action_records"):
            return None
        if not snapshot.has_table("thinking_questions"):
            return None
        if not snapshot.has_column("images", "emotion"):
            return None
        if not snapshot.has_column("action_records", "session_id"):
            return None
        if not snapshot.has_column("chat_history", "session_id"):
            return None
        if not snapshot.has_column("person_info", "user_nickname"):
            return None
        return V2_SCHEMA_VERSION


class LegacyV1SchemaDetector(BaseSchemaVersionDetector):
    """旧版 ``0.x`` schema 结构探测器。"""

    @property
    def name(self) -> str:
        """返回探测器名称。

        Returns:
            str: 当前探测器名称。
        """

        return "legacy_v1_schema_detector"

    def detect_version(self, snapshot: DatabaseSchemaSnapshot) -> Optional[int]:
        """检测数据库是否为旧版 ``0.x`` 结构。

        Args:
            snapshot: 当前数据库结构快照。

        Returns:
            Optional[int]: 若识别为旧版结构则返回 ``1``，否则返回 ``None``。
        """

        if any(snapshot.has_table(table_name) for table_name in _LEGACY_V1_EXCLUSIVE_TABLES):
            return LEGACY_V1_SCHEMA_VERSION

        legacy_shared_markers = (
            ("action_records", ("chat_id", "time")),
            ("chat_history", ("chat_id", "original_text")),
            ("images", ("emoji_hash", "path", "type")),
            ("llm_usage", ("model_api_provider", "status")),
            ("online_time", ("duration",)),
            ("person_info", ("nickname", "group_nick_name")),
        )
        for table_name, required_columns in legacy_shared_markers:
            if snapshot.has_table(table_name) and all(
                snapshot.has_column(table_name, column_name) for column_name in required_columns
            ):
                return LEGACY_V1_SCHEMA_VERSION
        return None


def build_default_schema_version_detectors() -> List[BaseSchemaVersionDetector]:
    """构建默认 schema 版本探测器链。

    Returns:
        List[BaseSchemaVersionDetector]: 按优先级排序的探测器列表。
    """

    return [
        LatestSchemaVersionDetector(),
        V27SchemaVersionDetector(),
        V26SchemaVersionDetector(),
        V25SchemaVersionDetector(),
        V24SchemaVersionDetector(),
        V23SchemaVersionDetector(),
        V22SchemaVersionDetector(),
        V21SchemaVersionDetector(),
        V20SchemaVersionDetector(),
        V19SchemaVersionDetector(),
        V18SchemaVersionDetector(),
        V17SchemaVersionDetector(),
        V16SchemaVersionDetector(),
        V15SchemaVersionDetector(),
        V14SchemaVersionDetector(),
        V13SchemaVersionDetector(),
        V12SchemaVersionDetector(),
        V11SchemaVersionDetector(),
        V10SchemaVersionDetector(),
        V9SchemaVersionDetector(),
        V6SchemaVersionDetector(),
        V5SchemaVersionDetector(),
        V3SchemaVersionDetector(),
        V2SchemaVersionDetector(),
        LegacyV1SchemaDetector(),
    ]


def build_default_schema_version_resolver() -> SchemaVersionResolver:
    """构建默认 schema 版本解析器。

    Returns:
        SchemaVersionResolver: 配置完成的 schema 版本解析器。
    """

    return SchemaVersionResolver(
        version_store=SQLiteUserVersionStore(),
        schema_inspector=SQLiteSchemaInspector(),
        detectors=build_default_schema_version_detectors(),
    )


def build_default_migration_registry() -> MigrationRegistry:
    """构建默认迁移步骤注册表。

    Returns:
        MigrationRegistry: 含默认迁移步骤的注册表实例。
    """

    return MigrationRegistry(
        steps=[
            MigrationStep(
                version_from=LEGACY_V1_SCHEMA_VERSION,
                version_to=V2_SCHEMA_VERSION,
                name="legacy_v1_to_v2",
                description="将旧版 0.x 数据库迁移到 v2 schema。",
                handler=migrate_legacy_v1_to_v2,
            ),
            MigrationStep(
                version_from=V2_SCHEMA_VERSION,
                version_to=V3_SCHEMA_VERSION,
                name="v2_to_v3",
                description="移除废弃表，并将 emoji 标签统一收敛到 description 字段。",
                handler=migrate_v2_to_v3,
            ),
            MigrationStep(
                version_from=V3_SCHEMA_VERSION,
                version_to=V4_SCHEMA_VERSION,
                name="v3_to_v4",
                description="移除 mai_messages.display_message 弃用列。",
                handler=migrate_v3_to_v4,
            ),
            MigrationStep(
                version_from=V4_SCHEMA_VERSION,
                version_to=V5_SCHEMA_VERSION,
                name="v4_to_v5",
                description="清空群聊 chat_sessions.user_id，避免把首个发言人误认为聊天流归属。",
                handler=migrate_v4_to_v5,
            ),
            MigrationStep(
                version_from=V5_SCHEMA_VERSION,
                version_to=V6_SCHEMA_VERSION,
                name="v5_to_v6",
                description="为 chat_sessions 增加 account_id/scope 路由归属字段。",
                handler=migrate_v5_to_v6,
            ),
            MigrationStep(
                version_from=V6_SCHEMA_VERSION,
                version_to=V7_SCHEMA_VERSION,
                name="v6_to_v7",
                description="移除 expressions.rejected 列，并删除已审核拒绝的表达方式。",
                handler=migrate_v6_to_v7,
            ),
            MigrationStep(
                version_from=V7_SCHEMA_VERSION,
                version_to=V8_SCHEMA_VERSION,
                name="v7_to_v8",
                description="将 AI 标记的已审核表达方式改回待人工审核。",
                handler=migrate_v7_to_v8,
            ),
            MigrationStep(
                version_from=V8_SCHEMA_VERSION,
                version_to=V9_SCHEMA_VERSION,
                name="v8_to_v9",
                description="新增统计汇总表、索引与历史统计回填。",
                handler=migrate_v8_to_v9,
            ),
            MigrationStep(
                version_from=V9_SCHEMA_VERSION,
                version_to=V10_SCHEMA_VERSION,
                name="v9_to_v10",
                description="为 chat_sessions 增加群名与私聊用户展示名字段。",
                handler=migrate_v9_to_v10,
            ),
            MigrationStep(
                version_from=V10_SCHEMA_VERSION,
                version_to=V11_SCHEMA_VERSION,
                name="v10_to_v11",
                description="为 jargons 增加创建时间和更新时间字段。",
                handler=migrate_v10_to_v11,
            ),
            MigrationStep(
                version_from=V11_SCHEMA_VERSION,
                version_to=V12_SCHEMA_VERSION,
                name="v11_to_v12",
                description="移除 jargons 中不再持久化的推理过程缓存字段。",
                handler=migrate_v11_to_v12,
            ),
            MigrationStep(
                version_from=V12_SCHEMA_VERSION,
                version_to=V13_SCHEMA_VERSION,
                name="v12_to_v13",
                description="为 jargons 增加创建来源字段。",
                handler=migrate_v12_to_v13,
            ),
            MigrationStep(
                version_from=V13_SCHEMA_VERSION,
                version_to=V14_SCHEMA_VERSION,
                name="v13_to_v14",
                description="为遥测聚合增加消息回复频率与模型任务名称字段。",
                handler=migrate_v13_to_v14,
            ),
            MigrationStep(
                version_from=V14_SCHEMA_VERSION,
                version_to=V15_SCHEMA_VERSION,
                name="v14_to_v15",
                description="为 LLM 使用记录增加 prompt cache token 统计字段。",
                handler=migrate_v14_to_v15,
            ),
            MigrationStep(
                version_from=V15_SCHEMA_VERSION,
                version_to=V16_SCHEMA_VERSION,
                name="v15_to_v16",
                description="为 LLM 使用记录增加当次请求是否启用 prompt cache 计费字段。",
                handler=migrate_v15_to_v16,
            ),
            MigrationStep(
                version_from=V16_SCHEMA_VERSION,
                version_to=V17_SCHEMA_VERSION,
                name="v16_to_v17",
                description="新增行为表现模式表。",
                handler=migrate_v16_to_v17,
            ),
            MigrationStep(
                version_from=V17_SCHEMA_VERSION,
                version_to=V18_SCHEMA_VERSION,
                name="v17_to_v18",
                description="新增高频词/词组词库表。",
                handler=migrate_v17_to_v18,
            ),
            MigrationStep(
                version_from=V18_SCHEMA_VERSION,
                version_to=V19_SCHEMA_VERSION,
                name="v18_to_v19",
                description="移除旧行为表现主表，创建节点化行为经验路径图谱。",
                handler=migrate_v18_to_v19,
            ),
            MigrationStep(
                version_from=V19_SCHEMA_VERSION,
                version_to=V20_SCHEMA_VERSION,
                name="v19_to_v20",
                description="删除测试期行为数据，创建独立场景簇概率分布结构。",
                handler=migrate_v19_to_v20,
            ),
            MigrationStep(
                version_from=V20_SCHEMA_VERSION,
                version_to=V21_SCHEMA_VERSION,
                name="v20_to_v21",
                description="为行为经验路径增加行为主体与学习类型字段。",
                handler=migrate_v20_to_v21,
            ),
            MigrationStep(
                version_from=V21_SCHEMA_VERSION,
                version_to=V22_SCHEMA_VERSION,
                name="v21_to_v22",
                description="合并行为场景索引重建、旧行为学习数据清理和 legacy v1 遗留表清理。",
                handler=migrate_v21_to_v22,
                transactional=False,
            ),
            MigrationStep(
                version_from=V22_SCHEMA_VERSION,
                version_to=V23_SCHEMA_VERSION,
                name="v22_to_v23",
                description="移除行为场景簇冗余身份字段。",
                handler=migrate_v22_to_v23,
            ),
            MigrationStep(
                version_from=V23_SCHEMA_VERSION,
                version_to=V24_SCHEMA_VERSION,
                name="v23_to_v24",
                description="将行为动作/结果收敛为文本实体，并移除冗余 action/outcome 图边。",
                handler=migrate_v23_to_v24,
            ),
            MigrationStep(
                version_from=V24_SCHEMA_VERSION,
                version_to=V25_SCHEMA_VERSION,
                name="v24_to_v25",
                description="将行为场景簇规整为仅由 domain tag 定义。",
                handler=migrate_v24_to_v25,
            ),
            MigrationStep(
                version_from=V25_SCHEMA_VERSION,
                version_to=V26_SCHEMA_VERSION,
                name="v25_to_v26",
                description="移除行为学习中不再显式存储的 scene node 图层。",
                handler=migrate_v25_to_v26,
            ),
            MigrationStep(
                version_from=V26_SCHEMA_VERSION,
                version_to=V27_SCHEMA_VERSION,
                name="v26_to_v27",
                description="移除行为场景簇不再使用的 score 字段。",
                handler=migrate_v26_to_v27,
            ),
        ]
    )
