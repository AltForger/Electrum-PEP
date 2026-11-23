import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from embit.transaction import Transaction, TransactionInput, TransactionOutput
from embit import script

from desktop_wallet import Coin, Snapshot, Wallet, coins, destination_script, parse_amount, prepare
from pep_wallet import COIN


class DesktopWalletTests(unittest.TestCase):
    words = "abandon " * 11 + "about"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sample.pepwallet"
        self.wallet = Wallet.create(self.path, self.words, "sample password")

    def test_recovery_encryption_and_existing_file(self):
        self.assertNotIn(b"abandon", self.path.read_bytes())
        with self.assertRaises(ValueError):
            Wallet.open(self.path, "wrong")
        with self.assertRaises(ValueError):
            Wallet.create(self.path, self.words, "other")
        opened = Wallet.open(self.path, "sample password")
        self.assertEqual(opened.address(0, 0), "nZVmfmUtKPmskB9Ds4P9GUJy9eYFqPKHqH")

    def test_multiple_inputs_and_change(self):
        coins_in, parents = [], {}
        for i in (0, 1):
            addr = self.wallet.address(i, 0)
            out = TransactionOutput(COIN, script.Script(destination_script(addr)))
            parent = Transaction(vin=[TransactionInput(bytes([i]) * 32, 1)], vout=[out])
            txid = parent.txid().hex()
            parents["/api/v2/tx/" + txid] = json.dumps({"hex": parent.serialize().hex()})
            coins_in.append(Coin(txid, 0, COIN, i, 0))
        snap = Snapshot(2 * COIN, 0, coins_in, [1, 1])
        with patch("desktop_wallet.api", side_effect=parents.__getitem__):
            plan = prepare(self.wallet, snap, self.wallet.address(0, 1), parse_amount("1,5"))
        tx = Transaction.from_string(plan["hex"])
        self.assertEqual(len(tx.vin), 2)
        self.assertEqual(plan["txid"], tx.txid().hex())
        self.assertEqual(tx.vout[0].value, 150000000)
        self.assertEqual(sum(o.value for o in tx.vout) + plan["fee"], 2 * COIN)
        self.assertEqual(tx.vout[1].script_pubkey.data, destination_script(self.wallet.address(1, 1)))

    def test_amount_and_network(self):
        self.assertEqual(coins(100000), "0.001")
        for text in ("NaN", "0", "-1", "0.123456789", "Infinity"):
            with self.assertRaises(ValueError):
                parse_amount(text)
        with self.assertRaises(ValueError):
            destination_script("PsGyoVVSD9VTYoDZGp5FmRB2BSoE69f9ZV")
