from unittest.mock import Mock, patch
from PIL import Image
import unittest

from src.desktop_pet.clipboard import copy_image


class ClipboardTests(unittest.TestCase):
    def test_dib_header_and_ownership_transfer(self):
        kernel, user = Mock(), Mock()
        kernel.GlobalAlloc.return_value = 101
        kernel.GlobalLock.return_value = 202
        user.SetClipboardData.return_value = 101
        with patch("ctypes.WinDLL", side_effect=[kernel, user]), patch("ctypes.memmove") as move:
            copy_image(Image.new("RGB", (12, 7), "red"), 303)
        dib = move.call_args.args[1]
        self.assertEqual(int.from_bytes(dib[:4], "little"), 40)
        self.assertEqual(int.from_bytes(dib[4:8], "little"), 12)
        self.assertEqual(int.from_bytes(dib[8:12], "little"), 7)
        user.SetClipboardData.assert_called_once_with(8, 101)
        user.CloseClipboard.assert_called_once()
        kernel.GlobalFree.assert_not_called()

    def test_busy_clipboard_releases_memory(self):
        kernel, user = Mock(), Mock()
        kernel.GlobalAlloc.return_value = 101
        kernel.GlobalLock.return_value = 202
        user.OpenClipboard.return_value = False
        with patch("ctypes.WinDLL", side_effect=[kernel, user]), patch("ctypes.memmove"):
            with self.assertRaises(OSError):
                copy_image(Image.new("RGB", (1, 1)), 303)
        kernel.GlobalFree.assert_called_once_with(101)
        user.SetClipboardData.assert_not_called()


if __name__ == "__main__":
    unittest.main()
