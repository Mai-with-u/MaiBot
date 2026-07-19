from src.A_memorix.core.retrieval.dual_path import DualPathRetriever, DualPathRetrieverConfig


class _FakeConfig:
    ppr_alpha = 0.85


class _FakePpr:
    class _Config:
        tol = 1e-6
        max_iter = 50
        min_iterations = 5

    config = _Config()


class _FakeGraphStore:
    num_nodes = 8
    num_edges = 7

    def __init__(self) -> None:
        self._nodes = {"艾宝", "检索", "记忆", "速度", "无关"}
        self._out = {
            "艾宝": ["检索", "记忆"],
            "检索": ["速度"],
            "记忆": ["速度"],
            "速度": [],
            "无关": [],
        }
        self._in = {
            "艾宝": [],
            "检索": ["艾宝"],
            "记忆": ["艾宝"],
            "速度": ["检索", "记忆"],
            "无关": [],
        }
        self.full_pagerank_called = False

    def find_node(self, node: str, ignore_case: bool = False):
        del ignore_case
        for candidate in self._nodes:
            if candidate.lower() == node.lower():
                return candidate
        return None

    def get_neighbors(self, node: str):
        return list(self._out.get(node, []))

    def get_weighted_neighbors(self, node: str):
        return [(neighbor, 1.0) for neighbor in self._out.get(node, [])]

    def get_in_neighbors(self, node: str):
        return list(self._in.get(node, []))

    def compute_pagerank(self, **kwargs):
        del kwargs
        self.full_pagerank_called = True
        return {"艾宝": 1.0}


def test_local_ppr_scores_stay_inside_seed_neighborhood() -> None:
    graph_store = _FakeGraphStore()
    config = DualPathRetrieverConfig(
        ppr_local_enabled=True,
        ppr_local_min_graph_nodes=0,
        ppr_local_max_nodes=4,
        ppr_local_hops=2,
    )
    retriever = object.__new__(DualPathRetriever)
    retriever.graph_store = graph_store
    retriever.config = config
    retriever._ppr = _FakePpr()

    scores = retriever._compute_ppr_scores({"艾宝": 1.0})

    assert scores
    assert "无关" not in scores
    assert graph_store.full_pagerank_called is False
    assert abs(sum(scores.values()) - 1.0) < 1e-6
