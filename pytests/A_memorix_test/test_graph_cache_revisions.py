from types import SimpleNamespace

from src.A_memorix.core.retrieval.dual_path import DualPathRetriever
from src.A_memorix.core.retrieval.pagerank import PersonalizedPageRank
from src.A_memorix.core.storage.graph_store import GraphStore


def _build_retriever(graph_store: GraphStore) -> DualPathRetriever:
    retriever = DualPathRetriever.__new__(DualPathRetriever)
    retriever.graph_store = graph_store
    retriever.config = SimpleNamespace(
        ppr_alpha=0.85,
        ppr_local_enabled=True,
        ppr_local_max_nodes=256,
        ppr_local_hops=2,
        ppr_local_min_graph_nodes=0,
    )
    retriever._ac_matcher = None
    retriever._ac_nodes_count = 0
    retriever._ac_node_revision = -1
    retriever._ac_node_map = {}
    return retriever


def test_graph_revisions_coalesce_batch_updates() -> None:
    graph_store = GraphStore()

    with graph_store.batch_update():
        graph_store.add_nodes(["甲", "乙", "丙"])
        graph_store.add_edges([("甲", "乙")])
        graph_store.add_edges([("乙", "丙")])

    assert graph_store.graph_revision == 1
    assert graph_store.node_revision == 1

    graph_store.add_nodes(["甲"])

    assert graph_store.graph_revision == 1
    assert graph_store.node_revision == 1


def test_ppr_cache_key_changes_when_same_size_graph_content_changes() -> None:
    graph_store = GraphStore()
    graph_store.add_nodes(["甲", "乙", "丙"])
    graph_store.add_edges([("甲", "乙")])
    retriever = _build_retriever(graph_store)

    edge_key_before = retriever._build_ppr_cache_key({"甲": 1.0})
    graph_store.delete_edges([("甲", "乙")])
    graph_store.add_edges([("甲", "丙")])
    edge_key_after = retriever._build_ppr_cache_key({"甲": 1.0})

    assert graph_store.num_nodes == 3
    assert graph_store.num_edges == 1
    assert edge_key_after != edge_key_before

    weight_key_before = edge_key_after
    graph_store.update_edge_weight("甲", "丙", 0.5)
    weight_key_after = retriever._build_ppr_cache_key({"甲": 1.0})

    assert graph_store.num_nodes == 3
    assert graph_store.num_edges == 1
    assert weight_key_after != weight_key_before


def test_relation_hash_only_change_does_not_invalidate_graph_revision() -> None:
    graph_store = GraphStore()
    graph_store.add_edges([("甲", "乙")], weights=[1.0], relation_hashes=["relation-1"])
    revision_before = graph_store.graph_revision

    with graph_store.batch_update():
        graph_store.add_edges([("甲", "乙")], weights=[1.0], relation_hashes=["relation-2"])

    assert graph_store.graph_revision == revision_before
    assert graph_store.get_relation_hashes_for_edge("甲", "乙") == {"relation-1", "relation-2"}


def test_entity_matchers_rebuild_after_same_count_node_replacement() -> None:
    graph_store = GraphStore()
    graph_store.add_nodes(["旧节点"])
    retriever = _build_retriever(graph_store)
    pagerank = PersonalizedPageRank(graph_store)

    assert retriever._extract_entities("旧节点") == {"旧节点": 1.0}
    assert pagerank._extract_entities_from_query("旧节点") == ["旧节点"]

    graph_store.delete_nodes(["旧节点"])
    graph_store.add_nodes(["新节点"])

    assert graph_store.num_nodes == 1
    assert retriever._extract_entities("旧节点") == {}
    assert retriever._extract_entities("新节点") == {"新节点": 1.0}
    assert pagerank._extract_entities_from_query("旧节点") == []
    assert pagerank._extract_entities_from_query("新节点") == ["新节点"]
