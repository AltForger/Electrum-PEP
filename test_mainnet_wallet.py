from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from embit import ec, script
from embit.transaction import Transaction, TransactionInput, TransactionOutput

import mainnet_electrum
from desktop_wallet import Coin, Snapshot, Wallet, broadcast, destination_script, prepare
from pep_wallet import COIN, address, restore


class MainnetTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "sample.pepwallet"
        self.words = "abandon " * 11 + "about"
        self.wallet = Wallet.create(path, self.words, "test password", "mainnet")
        self.addr = self.wallet.address(0, 0)
        self.parent = Transaction(vin=[TransactionInput(b"a" * 32, 0)], vout=[
            TransactionOutput(3 * COIN, script.Script(destination_script(self.addr, "mainnet")))])
        self.txid = self.parent.txid().hex()

    def test_restore_and_network_isolation(self):
        path = Path(self.wallet.storage.path)
        with self.assertRaises(ValueError):
            Wallet.open(path, "test password", "testnet")
        reopened = Wallet.open(path, "test password", "mainnet")
        self.assertEqual(reopened.address(0, 0), address(restore(self.words), "mainnet"))
        test_address = address(restore(self.words), "testnet")
        with self.assertRaises(ValueError):
            destination_script(test_address, "mainnet")
        with self.assertRaises(ValueError):
            destination_script(self.addr)
        with self.assertRaises(ValueError):
            broadcast({"network": "mainnet"}, "testnet")

    def test_mainnet_payment_signature_recipient_fee_and_change(self):
        snap = Snapshot(3 * COIN, 0, [Coin(self.txid, 0, 3 * COIN, 0, 0)], [1, 3])
        dest = self.wallet.address(0, 1)
        with patch.object(mainnet_electrum, "raw_transaction", return_value=self.parent.serialize().hex()):
            plan = prepare(self.wallet, snap, dest, COIN)
        tx = Transaction.from_string(plan["hex"])
        self.assertEqual(plan["network"], "mainnet")
        self.assertEqual(plan["txid"], tx.txid().hex())
        self.assertEqual(tx.vin[0].txid, self.parent.txid())
        self.assertEqual(tx.vout[0].value, COIN)
        self.assertEqual(mainnet_electrum.output_address(tx.vout[0].script_pubkey.data), dest)
        self.assertEqual(mainnet_electrum.output_address(tx.vout[1].script_pubkey.data), self.wallet.address(1, 3))
        self.assertEqual(tx.vout[1].value + COIN + plan["fee"], 3 * COIN)
        sig = tx.vin[0].script_sig.data
        self.assertEqual(sig[sig[0]], 1)
        self.assertTrue(ec.PublicKey.parse(sig[sig[0] + 2:]).verify(
            ec.Signature.parse(sig[1:sig[0]]), tx.sighash_legacy(0, self.parent.vout[0].script_pubkey)))
        client = Mock()
        client.call.side_effect = [mainnet_electrum.ServerError("not found"), plan["txid"]]
        with patch.object(mainnet_electrum, "connected", side_effect=lambda fn: fn(client)):
            broadcast(plan, "mainnet")
        client.call.assert_called_with("blockchain.transaction.broadcast", [plan["hex"]])
        client.call.reset_mock()
        client.call.side_effect = [plan["hex"]]
        mainnet_electrum.broadcast(client, plan)
        client.call.assert_called_once_with("blockchain.transaction.get", [plan["txid"]])

    def test_server_selection_and_connection_reuse(self):
        primary, fallback = mainnet_electrum.SERVERS
        peer = Mock(host=fallback, port=50002)
        peer.__enter__ = Mock(return_value=peer)
        with patch.object(mainnet_electrum, "Client", side_effect=[OSError("offline"), peer]) as ctor:
            connection = mainnet_electrum.WalletConnection()
            self.assertEqual(connection.run(lambda c: c.host), fallback)
            self.assertEqual(connection.run(lambda c: c.port), 50002)
            self.assertEqual(connection.status[:2], ("Connected · TLS", fallback + ":50002"))
            self.assertEqual([call.args for call in ctor.call_args_list], [(primary, 50002), (fallback, 50002)])
            connection.close()
            peer.close.assert_called_once()
        with patch.object(mainnet_electrum, "Client", side_effect=OSError("offline")) as ctor:
            connection = mainnet_electrum.WalletConnection(("my-server.example", 61000))
            with self.assertRaises(ConnectionError):
                connection.run(lambda c: c.host)
            ctor.assert_called_once_with("my-server.example", 61000)
            self.assertEqual(connection.status[1], "")
            self.assertIn("my-server.example:61000", connection.status[2])

    def test_server_address(self):
        self.assertEqual(mainnet_electrum.server_endpoint("[::1]", 50002), ("::1", 50002))
        for host, port in [("https://example.com", 50002), ("", 50002), ("myserver", 65536)]:
            with self.assertRaises(ValueError):
                mainnet_electrum.server_endpoint(host, port)

    def test_scan_and_pending_spend(self):
        spend = Transaction(vin=[TransactionInput(self.parent.txid(), 0)], vout=[
            TransactionOutput(2 * COIN, script.Script(destination_script(self.wallet.address(1, 2), "mainnet")))])
        raws = {self.txid: self.parent, spend.txid().hex(): spend}
        history = [{"tx_hash": self.txid, "height": 100}, {"tx_hash": spend.txid().hex(), "height": 0}]
        import hashlib
        def sh(addr):
            return hashlib.sha256(destination_script(addr, "mainnet")).digest()[::-1].hex()
        by_hash = {sh(self.addr): history, sh(self.wallet.address(1, 2)): history[1:]}
        def many(method, args):
            if method.endswith("get_history"):
                return [by_hash.get(arg[0], []) for arg in args]
            if method.endswith("transaction.get"):
                return [raws[arg[0]].serialize().hex() for arg in args]
            return ["00" * 80 for _ in args]
        client = Mock()
        client.call.return_value = {"height": 120}
        client.many.side_effect = many
        snap = mainnet_electrum.scan(client, self.wallet, True)
        self.assertEqual((snap.confirmed, snap.pending, snap.next_indices), (3 * COIN, -COIN, [1, 3]))
        self.assertEqual(snap.utxos, [])
        self.assertEqual([t["confirmations"] for t in snap.transactions], [0, 21])

