from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets
from desktop_wallet import Wallet
from pep_i18n import set_ui_language
from pep_qt import MainWindow
from pep_updates import API_URL, available_release, check_latest
from pep_wallet import create_phrase


def release(tag, **fields):
    return {"tag_name": tag, "draft": False, "prerelease": False, **fields}


class UpdateTests(unittest.TestCase):
    def test_stable_versions_only(self):
        for tag in ("v0.5.4", "v0.5.5", "v0.5.6-beta", "v0.5.6/../../elsewhere", None):
            self.assertIsNone(available_release(release(tag), "0.5.5"))
        for flag in ("draft", "prerelease"):
            self.assertIsNone(available_release(release("v1.0.0", **{flag: True}), "0.5.5"))
        self.assertEqual(available_release(release("v0.10.0"), "0.9.9"), "v0.10.0")

    def test_metadata_request(self):
        with patch("pep_updates.urlopen", return_value=BytesIO(json.dumps(release("v10.0.0")).encode())) as get:
            self.assertEqual(check_latest(), "v10.0.0")
            self.assertEqual(get.call_args.args[0].full_url, API_URL)
            self.assertIsNone(get.call_args.args[0].data)
        with patch("pep_updates.urlopen", return_value=BytesIO(b'{"tag')):
            with self.assertRaises(ValueError):
                check_latest()

    def test_notice_after_open_click_and_retry(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        set_ui_language(["ru"])
        self.addCleanup(set_ui_language, ["en"])
        with TemporaryDirectory() as tmp, patch.object(MainWindow, "refresh"), patch("pep_qt.external") as open_link:
            folder = Path(tmp)
            wallet = Wallet.create(folder / "test_wallet", create_phrase(), "temporary")
            settings = QtCore.QSettings(str(folder / "test.ini"), QtCore.QSettings.Format.IniFormat)
            with patch("pep_updates.check_latest", side_effect=[OSError("offline"), "v0.5.6", None]):
                window = MainWindow(wallet, folder, settings)
                try:
                    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
                    window.show()
                    for index, interval in enumerate([1, 6, 6]):
                        if index:
                            window.update_timer.start(0)
                        deadline = time.monotonic() + 4
                        while time.monotonic() < deadline:
                            app.processEvents()
                            if window.update_timer.isActive() and window.update_timer.interval() == interval * 3600000:
                                break
                            time.sleep(0.01)
                        else:
                            self.fail("Release check did not finish")
                        self.assertEqual(window.update_banner.isVisible(), index == 1)
                        self.assertFalse(window.busy)
                        if index == 1:
                            self.assertIn("Новая версия", window.update_banner.text())
                            open_link.assert_not_called()
                            window.update_banner.click()
                            open_link.assert_called_once_with("https://github.com/AltForger/Electrum-PEP/releases/tag/v0.5.6")
                finally:
                    window.close()
