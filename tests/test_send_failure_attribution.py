"""回归测试：网关失败回执必须携带可归因的错误文本（issue #2052）。

背景
----
报告的五次生产事故里 ``error=`` 一律为空，根因是 ``dict.get(key, default)``
的默认值只在「键不存在」时生效 —— 上游回 ``{"error": ""}`` 这类空串时取到空值，
失败原因在赋值处即丢失。

覆盖 ``_build_receipt`` 的三条失败分支，断言任何 FAILED 回执的 error 都非空、
且带出原始结构：

==== ==============================================================
分支 位置      场景
==== ==============================================================
C    :152     信封层 ``response.error.message`` 为空串
A    :171     信封层 ``payload.success`` 为假（原先会把整个 result 的 repr 当错误文本）
B    :183     信封 success 为真、内层 ``result.success`` 为假且 ``error`` 为空串 ← 生产现场路径
==== ==============================================================

注意
----
分支 A 与 B 走的是**不同**的代码路径。仅按报告里给出的复现用例
（``payload={"success": False, ...}``）编写测试只能覆盖分支 A；生产上
出现空 error 的是分支 B。两条都必须驱动，否则修复无法被有效验证。

若 ``PluginPlatformDriver`` 的构造方式发生变化，请调整 ``_make_driver()``。
"""
from types import SimpleNamespace

import pytest

from src.platform_io.drivers.plugin_driver import PluginPlatformDriver
from src.platform_io.types import DeliveryStatus

ROUTE_KEY = SimpleNamespace(platform="test")


def _make_driver() -> PluginPlatformDriver:
    """构造只测 ``_build_receipt`` 归一化逻辑的最小实例，绕开 Supervisor 依赖。"""
    driver = PluginPlatformDriver.__new__(PluginPlatformDriver)
    driver.driver_id = "test-driver"
    driver.descriptor = SimpleNamespace(kind="plugin")
    return driver


@pytest.mark.parametrize(
    ("response", "label"),
    [
        pytest.param(
            SimpleNamespace(error={"message": ""}, payload=None),
            "C: envelope response.error.message is an empty string",
            id="empty-envelope-message",
        ),
        pytest.param(
            SimpleNamespace(
                error=None,
                payload={"success": False, "result": {"success": False, "error": ""}},
            ),
            "A: envelope payload.success is falsy",
            id="payload-not-success",
        ),
        pytest.param(
            SimpleNamespace(
                error=None,
                payload={"success": True, "result": {"success": False, "error": ""}},
            ),
            "B: inner result.success is False with a blank error",
            id="inner-result-not-success",
        ),
    ],
)
def test_failed_receipt_always_has_error_text(response, label):
    """任何 FAILED 回执都必须能打出非空 error —— 否则日志无法归因。"""
    receipt = _make_driver()._build_receipt("internal-1", ROUTE_KEY, response)

    assert receipt.status is DeliveryStatus.FAILED, label
    assert receipt.error, f"{label}: FAILED receipt logged an empty error"
    assert receipt.error.strip(), f"{label}: error is blank after strip()"


def test_failed_receipt_error_carries_raw_structure():
    """错误字段为空时必须把原始结构带出来，否则下游拿不到可归因信息。"""
    response = SimpleNamespace(
        error=None,
        payload={"success": True, "result": {"success": False, "error": ""}},
    )
    receipt = _make_driver()._build_receipt("internal-2", ROUTE_KEY, response)

    assert "raw=" in receipt.error


def test_success_path_still_reports_sent():
    """回归护栏：成功路径不受本次改动影响。"""
    response = SimpleNamespace(
        error=None,
        payload={"success": True, "result": {"success": True, "message_id": "ext-1"}},
    )
    receipt = _make_driver()._build_receipt("internal-3", ROUTE_KEY, response)

    assert receipt.status is DeliveryStatus.SENT
    assert receipt.external_message_id == "ext-1"
