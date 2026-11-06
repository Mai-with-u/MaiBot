from pathlib import Path

import json

from src.plugins.built_in.qq_official_adapter.nickname_cache import (
    NicknameCache,
    cache_key,
    is_placeholder_nickname,
    sanitize_nickname,
)


class FakeClock:
    """可手动推进的时钟，避免测试里真的等待 TTL 过期。"""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _cache(tmp_path: Path, clock: FakeClock, *, ttl_days: int = 30, max_entries: int = 100) -> NicknameCache:
    return NicknameCache(tmp_path / "nicknames.json", ttl_days=ttl_days, max_entries=max_entries, clock=clock)


def test_sanitize_nickname_strips_invisible_and_whitespace() -> None:
    # 从群名片复制出来的昵称常带零宽/双向控制字符。
    assert sanitize_nickname("  Cypas_Nya\u202d\u200b  ") == "Cypas_Nya"
    assert sanitize_nickname("小 明\n二号") == "小 明 二号"
    assert sanitize_nickname("x" * 100) == "x" * 64
    assert sanitize_nickname(None) == ""
    assert sanitize_nickname(123) == ""


def test_placeholder_detection() -> None:
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"
    assert is_placeholder_nickname(openid, openid)
    assert is_placeholder_nickname(openid.lower(), openid)
    assert is_placeholder_nickname("", openid)
    # 解码器回退出来的 OpenID 即便和当前 OpenID 不等，也不该当成真实昵称。
    assert is_placeholder_nickname("92A90A9A8F205F9DB59C3DD10827783B", openid)
    assert not is_placeholder_nickname("小明", openid)


def test_group_nickname_feeds_private_lookup(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path, clock)
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"

    assert cache.lookup("1906281926", openid) == ""
    assert cache.remember("1906281926", openid, "小明", group_openid="GROUP1") is True
    assert cache.lookup("1906281926", openid) == "小明"

    # 同一昵称重复出现只刷新时间戳，不触发落盘。
    clock.advance(60)
    assert cache.remember("1906281926", openid, "小明") is False
    assert cache.snapshot()[cache_key("1906281926", openid)]["updated_at"] == clock.now

    # 改名以最新为准。
    clock.advance(60)
    assert cache.remember("1906281926", openid, "小明二号") is True
    assert cache.lookup("1906281926", openid) == "小明二号"


def test_placeholder_nickname_is_never_cached(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path, clock)
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"

    assert cache.remember("1906281926", openid, openid) is False
    assert cache.remember("1906281926", openid, "   ") is False
    assert cache.lookup("1906281926", openid) == ""
    assert len(cache) == 0


def test_entries_expire_after_ttl(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path, clock, ttl_days=1)
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"

    cache.remember("1906281926", openid, "小明")
    clock.advance(86400 - 1)
    assert cache.lookup("1906281926", openid) == "小明"

    clock.advance(2)
    assert cache.lookup("1906281926", openid) == ""
    assert len(cache) == 0


def test_cache_is_scoped_by_app_id(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path, clock)
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"

    cache.remember("app-a", openid, "小明")
    assert cache.lookup("app-a", openid) == "小明"
    # 换机器人应用后 OpenID 会变，缓存不能跨应用串。
    assert cache.lookup("app-b", openid) == ""


def test_overflow_evicts_oldest_entry(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path, clock, max_entries=2)

    for index, openid in enumerate(("A" * 32, "B" * 32, "C" * 32)):
        clock.advance(1)
        cache.remember("1906281926", openid, f"用户{index}")

    assert len(cache) == 2
    assert cache.lookup("1906281926", "A" * 32) == ""
    assert cache.lookup("1906281926", "B" * 32) == "用户1"
    assert cache.lookup("1906281926", "C" * 32) == "用户2"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    clock = FakeClock()
    openid = "5A2B24CF1520E16030C7D7E1FA447CC3"
    cache = _cache(tmp_path, clock)
    cache.remember("1906281926", openid, "小明", group_openid="GROUP1")
    cache.save()

    reloaded = _cache(tmp_path, clock)
    assert reloaded.load() == 1
    assert reloaded.lookup("1906281926", openid) == "小明"
    assert reloaded.snapshot()[cache_key("1906281926", openid)]["source_group"] == "GROUP1"


def test_corrupted_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "nicknames.json"
    path.write_text("{not json", encoding="utf-8")

    clock = FakeClock()
    cache = _cache(tmp_path, clock)
    assert cache.load() == 0
    assert cache.lookup("1906281926", "A" * 32) == ""

    # 下次写入应直接覆盖损坏文件。
    cache.remember("1906281926", "A" * 32, "小明")
    cache.save()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_unchanged_nickname_is_repersisted_daily(tmp_path: Path) -> None:
    """昵称没变时也要定期回写，否则长期不动盘会让缓存在重启后被 TTL 清掉。"""
    clock = FakeClock()
    cache = _cache(tmp_path, clock, ttl_days=30)
    openid = "A" * 32

    assert cache.remember("1906281926", openid, "小明") is True
    clock.advance(3600)
    assert cache.remember("1906281926", openid, "小明") is False
    clock.advance(86400)
    assert cache.remember("1906281926", openid, "小明") is True
