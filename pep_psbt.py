"""Verified legacy P2PKH and BIP67 P2SH partial transactions for Pepecoin."""
from pep_i18n import tr
from embit import ec, script
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from mainnet_electrum import output_address
from wallet_keys import single_address


def push(data):
    n = len(data)
    if n < 76:
        return bytes([n]) + data
    if n < 256:
        return b"\x4c" + bytes([n]) + data
    return b"\x4d" + n.to_bytes(2, "little") + data


def proposal(wallet, selected, parents, dest, amount, change, change_index):
    from desktop_wallet import destination_script
    outs = [TransactionOutput(amount, script.Script(destination_script(dest, wallet.network)))]
    if change:
        addr = wallet.change_address(change_index)
        outs.append(TransactionOutput(change, script.Script(destination_script(addr, wallet.network))))
    ins = [TransactionInput(bytes.fromhex(c.txid), c.index) for c in selected]
    p = PSBT(Transaction(version=1, vin=ins, vout=outs))
    for scope, coin, parent in zip(p.inputs, selected, parents):
        scope.non_witness_utxo = parent
        for account in wallet.keys.accounts:
            path = [coin.branch, coin.child]
            scope.bip32_derivations[account.derive(path).get_public_key()] = DerivationPath(account.my_fingerprint, path)
        if wallet.keys.kind == "multisig":
            scope.redeem_script = wallet.keys.redeem(coin.branch, coin.child)
    inspect(wallet, p)
    return {"psbt": p.to_base64()}


def details(wallet, scope):
    keys = wallet.keys
    parent = scope.non_witness_utxo
    if parent is None or parent.txid() != scope.txid or not 0 <= scope.vout < len(parent.vout):
        raise ValueError(tr("The previous transaction for this input does not match."))
    if scope.witness_utxo or scope.final_scriptsig or scope.final_scriptwitness or scope.sighash_type not in (None, 1):
        raise ValueError(tr("Only unsigned PEP inputs with SIGHASH_ALL are supported."))
    output = parent.vout[scope.vout]
    address = output_address(output.script_pubkey.data)
    if keys.entries:
        entries = dict(keys.entries)
        if address not in entries:
            raise ValueError(tr("The input belongs to a different wallet."))
        key = entries[address]
        return output, output.script_pubkey, ([key.get_public_key()] if key else []), key, None
    path = next((d.derivation for pub,d in scope.bip32_derivations.items()
                 if d.fingerprint == keys.accounts[0].my_fingerprint and len(d.derivation)==2
                 and d.derivation[0] in (0,1) and d.derivation[1]<2**31
                 and keys.accounts[0].derive(d.derivation).sec()==pub.sec()), None)
    if path is None or wallet.address(*path) != address:
        raise ValueError(tr("The input path or address does not match the wallet."))
    children = keys.children(*path)
    pubs = sorted((k.get_public_key() for k in children), key=lambda p:p.sec())
    code = output.script_pubkey
    if keys.kind == "multisig":
        code = keys.redeem(*path)
        if scope.redeem_script != code:
            raise ValueError(tr("The cosigners or signature threshold do not match."))
    return output, code, pubs, (children[0].key if children[0].is_private else None), path


def inspect(wallet, psbt):
    tx = psbt.tx
    if not tx.vin or not tx.vout or len({(i.txid,i.vout) for i in tx.vin}) != len(tx.vin):
        raise ValueError(tr("Empty transaction or duplicate input."))
    total = sum(details(wallet, scope)[0].value for scope in psbt.inputs)
    outputs = []
    for out in tx.vout:
        addr = output_address(out.script_pubkey.data)
        if not addr or out.value <= 0:
            raise ValueError(tr("Unsupported payment output."))
        outputs.append((addr, out.value))
    fee = total - sum(amount for _,amount in outputs)
    if fee < 0:
        raise ValueError(tr("The output total exceeds the input total."))
    return outputs, fee


def sign(wallet, p):
    outputs, fee = inspect(wallet, p)
    tx = p.tx
    inputs = [details(wallet,s) for s in p.inputs]
    if wallet.keys.ledger:
        from pep_ledger import sign_inputs
        sign_inputs(wallet,p,inputs)
    complete = True
    for i,(scope,(_,code,pubs,key,path)) in enumerate(zip(p.inputs,inputs)):
        digest = tx.sighash_legacy(i, code, 1)
        if key:
            scope.partial_sigs[key.get_public_key()] = key.sign(digest).serialize() + b"\x01"
        if not pubs and wallet.keys.entries:
            pubs = [pub for pub in scope.partial_sigs if single_address(pub)==output_address(code.data)]
        signatures = []
        for pub in pubs:
            sig = scope.partial_sigs.get(pub)
            if sig is None:
                continue
            if sig[-1:] != b"\x01" or not pub.verify(ec.Signature.parse(sig[:-1]), digest):
                raise ValueError(tr("Invalid cosigner signature."))
            signatures.append((pub,sig))
        if len(signatures) < wallet.keys.threshold:
            complete = False
        elif wallet.keys.kind == "multisig":
            tx.vin[i].script_sig = script.Script(b"\x00" + b"".join(push(s) for _,s in signatures[:wallet.keys.threshold]) + push(code.data))
        else:
            pub,sig = signatures[0]
            tx.vin[i].script_sig = script.Script(push(sig) + push(pub.sec()))
    if not complete:
        return {"psbt":p.to_base64()}
    return {"network":wallet.network, "hex":tx.to_string(),"txid":tx.txid().hex(),
            "fee":fee,"amount":outputs[0][1],"address":outputs[0][0]}
