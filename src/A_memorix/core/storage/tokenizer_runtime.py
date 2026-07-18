from typing import Any, Optional

try:
    import jieba
except ImportError:
    jieba = None

HAS_JIEBA = jieba is not None
JIEBA_MODULE: Optional[Any] = jieba
