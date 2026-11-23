"""USB interface to the Pepecoin Ledger app (legacy Bitcoin APDUs)."""
from pep_i18n import tr
from contextlib import contextmanager
import hid
from embit import bip32, ec, hashes
from ledger_bitcoin.btchip.btchip import btchip
from ledger_bitcoin.btchip.btchipComm import HIDDongleHIDAPI
from ledger_bitcoin.btchip.bitcoinTransaction import bitcoinTransaction
from wallet_keys import ACCOUNT


@contextmanager
def device():
    paths = [r["path"] for r in hid.enumerate(0x2c97, 0)
             if r.get("usage_page")==0xffa0 or r.get("interface_number")==0]
    if len(set(paths)) != 1:
        raise ValueError(tr("Connect one unlocked Ledger over USB and open the Pepecoin app on it."))
    handle = hid.device()
    handle.open_path(paths[0])
    try:
        handle.set_nonblocking(True)
        app = btchip(HIDDongleHIDAPI(handle, True, False))
        if app.getAppName() != "Pepecoin":
            raise ValueError(tr("Open the Pepecoin app on your Ledger."))
        yield app
    finally:
        handle.close()


def compressed(data):
    pub = ec.PublicKey.parse(bytes(data))
    pub.compressed = True
    return pub


def read_account(app):
    path = ACCOUNT[2:]
    parent = app.getWalletPublicKey(path.rsplit("/",1)[0])
    node = app.getWalletPublicKey(path)
    fp = hashes.hash160(compressed(parent["publicKey"]).sec())[:4]
    return bip32.HDKey(compressed(node["publicKey"]), bytes(node["chainCode"]), depth=3,
                       fingerprint=fp, child_number=0x80000000)


def pair():
    with device() as app:
        key = read_account(app)
        first = app.getWalletPublicKey(ACCOUNT[2:] + "/0/0", showOnScreen=True)
        if compressed(first["publicKey"]).sec() != key.derive([0,0]).sec():
            raise ValueError(tr("The device address does not match the exported key."))
        return key.to_base58()


def sign_inputs(wallet, psbt, inputs):
    with device() as app:
        account = read_account(app)
        if account.to_base58() != wallet.keys.accounts[0].to_base58():
            raise ValueError(tr("The connected Ledger belongs to another wallet."))
        tx = psbt.tx
        trusted = []
        for scope, vin in zip(psbt.inputs, tx.vin):
            prev = bitcoinTransaction(bytearray(scope.non_witness_utxo.serialize()))
            entry = app.getTrustedInput(prev,scope.vout)
            entry["sequence"] = vin.sequence.to_bytes(4,"little").hex()
            trusted.append(entry)
        first = True
        for i,(_,code,_,_,path) in enumerate(inputs):
            pub = account.derive(path).get_public_key()
            if pub in psbt.inputs[i].partial_sigs:
                continue
            app.startUntrustedTransaction(first, i, trusted, bytearray(code.data), version=tx.version)
            app.finalizeInput(b"",0,0,"",tx.serialize())
            sig = app.untrustedHashSign(ACCOUNT[2:] + f"/{path[0]}/{path[1]}",lockTime=tx.locktime)
            psbt.inputs[i].partial_sigs[pub] = bytes(sig)
            first = False
