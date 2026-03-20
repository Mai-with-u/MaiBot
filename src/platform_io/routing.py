"""提供 Platform IO 的路由绑定存储与归属解析能力。"""

from typing import Dict, List, Optional

from .types import RouteBinding, RouteKey, RouteMode


class RouteBindingConflictError(ValueError):
    """当同一路由键出现多个 active owner 竞争时抛出。"""


class RouteTable:
    """维护路由绑定并解析路由归属。

    这个表刻意保持轻量，只负责归属规则本身，不掺杂具体发送或接收逻辑。
    它决定某个路由键当前由哪个驱动 active 接管，哪些驱动仅以 shadow
    方式旁路观测。
    """

    def __init__(self) -> None:
        """初始化一个空的路由绑定表。"""
        self._bindings: Dict[RouteKey, Dict[str, RouteBinding]] = {}

    def bind(self, binding: RouteBinding, *, replace: bool = False) -> None:
        """注册或更新一条路由绑定。

        Args:
            binding: 要注册的绑定对象。
            replace: 当精确路由键上已经存在 active owner 时，是否允许替换。

        Raises:
            RouteBindingConflictError: 当精确路由键上已存在其他 active owner，
                且 ``replace`` 为 ``False`` 时抛出。
        """

        if binding.mode == RouteMode.DISABLED:
            self.unbind(binding.route_key, binding.driver_id)
            return

        if binding.mode == RouteMode.ACTIVE:
            active_binding = self.get_active_binding(binding.route_key, exact_only=True)
            if active_binding and active_binding.driver_id != binding.driver_id:
                if not replace:
                    raise RouteBindingConflictError(
                        f"RouteKey {binding.route_key} 已由 {active_binding.driver_id} 接管，"
                        f"拒绝绑定到 {binding.driver_id}"
                    )
                self.unbind(binding.route_key, active_binding.driver_id)

        self._bindings.setdefault(binding.route_key, {})[binding.driver_id] = binding

    def unbind(self, route_key: RouteKey, driver_id: Optional[str] = None) -> List[RouteBinding]:
        """移除指定路由键上的绑定。

        Args:
            route_key: 要移除绑定的路由键。
            driver_id: 可选的特定驱动 ID；若为空，则移除该路由键上的全部绑定。

        Returns:
            List[RouteBinding]: 被移除的绑定列表。
        """

        binding_map = self._bindings.get(route_key)
        if not binding_map:
            return []

        if driver_id is None:
            removed = list(binding_map.values())
            self._bindings.pop(route_key, None)
            return removed

        removed_binding = binding_map.pop(driver_id, None)
        if not binding_map:
            self._bindings.pop(route_key, None)
        return [removed_binding] if removed_binding else []

    def remove_bindings_by_driver(self, driver_id: str) -> List[RouteBinding]:
        """移除某个驱动在所有路由键上的绑定。

        Args:
            driver_id: 要移除绑定的驱动 ID。

        Returns:
            List[RouteBinding]: 被移除的绑定列表。
        """
        removed_bindings: List[RouteBinding] = []
        empty_route_keys: List[RouteKey] = []

        for route_key, binding_map in self._bindings.items():
            removed_binding = binding_map.pop(driver_id, None)
            if removed_binding is not None:
                removed_bindings.append(removed_binding)
            if not binding_map:
                empty_route_keys.append(route_key)

        for route_key in empty_route_keys:
            self._bindings.pop(route_key, None)

        return self._sort_bindings(removed_bindings)

    def list_bindings(self, route_key: Optional[RouteKey] = None) -> List[RouteBinding]:
        """列出当前绑定。

        Args:
            route_key: 可选的路由键过滤条件；若为空，则返回全部路由键上的绑定。

        Returns:
            List[RouteBinding]: 按优先级降序排列的绑定列表。
        """

        if route_key is None:
            bindings: List[RouteBinding] = []
            for binding_map in self._bindings.values():
                bindings.extend(binding_map.values())
            return self._sort_bindings(bindings)

        binding_map = self._bindings.get(route_key, {})
        return self._sort_bindings(list(binding_map.values()))

    def get_active_binding(self, route_key: RouteKey, *, exact_only: bool = False) -> Optional[RouteBinding]:
        """获取某个路由键当前生效的 active 绑定。

        Args:
            route_key: 要解析的路由键。
            exact_only: 是否只检查精确路由键而不做回退解析。

        Returns:
            Optional[RouteBinding]: 若存在 active owner，则返回对应绑定。
        """

        candidate_keys = [route_key] if exact_only else route_key.resolution_order()
        for candidate_key in candidate_keys:
            binding_map = self._bindings.get(candidate_key, {})
            active_binding = self._pick_best_binding(binding_map, RouteMode.ACTIVE)
            if active_binding is not None:
                return active_binding
        return None

    def get_shadow_bindings(self, route_key: RouteKey) -> List[RouteBinding]:
        """获取某个精确路由键上的 shadow 绑定。

        Args:
            route_key: 要查看的路由键。

        Returns:
            List[RouteBinding]: 按优先级降序排列的 shadow 绑定列表。
        """
        binding_map = self._bindings.get(route_key, {})
        shadow_bindings = [binding for binding in binding_map.values() if binding.mode == RouteMode.SHADOW]
        return self._sort_bindings(shadow_bindings)

    def accepts_inbound(self, route_key: RouteKey, driver_id: str) -> bool:
        """判断某个驱动是否是当前允许入 Core 的 active owner。

        Args:
            route_key: 入站消息对应的路由键。
            driver_id: 希望将消息送入 Core 的驱动 ID。

        Returns:
            bool: 若该驱动是解析结果中的 active owner，则返回 ``True``。
        """

        active_binding = self.get_active_binding(route_key)
        return active_binding is not None and active_binding.driver_id == driver_id

    @staticmethod
    def _sort_bindings(bindings: List[RouteBinding]) -> List[RouteBinding]:
        """按优先级降序排列绑定列表。

        Args:
            bindings: 待排序的绑定列表。

        Returns:
            List[RouteBinding]: 排序后的绑定列表。
        """
        return sorted(bindings, key=lambda item: item.priority, reverse=True)

    @staticmethod
    def _pick_best_binding(
        binding_map: Dict[str, RouteBinding],
        mode: RouteMode,
    ) -> Optional[RouteBinding]:
        """从绑定映射中挑选指定模式下优先级最高的一条绑定。

        Args:
            binding_map: 某个精确 ``RouteKey`` 对应的绑定映射。
            mode: 需要挑选的绑定模式。

        Returns:
            Optional[RouteBinding]: 若存在匹配模式的绑定，则返回优先级最高的一条。

        Notes:
            这里使用单次线性扫描代替“先过滤成列表再排序”的做法，以减少
            高频路由解析路径上的临时对象分配和排序开销。
        """
        best_binding: Optional[RouteBinding] = None
        for binding in binding_map.values():
            if binding.mode != mode:
                continue
            if best_binding is None or binding.priority > best_binding.priority:
                best_binding = binding
        return best_binding
