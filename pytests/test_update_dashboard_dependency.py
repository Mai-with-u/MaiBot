from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

from packaging.version import Version
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / ".github/scripts/update_dashboard_dependency.py"
SPEC = spec_from_file_location("update_dashboard_dependency", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
updater = module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
VERSION = Version("1.9.4.dev20261019539")
REQUIREMENT = f"maibot-dashboard=={VERSION}"


def test_waits_until_uv_can_resolve_exact_version() -> None:
    responses = [
        CompletedProcess([], 1, "", f"Because there is no version of {REQUIREMENT}"),
        CompletedProcess([], 0, REQUIREMENT, "Resolved 1 package"),
    ]
    with patch.object(updater, "run", side_effect=responses) as resolve, patch.object(updater, "sleep") as sleep:
        updater.wait_for_pypi_version(VERSION)

    assert resolve.call_count == 2
    assert sleep.call_count == 1
    assert resolve.call_args.kwargs["input"] == f"{REQUIREMENT}\n"
    command = resolve.call_args.args[0]
    assert command[:3] == ["uv", "pip", "compile"]
    assert command[command.index("--index") + 1] == updater.PYPI_INDEX_URL
    assert "--refresh-package" in command


def test_missing_version_times_out() -> None:
    with (
        patch.object(updater, "monotonic", side_effect=[0, 0, 300, 300]),
        patch.object(updater, "run", return_value=CompletedProcess(
            [], 1, "", f"Because there is no version of {REQUIREMENT}"
        )),
        patch.object(updater, "sleep"),
        pytest.raises(TimeoutError, match=str(VERSION).replace(".", r"\.")),
    ):
        updater.wait_for_pypi_version(VERSION)


@pytest.mark.parametrize("error", ["HTTP 403 Forbidden", "incompatible Python version", "invalid index metadata"])
def test_other_errors_are_reported_without_retry(error: str) -> None:
    with (
        patch.object(updater, "run", return_value=CompletedProcess([], 1, "", error)),
        patch.object(updater, "sleep") as sleep,
    ):
        with pytest.raises(CalledProcessError) as caught:
            updater.wait_for_pypi_version(VERSION)
    assert caught.value.stderr == error
    sleep.assert_not_called()


def test_index_failure_does_not_modify_dependencies() -> None:
    with (
        patch.object(updater, "get_target_base_version", return_value=Version("1.9.4")),
        patch.object(updater, "get_target_dev_version", return_value=VERSION),
        patch.object(updater, "wait_for_pypi_version", side_effect=TimeoutError),
        patch.object(updater, "update_pyproject") as update_pyproject,
        patch.object(updater, "update_requirements") as update_requirements,
        pytest.raises(TimeoutError),
    ):
        updater.main()
    update_pyproject.assert_not_called()
    update_requirements.assert_not_called()
