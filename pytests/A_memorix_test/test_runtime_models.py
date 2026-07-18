from src.A_memorix.core.runtime import models
from src.A_memorix.core.runtime import sdk_memory_kernel


def test_runtime_models_are_reexported_by_sdk_memory_kernel() -> None:
    assert sdk_memory_kernel.KernelSearchRequest is models.KernelSearchRequest
    assert sdk_memory_kernel._NormalizedSearchTimeWindow is models._NormalizedSearchTimeWindow


def test_kernel_search_request_defaults_are_preserved() -> None:
    request = models.KernelSearchRequest()

    assert request.query == ""
    assert request.limit == 5
    assert request.mode == "search"
    assert request.shared_chat_ids == ()
    assert request.respect_filter is True


def test_normalized_search_time_window_defaults_are_preserved() -> None:
    window = models._NormalizedSearchTimeWindow()

    assert window.numeric_start is None
    assert window.numeric_end is None
    assert window.query_start is None
    assert window.query_end is None
