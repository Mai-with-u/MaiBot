from pathlib import Path
from unittest.mock import patch
import json
import os
import tempfile
import unittest

import pet_client_release as release


class PetClientReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "packages"
        self.environment = patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": "owner/repo",
                "RELEASE_TAG": "v1",
                "SOURCE_COMMIT": "abc",
                "GITHUB_OUTPUT": str(self.root / "outputs"),
                "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.metadata = {
            "id": 1,
            "tag_name": "v1",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/owner/repo/releases/tag/v1",
        }

    def collect_android(self) -> None:
        app = self.root / "pet-clients/android/app"
        package = app / "build/outputs/apk/release/app-release.apk"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"signed apk fixture")
        (app / "build.gradle").write_text("versionName '1.0.2'\n", encoding="utf-8")
        release.collect(self.root, self.output, "android")

    def test_resolve_locks_latest_tag_commit(self) -> None:
        with (
            patch.object(release, "api", return_value=self.metadata) as api,
            patch.object(release, "tag_commit", return_value="abc") as commit,
        ):
            release.resolve()
        api.assert_called_once_with("releases/latest")
        commit.assert_called_once_with("v1")
        self.assertEqual((self.root / "outputs").read_text(), "ready=true\ntag=v1\ncommit=abc\n")

    def test_collect_both_platforms_and_detect_tampering(self) -> None:
        self.collect_android()
        desktop = self.root / "pet-clients/desktop"
        desktop.mkdir(parents=True)
        (desktop / "package.json").write_text(json.dumps({"version": "1.0.1"}))
        package = self.root / "dist/desktop-pet-client/maibot-desktop-pet-1.0.1-win-x64-setup.exe"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"installer fixture")
        release.collect(self.root, self.output, "windows")
        self.assertEqual(len(release.verified_packages(self.output)), 4)
        (self.output / package.name).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "校验和不符"):
            release.verified_packages(self.output)

    def test_attach_rejects_moved_tag(self) -> None:
        with (
            patch.object(release, "api", return_value=self.metadata),
            patch.object(release, "tag_commit", return_value="changed"),
            patch.object(release, "gh") as gh,
        ):
            with self.assertRaisesRegex(ValueError, "已变化"):
                release.attach(self.output)
        gh.assert_not_called()

    def test_attach_uploads_to_locked_release(self) -> None:
        self.collect_android()
        with (
            patch.object(release, "api", return_value=self.metadata) as api,
            patch.object(release, "tag_commit", return_value="abc"),
            patch.object(release, "gh", return_value="[[]]") as gh,
        ):
            release.attach(self.output)
        api.assert_called_once_with("releases/tags/v1")
        upload = gh.call_args_list[-1].args
        self.assertEqual(upload[:3], ("release", "upload", "v1"))
        self.assertNotIn("--clobber", upload)
        self.assertEqual(len(upload), 7)

    def test_attach_does_not_overwrite_conflicting_asset(self) -> None:
        self.collect_android()
        name = "maibot-android-pet-1.0.2.apk"

        def gh(*args: str) -> str:
            if args[0] == "api":
                return json.dumps([[{"name": name}]])
            self.assertEqual(args[:2], ("release", "download"))
            (Path(args[-1]) / name).write_bytes(b"previous release package")
            return ""

        with (
            patch.object(release, "api", return_value=self.metadata),
            patch.object(release, "tag_commit", return_value="abc"),
            patch.object(release, "gh", side_effect=gh),
        ):
            with self.assertRaisesRegex(ValueError, "同名附件"):
                release.attach(self.output)


if __name__ == "__main__":
    unittest.main()
