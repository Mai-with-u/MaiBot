"""知识库图谱可视化 API 路由"""

from typing import List, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webui/knowledge", tags=["knowledge"])


class KnowledgeNode(BaseModel):
    """知识节点"""

    id: str
    type: str  # 'entity' or 'paragraph'
    content: str
    create_time: Optional[float] = None


class KnowledgeEdge(BaseModel):
    """知识边"""

    source: str
    target: str
    weight: float
    create_time: Optional[float] = None
    update_time: Optional[float] = None


class KnowledgeGraph(BaseModel):
    """知识图谱"""

    nodes: List[KnowledgeNode]
    edges: List[KnowledgeEdge]


class KnowledgeStats(BaseModel):
    """知识库统计信息"""

    total_nodes: int
    total_edges: int
    entity_nodes: int
    paragraph_nodes: int
    avg_connections: float


def _load_kg_manager():
    """延迟加载 KGManager"""
    try:
        from src.chat.knowledge.kg_manager import KGManager

        kg_manager = KGManager()
        kg_manager.load_from_file()
        return kg_manager
    except Exception as e:
        logger.error(f"加载 KGManager 失败: {e}")
        return None


def _convert_graph_to_json(kg_manager) -> KnowledgeGraph:
    """将 DiGraph 转换为 JSON 格式"""
    if kg_manager is None or kg_manager.graph is None:
        return KnowledgeGraph(nodes=[], edges=[])

    graph = kg_manager.graph
    nodes = []
    edges = []

    # 转换节点
    node_list = graph.get_node_list()
    for node_id in node_list:
        try:
            node_data = graph[node_id]
            # 节点类型: "ent" -> "entity", "pg" -> "paragraph"
            node_type = "entity" if ("type" in node_data and node_data["type"] == "ent") else "paragraph"
            content = node_data["content"] if "content" in node_data else node_id
            create_time = node_data["create_time"] if "create_time" in node_data else None

            nodes.append(KnowledgeNode(id=node_id, type=node_type, content=content, create_time=create_time))
        except Exception as e:
            logger.warning(f"跳过节点 {node_id}: {e}")
            continue

    # 转换边
    edge_list = graph.get_edge_list()
    for edge_tuple in edge_list:
        try:
            # edge_tuple 是 (source, target) 元组
            source, target = edge_tuple[0], edge_tuple[1]
            # 通过 graph[source, target] 获取边的属性数据
            edge_data = graph[source, target]

            # edge_data 支持 [] 操作符但不支持 .get()
            weight = edge_data["weight"] if "weight" in edge_data else 1.0
            create_time = edge_data["create_time"] if "create_time" in edge_data else None
            update_time = edge_data["update_time"] if "update_time" in edge_data else None

            edges.append(
                KnowledgeEdge(
                    source=source, target=target, weight=weight, create_time=create_time, update_time=update_time
                )
            )
        except Exception as e:
            logger.warning(f"跳过边 {edge_tuple}: {e}")
            continue

    return KnowledgeGraph(nodes=nodes, edges=edges)


@router.get("/graph", response_model=KnowledgeGraph)
async def get_knowledge_graph(
    limit: int = Query(100, ge=1, le=10000, description="返回的最大节点数"),
    node_type: str = Query("all", description="节点类型过滤: all, entity, paragraph"),
):
    """获取知识图谱(限制节点数量)

    Args:
        limit: 返回的最大节点数,默认 100,最大 10000
        node_type: 节点类型过滤 - all(全部), entity(实体), paragraph(段落)

    Returns:
        KnowledgeGraph: 包含指定数量节点和相关边的知识图谱
    """
    try:
        kg_manager = _load_kg_manager()
        if kg_manager is None:
            logger.warning("KGManager 未初始化，返回空图谱")
            return KnowledgeGraph(nodes=[], edges=[])

        graph = kg_manager.graph
        all_node_list = graph.get_node_list()

        # 按类型过滤节点
        if node_type == "entity":
            all_node_list = [
                n for n in all_node_list if n in graph and "type" in graph[n] and graph[n]["type"] == "ent"
            ]
        elif node_type == "paragraph":
            all_node_list = [n for n in all_node_list if n in graph and "type" in graph[n] and graph[n]["type"] == "pg"]

        # 限制节点数量
        total_nodes = len(all_node_list)
        if len(all_node_list) > limit:
            node_list = all_node_list[:limit]
        else:
            node_list = all_node_list

        logger.info(f"总节点数: {total_nodes}, 返回节点: {len(node_list)} (limit={limit}, type={node_type})")

        # 转换节点
        nodes = []
        node_ids = set()
        for node_id in node_list:
            try:
                node_data = graph[node_id]
                node_type_val = "entity" if ("type" in node_data and node_data["type"] == "ent") else "paragraph"
                content = node_data["content"] if "content" in node_data else node_id
                create_time = node_data["create_time"] if "create_time" in node_data else None

                nodes.append(KnowledgeNode(id=node_id, type=node_type_val, content=content, create_time=create_time))
                node_ids.add(node_id)
            except Exception as e:
                logger.warning(f"跳过节点 {node_id}: {e}")
                continue

        # 只获取涉及当前节点集的边(保证图的完整性)
        edges = []
        edge_list = graph.get_edge_list()
        for edge_tuple in edge_list:
            try:
                source, target = edge_tuple[0], edge_tuple[1]
                # 只包含两端都在当前节点集中的边
                if source not in node_ids or target not in node_ids:
                    continue

                edge_data = graph[source, target]
                weight = edge_data["weight"] if "weight" in edge_data else 1.0
                create_time = edge_data["create_time"] if "create_time" in edge_data else None
                update_time = edge_data["update_time"] if "update_time" in edge_data else None

                edges.append(
                    KnowledgeEdge(
                        source=source, target=target, weight=weight, create_time=create_time, update_time=update_time
                    )
                )
            except Exception as e:
                logger.warning(f"跳过边 {edge_tuple}: {e}")
                continue

        graph_data = KnowledgeGraph(nodes=nodes, edges=edges)
        logger.info(f"返回知识图谱: {len(nodes)} 个节点, {len(edges)} 条边")
        return graph_data

    except Exception as e:
        logger.error(f"获取知识图谱失败: {e}", exc_info=True)
        return KnowledgeGraph(nodes=[], edges=[])


@router.get("/stats", response_model=KnowledgeStats)
async def get_knowledge_stats():
    """获取知识库统计信息

    Returns:
        KnowledgeStats: 统计信息
    """
    try:
        kg_manager = _load_kg_manager()
        if kg_manager is None or kg_manager.graph is None:
            return KnowledgeStats(total_nodes=0, total_edges=0, entity_nodes=0, paragraph_nodes=0, avg_connections=0.0)

        graph = kg_manager.graph
        node_list = graph.get_node_list()
        edge_list = graph.get_edge_list()

        total_nodes = len(node_list)
        total_edges = len(edge_list)

        # 统计节点类型
        entity_nodes = 0
        paragraph_nodes = 0
        for node_id in node_list:
            try:
                node_data = graph[node_id]
                node_type = node_data["type"] if "type" in node_data else "ent"
                if node_type == "ent":
                    entity_nodes += 1
                elif node_type == "pg":
                    paragraph_nodes += 1
            except Exception:
                continue

        # 计算平均连接数
        avg_connections = (total_edges * 2) / total_nodes if total_nodes > 0 else 0.0

        return KnowledgeStats(
            total_nodes=total_nodes,
            total_edges=total_edges,
            entity_nodes=entity_nodes,
            paragraph_nodes=paragraph_nodes,
            avg_connections=round(avg_connections, 2),
        )

    except Exception as e:
        logger.error(f"获取统计信息失败: {e}", exc_info=True)
        return KnowledgeStats(total_nodes=0, total_edges=0, entity_nodes=0, paragraph_nodes=0, avg_connections=0.0)


@router.get("/search", response_model=List[KnowledgeNode])
async def search_knowledge_node(query: str = Query(..., min_length=1)):
    """搜索知识节点

    Args:
        query: 搜索关键词

    Returns:
        List[KnowledgeNode]: 匹配的节点列表
    """
    try:
        kg_manager = _load_kg_manager()
        if kg_manager is None or kg_manager.graph is None:
            return []

        graph = kg_manager.graph
        node_list = graph.get_node_list()
        results = []
        query_lower = query.lower()

        # 在节点内容中搜索
        for node_id in node_list:
            try:
                node_data = graph[node_id]
                content = node_data["content"] if "content" in node_data else node_id
                node_type = "entity" if ("type" in node_data and node_data["type"] == "ent") else "paragraph"

                if query_lower in content.lower() or query_lower in node_id.lower():
                    create_time = node_data["create_time"] if "create_time" in node_data else None
                    results.append(KnowledgeNode(id=node_id, type=node_type, content=content, create_time=create_time))
            except Exception:
                continue

        logger.info(f"搜索 '{query}' 找到 {len(results)} 个节点")
        return results[:50]  # 限制返回数量

    except Exception as e:
        logger.error(f"搜索节点失败: {e}", exc_info=True)
        return []
