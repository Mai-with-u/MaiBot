from src.A_memorix.paths import default_data_dir, repo_root


def test_default_data_dir_matches_runtime_template() -> None:
    assert default_data_dir() == repo_root() / "data" / "a-memorix"
