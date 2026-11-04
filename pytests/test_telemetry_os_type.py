import pytest

from src.common import remote

ANDROID_ENV = {"ANDROID_ROOT": "/system", "ANDROID_DATA": "/data"}


@pytest.mark.parametrize(
    ("system_name", "env", "expected"),
    [
        ("Windows", {}, "Windows"),
        ("Linux", {}, "Linux"),
        ("Darwin", {}, "macOS"),
        # Termux、proot 容器里的 Linux 版 Python 带着 Android 的环境变量，应识别为 Android
        ("Linux", ANDROID_ENV, "Android"),
        # 只有其中一个变量时不足以判定为 Android
        ("Linux", {"ANDROID_ROOT": "/system"}, "Linux"),
        # 两个变量都在但值不是 Android 的标准路径时，同样不判定为 Android
        ("Linux", {"ANDROID_ROOT": "/other", "ANDROID_DATA": "/data"}, "Linux"),
        # 官方 Android 构建的 CPython 3.13+ 直接返回 "Android"
        ("Android", {}, "Android"),
        ("FreeBSD", {}, "Unknown"),
    ],
)
def test_get_sys_info_reports_os_type(monkeypatch, system_name, env, expected) -> None:
    """按 platform.system() 的返回值和 Android 环境变量，检查遥测上报的系统类型。"""
    monkeypatch.setattr(remote.platform, "system", lambda: system_name)
    for key in ANDROID_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert remote.TelemetryHeartBeatTask._get_sys_info()["os_type"] == expected
