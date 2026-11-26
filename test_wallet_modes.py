from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from embit import base58, bip32, ec, script
from embit.psbt import PSBT
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from desktop_wallet import Wallet, Coin, Snapshot, prepare, destination_script, COIN
from pep_psbt import inspect, sign
from wallet_keys import single_address


class WalletModes(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.a = bip32.HDKey.from_seed(b"A"*32)
        self.b = bip32.HDKey.from_seed(b"B"*32)

    def wallet(self, name, doc):
        path = self.dir / name
        w = Wallet.create_with_keys(path,doc,"test", "mainnet")
        reopened = Wallet.open(path,"test","mainnet")
        self.assertEqual(w.address(0,0),reopened.address(0,0))
        return reopened

    def payment(self, w):
        spk = script.Script(destination_script(w.address(0,0),"mainnet"))
        parent = Transaction(vin=[TransactionInput(bytes([3])*32,0)],vout=[TransactionOutput(3*COIN,spk)])
        coin = Coin(parent.txid().hex(),0,3*COIN,0,0)
        dest = single_address(self.a.derive([5]).get_public_key())
        with patch("mainnet_electrum.raw_transaction",return_value=parent.to_string()):
            result = prepare(w,Snapshot(3*COIN,0,[coin],[1,0]),dest,COIN)
        return PSBT.from_base64(result["psbt"])

    def test_multisig_exchange(self):
        a = self.wallet("one",dict(kind="multisig",master=self.a.to_base58(),cosigners=[self.b.to_public().to_base58()],threshold=2))
        b = self.wallet("two",dict(kind="multisig",master=self.b.to_base58(),cosigners=[self.a.to_public().to_base58()],threshold=2))
        self.assertEqual(a.address(0,0),b.address(0,0))
        self.assertEqual(base58.decode_check(a.address(0,0))[0],22)
        p = self.payment(a)
        partial = sign(a,p)
        self.assertIn("psbt",partial)
        received = PSBT.from_base64(partial["psbt"])
        self.assertEqual(inspect(b,received),inspect(a,p))
        plan = sign(b,received)
        tx = Transaction.from_string(plan["hex"])
        self.assertTrue(tx.vin[0].script_sig.data.endswith(a.keys.redeem(0,0).data))
        self.assertEqual(tx.vin[0].script_sig.data[0],0)
        self.assertEqual(plan["txid"],tx.txid().hex())
        self.assertEqual(inspect(a,p)[1],plan["fee"])
        received.inputs[0].partial_sigs[next(iter(received.inputs[0].partial_sigs))]=b"bad"
        with self.assertRaises(ValueError):
            sign(b,received)

    def test_xpub_external_signing(self):
        cold=self.wallet("cold",{"master":self.a.to_base58()})
        watch=self.wallet("watch",{"master":self.a.to_public().to_base58()})
        self.assertTrue(watch.keys.watch)
        p=self.payment(watch)
        ref = f"{p.tx.vin[0].txid.hex()}:{p.tx.vin[0].vout}"
        cold.set_coin_frozen(ref, True)
        with self.assertRaises(ValueError):
            sign(cold, p)
        cold.set_coin_frozen(ref, False)
        self.assertIn("psbt",sign(watch,p))
        self.assertIn("hex",sign(cold,p))

    def test_imports(self):
        for compressed in (True,False):
            key=ec.PrivateKey((12345).to_bytes(32,"big"),compressed)
            wif=base58.encode_check(bytes([158])+key.secret+(b"\x01" if compressed else b""))
            w=self.wallet(str(compressed),dict(kind="import",entries=[wif]))
            self.assertEqual(w.address(0,0),single_address(key.get_public_key()))
            result=sign(w,self.payment(w));self.assertIn("hex",result)
        watch=self.wallet("addresses",dict(kind="import",entries=[w.address(0,0)]))
        self.assertTrue(watch.keys.watch)
        self.assertIn("psbt",sign(watch,self.payment(watch)))

    def test_ledger_account_and_signature(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        from pep_ledger import pair
        from wallet_keys import ACCOUNT
        app = Mock()
        def get_key(path, showOnScreen=False):
            node = self.a.derive("m/" + path)
            return {"publicKey":node.sec(), "chainCode":node.chain_code}
        app.getWalletPublicKey.side_effect = get_key
        with patch("pep_ledger.device",side_effect=lambda:nullcontext(app)):
            xpub = pair()
            expected = self.a.derive(ACCOUNT).to_public()
            self.assertEqual(xpub,expected.to_base58())
            app.getWalletPublicKey.assert_called_with(ACCOUNT[2:]+"/0/0",showOnScreen=True)
            wallet = self.wallet("ledger",{"master":xpub, "ledger":True})
            packet = self.payment(wallet)
            prev = packet.inputs[0].non_witness_utxo
            digest = packet.tx.sighash_legacy(0,prev.vout[0].script_pubkey,1)
            app.getTrustedInput.return_value = {}
            app.untrustedHashSign.side_effect = lambda path,lockTime: self.a.derive("m/"+path).key.sign(digest).serialize()+b"\x01"
            signed = sign(wallet,packet)
            self.assertIn("hex",signed)
            self.assertNotIn("words",wallet.document)
            self.assertEqual(xpub,wallet.document["master"])
