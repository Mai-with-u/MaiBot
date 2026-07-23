from pathlib import Path

import pytest

from src.webui.version_compatibility import (
    compare_versions,
    get_webui_version_compatibility,
    read_local_webui_version,
    read_required_webui_version,
)


def _write_project_files(
    project_root: Path,
    *,
    main_program_version: str = "1.2.3",
    required_webui_version: str = "2.0.0.dev10",
    local_webui_version: str = "2.0.0.dev10",
) -> None:
    (project_root / "dashboard").mkdir()
    (project_root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "MaiBot"',
                f'version = "{main_program_version}"',
                f'dependencies = ["maibot-dashboard>={required_webui_version}"]',
            ]
        ),
        encoding="utf-8",
    )
    (project_root / "dashboard" / "package.json").write_text(
        f'{{"name":"maibot-dashboard","version":"{local_webui_version}"}}',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.6.0.dev9", "1.6.0.dev10", -1),
        ("1.6.0.dev10", "1.6.0.dev10", 0),
        ("1.6.0.dev11", "1.6.0.dev10", 1),
        ("1.6.0", "1.6.0.dev10", 1),
        ("1.6.0a1.dev1", "1.6.0a1", -1),
        ("1.6", "1.6.0", 0),
        ("2.0.0", "1.99.99", 1),
    ],
)
def test_compare_versions(left: str, right: str, expected: int) -> None:
    assert compare_versions(left, right) == expected


def test_read_webui_versions_from_project_files(tmp_path: Path) -> None:
    _write_project_files(tmp_path)

    assert read_required_webui_version(tmp_path) == "2.0.0.dev10"
    assert read_local_webui_version(tmp_path) == "2.0.0.dev10"


@pytest.mark.parametrize(
    ("webui_version", "expected_status"),
    [
        ("2.0.0.dev9", "webui_outdated"),
        ("2.0.0.dev10", "compatible"),
        ("2.0.0.dev11", "compatible"),
        ("2.0.0", "compatible"),
        ("2.0", "compatible"),
        ("2.0.1", "main_program_outdated"),
    ],
)
def test_get_webui_version_compatibility_distinguishes_update_target(
    tmp_path: Path,
    webui_version: str,
    expected_status: str,
) -> None:
    _write_project_files(tmp_path)

    compatibility = get_webui_version_compatibility(webui_version, tmp_path)

    assert compatibility.status == expected_status
    assert compatibility.main_program_version == "1.2.3"
    assert compatibility.webui_version == webui_version
    assert compatibility.required_webui_version == "2.0.0.dev10"


def test_read_required_webui_version_rejects_missing_dependency(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "MaiBot"\nversion = "1.2.3"\ndependencies = []\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="maibot-dashboard"):
        read_required_webui_version(tmp_path)
