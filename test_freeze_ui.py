from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets

from desktop_wallet import Coin, Snapshot, Wallet, coins, estimated_fee
from pep_qt import MainWindow
from pep_wallet import COIN, create_phrase


class FreezeUITests(unittest.TestCase):
    def test_freeze_max_unfreeze_and_save_failure(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with TemporaryDirectory() as tmp, patch.object(MainWindow, "refresh"):
            folder = Path(tmp)
            wallet = Wallet.create(folder / "wallet", create_phrase(), "password", "mainnet")
            settings = QtCore.QSettings(str(folder / "ui.ini"), QtCore.QSettings.Format.IniFormat)
            window = MainWindow(wallet, folder, settings)
            try:
                first, second = [Coin("11"*32, i, n*COIN, 0, 0) for i, n in enumerate((3, 1))]
                window.snapshot = Snapshot(4*COIN, 0, [first, second], [1, 0])
                window.paint_data()
                window.outputs.topLevelItem(0).setCheckState(3, QtCore.Qt.CheckState.Checked)
                window.paint_data()
                self.assertEqual(window.outputs.topLevelItem(0).checkState(3), QtCore.Qt.CheckState.Checked)
                window.maximum()
                fee = estimated_fee(1, window.rate(), wallet.keys.input_bytes())
                self.assertEqual(window.amount.text(), coins(COIN-fee))
                row = window.outputs.topLevelItem(1)
                with patch.object(wallet.storage, "write", side_effect=OSError("write failed")):
                    row.setCheckState(3, QtCore.Qt.CheckState.Checked)
                self.assertEqual(row.checkState(3), QtCore.Qt.CheckState.Unchecked)
                row.setCheckState(3, QtCore.Qt.CheckState.Checked)
                window.maximum()
                self.assertEqual(window.amount.text(), "0")
                window.outputs.topLevelItem(0).setCheckState(3, QtCore.Qt.CheckState.Unchecked)
                window.maximum()
                self.assertEqual(window.amount.text(), coins(3*COIN-fee))
                reopened = Wallet.open(folder / "wallet", "password", "mainnet")
                self.assertEqual(reopened.frozen_outpoints(), {second.outpoint})
            finally:
                window.close()
