from io import StringIO

from src.common.logger import get_console_handler, redirect_console_logs


def test_redirect_console_logs_restores_original_stream() -> None:
    handler = get_console_handler()
    original_stream = handler.stream
    redirected_stream = StringIO()

    with redirect_console_logs(redirected_stream):
        assert handler.stream is redirected_stream

    assert handler.stream is original_stream
