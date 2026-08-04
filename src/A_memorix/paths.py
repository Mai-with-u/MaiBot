from __future__ import annotations

from pathlib import Path

A_MEMORIX_SYSTEM_ID = "a_memorix"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def src_root() -> Path:
    return package_root().parent


def repo_root() -> Path:
    return src_root().parent


def config_path() -> Path:
    return repo_root() / "config" / f"{A_MEMORIX_SYSTEM_ID}.toml"


def default_data_dir() -> Path:
    return repo_root() / "data" / "a-memorix"


def artifacts_root() -> Path:
    return default_data_dir() / "artifacts"


def schema_path() -> Path:
    return package_root() / "config_schema.json"


def web_root() -> Path:
    return package_root() / "web"


def scripts_root() -> Path:
    return package_root() / "scripts"


def resolve_repo_path(raw_path: str | Path | None, *, fallback: Path | None = None) -> Path:
    if raw_path is None:
        return (fallback or default_data_dir()).resolve()

    raw_value = str(raw_path).strip()
    if not raw_value:
        return (fallback or default_data_dir()).resolve()

    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    return (repo_root() / candidate).resolve()
