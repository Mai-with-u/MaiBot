"""市场业务错误；由 HTTP 和 SDK 适配层各自映射传输错误。"""


class MarketError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)
