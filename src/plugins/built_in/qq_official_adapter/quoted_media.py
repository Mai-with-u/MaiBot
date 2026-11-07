"""Read images supplied in QQ quote previews, without guessing message IDs."""
from typing import Any, List, Mapping, Set


MAX_QUOTE_DEPTH = 4
MAX_QUOTE_ITEMS = 64


def quoted_image_attachments(payload: Mapping[str, Any], limit: int) -> List[Mapping[str, Any]]:
    """Collect quote-preview images within bounded element and attachment budgets.

    Normal/parallel message elements are not quotes. Forwarded chat records are
    deliberately not expanded. URLs remain subject to the adapter's downloader.
    """
    images: List[Mapping[str, Any]] = []
    remaining = MAX_QUOTE_ITEMS
    seen: Set[str] = set()

    def visit(elements: Any, depth: int, quoted: bool) -> None:
        nonlocal remaining
        if not isinstance(elements, list) or depth > MAX_QUOTE_DEPTH:
            return
        for element in elements:
            if remaining <= 0 or len(images) >= limit:
                return
            remaining -= 1
            if not isinstance(element, Mapping):
                continue
            kind = str(element.get("message_type", element.get("type", "")))
            if kind == "102":
                continue
            is_quote = quoted or kind == "103"
            attachments = element.get("attachments")
            if is_quote and isinstance(attachments, list):
                for attachment in attachments:
                    if remaining <= 0 or len(images) >= limit:
                        return
                    remaining -= 1
                    if not isinstance(attachment, Mapping):
                        continue
                    mime = str(attachment.get("content_type") or "").lower()
                    media_type = str(attachment.get("type") or "").lower()
                    url = str(attachment.get("url") or "").strip()
                    if url and url not in seen and (
                        mime.startswith("image/") or media_type in {"image", "emoji", "face", "sticker"}
                    ):
                        seen.add(url)
                        images.append(attachment)
            visit(element.get("msg_elements"), depth + 1, is_quote)

    if limit > 0:
        visit(payload.get("msg_elements"), 0, str(payload.get("message_type")) == "103")
    return images
