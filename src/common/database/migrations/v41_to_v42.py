"""v41 到 v42：移除行为学习数据表。"""

from .models import MigrationExecutionContext

BEHAVIOR_TABLES = (
    "behavior_experience_paths_v21",
    "behavior_scene_nodes_v22",
    "behavior_scene_tag_clusters_v22",
    "behavior_scene_clusters_v23",
    "behavior_actions_v24",
    "behavior_experience_paths_v24",
    "behavior_outcomes_v24",
    "behavior_scene_clusters_v27",
    "behavior_offline_import_records",
    "behavior_pattern_scene_links",
    "behavior_experience_scene_links",
    "behavior_scene_node_tags",
    "behavior_scene_action_edges",
    "behavior_action_outcome_edges",
    "behavior_scene_edges",
    "behavior_patterns",
    "behavior_experience_paths",
    "behavior_scene_clusters",
    "behavior_scene_tag_clusters",
    "behavior_scene_nodes",
    "behavior_action_nodes",
    "behavior_outcome_nodes",
    "behavior_actions",
    "behavior_outcomes",
)


def migrate_v41_to_v42(context: MigrationExecutionContext) -> None:
    """删除当前及历史行为学习表，保留其他学习和聊天数据。"""
    context.start_progress(
        total_tables=len(BEHAVIOR_TABLES),
        total_records=0,
        description="v41 -> v42 移除行为学习",
        table_unit_name="表",
        record_unit_name="记录",
    )
    for table_name in BEHAVIOR_TABLES:
        context.connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
        context.advance_progress(records=0, completed_tables=1, item_name=table_name)
