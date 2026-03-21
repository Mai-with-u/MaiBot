"""NapCat 出站消息编解码。"""

from typing import Any, Dict, List, Mapping, Tuple


class NapCatOutboundCodec:
    """NapCat 出站消息编码器。"""

    def build_outbound_action(
        self,
        message: Mapping[str, Any],
        route: Mapping[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """为 Host 出站消息构造 OneBot 动作。

        Args:
            message: Host 侧标准 ``MessageDict``。
            route: Platform IO 路由信息。

        Returns:
            Tuple[str, Dict[str, Any]]: 动作名称与参数字典。

        Raises:
            ValueError: 当私聊出站缺少目标用户 ID 时抛出。
        """
        message_info = message.get("message_info", {})
        if not isinstance(message_info, Mapping):
            message_info = {}

        group_info = message_info.get("group_info", {})
        if not isinstance(group_info, Mapping):
            group_info = {}

        additional_config = message_info.get("additional_config", {})
        if not isinstance(additional_config, Mapping):
            additional_config = {}

        raw_message = message.get("raw_message", [])
        segments = self.convert_segments(raw_message)

        if target_group_id := str(
            group_info.get("group_id") or additional_config.get("platform_io_target_group_id") or ""
        ).strip():
            return "send_group_msg", {"group_id": target_group_id, "message": segments}

        target_user_id = str(
            additional_config.get("platform_io_target_user_id")
            or additional_config.get("target_user_id")
            or route.get("target_user_id")
            or ""
        ).strip()
        if not target_user_id:
            raise ValueError("Outbound private message is missing target_user_id")

        return "send_private_msg", {"message": segments, "user_id": target_user_id}

    def convert_segments(self, raw_message: Any) -> List[Dict[str, Any]]:
        """将 Host 消息段转换为 OneBot 消息段。

        Args:
            raw_message: Host 侧 ``raw_message`` 字段。

        Returns:
            List[Dict[str, Any]]: OneBot 消息段列表。
        """
        if not isinstance(raw_message, list):
            return [{"type": "text", "data": {"text": ""}}]

        outbound_segments: List[Dict[str, Any]] = []
        for item in raw_message:
            if not isinstance(item, Mapping):
                continue

            item_type = str(item.get("type") or "").strip()
            item_data = item.get("data")

            if item_type == "text":
                text_value = str(item_data or "")
                outbound_segments.append({"type": "text", "data": {"text": text_value}})
                continue

            if item_type == "at" and isinstance(item_data, Mapping):
                if target_user_id := str(item_data.get("target_user_id") or "").strip():
                    outbound_segments.append({"type": "at", "data": {"qq": target_user_id}})
                continue

            if item_type == "reply":
                if isinstance(item_data, Mapping):
                    target_message_id = str(item_data.get("target_message_id") or "").strip()
                else:
                    target_message_id = str(item_data or "").strip()
                if target_message_id:
                    outbound_segments.append({"type": "reply", "data": {"id": target_message_id}})
                continue

            if item_type == "image":
                binary_base64 = str(item.get("binary_data_base64") or "").strip()
                if binary_base64:
                    outbound_segments.append(
                        {
                            "type": "image",
                            "data": {"file": f"base64://{binary_base64}", "subtype": 0},
                        }
                    )
                else:
                    outbound_segments.append({"type": "text", "data": {"text": "[image]"}})
                continue

            if item_type == "emoji":
                binary_base64 = str(item.get("binary_data_base64") or "").strip()
                if binary_base64:
                    outbound_segments.append(
                        {
                            "type": "image",
                            "data": {
                                "file": f"base64://{binary_base64}",
                                "subtype": 1,
                                "summary": "[动画表情]",
                            },
                        }
                    )
                else:
                    outbound_segments.append({"type": "text", "data": {"text": "[emoji]"}})
                continue

            if item_type == "voice":
                binary_base64 = str(item.get("binary_data_base64") or "").strip()
                if binary_base64:
                    outbound_segments.append({"type": "record", "data": {"file": f"base64://{binary_base64}"}})
                else:
                    outbound_segments.append({"type": "text", "data": {"text": "[voice]"}})
                continue

            if item_type == "forward" and isinstance(item_data, list):
                outbound_segments.extend(self._build_forward_nodes(item_data))
                continue

            if item_type == "dict" and isinstance(item_data, Mapping):
                if dict_segment := self._build_dict_component_segment(item_data):
                    outbound_segments.append(dict_segment)
                    continue

            fallback_text = f"[unsupported:{item_type or 'unknown'}]"
            outbound_segments.append({"type": "text", "data": {"text": fallback_text}})

        if not outbound_segments:
            outbound_segments.append({"type": "text", "data": {"text": ""}})
        return outbound_segments

    def _build_forward_nodes(self, forward_nodes: List[Any]) -> List[Dict[str, Any]]:
        """构造 NapCat 转发节点列表。

        Args:
            forward_nodes: 内部转发节点列表。

        Returns:
            List[Dict[str, Any]]: NapCat 转发节点列表。
        """
        built_nodes: List[Dict[str, Any]] = []
        for node in forward_nodes:
            if not isinstance(node, Mapping):
                continue
            raw_content = node.get("content", [])
            node_segments = self.convert_segments(raw_content)
            built_nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": str(node.get("user_nickname") or node.get("user_cardname") or "QQ用户"),
                        "uin": str(node.get("user_id") or ""),
                        "content": node_segments,
                    },
                }
            )
        return built_nodes

    def _build_dict_component_segment(self, item_data: Mapping[str, Any]) -> Dict[str, Any]:
        """尽力将 ``DictComponent`` 转换为 NapCat 消息段。

        Args:
            item_data: ``DictComponent`` 原始数据。

        Returns:
            Dict[str, Any]: NapCat 消息段；不支持时返回占位文本段。
        """
        raw_type = str(item_data.get("type") or "").strip()
        raw_payload = item_data.get("data", item_data)
        if raw_type in {"file", "music", "video", "face"} and isinstance(raw_payload, Mapping):
            return {"type": raw_type, "data": dict(raw_payload)}
        if raw_type in {"image", "record", "reply", "at"} and isinstance(raw_payload, Mapping):
            return {"type": raw_type, "data": dict(raw_payload)}
        return {"type": "text", "data": {"text": f"[unsupported:{raw_type or 'dict'}]"}}
