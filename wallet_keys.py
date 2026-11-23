"""Mainnet account keys, imported PEP addresses and sorted P2SH multisig."""
from pep_i18n import tr
from embit import base58, bip32, bip39, ec, hashes, script

ACCOUNT = "m/44'/3434'/0'"
PUB = bytes.fromhex("0488b21e")
PRV = bytes.fromhex("0488ade4")


def account_key(text):
    data = base58.decode_check(text.strip())
    versions = {PUB: PUB, PRV: PRV, bytes.fromhex("02facafd"): PUB, bytes.fromhex("02fac398"): PRV}
    if len(data) != 78 or data[:4] not in versions:
        raise ValueError(tr("Enter a mainnet master key (xpub, xprv, dgub or dgpv)."))
    key = bip32.HDKey.parse(versions[data[:4]] + data[4:])
    if key.is_private != (versions[data[:4]] == PRV):
        raise ValueError(tr("Invalid key type."))
    return key


def public(key):
    return key.to_public() if key.is_private else key


def wif_key(text):
    raw = base58.decode_check(text)
    if raw[:1] != bytes([158]) or len(raw) not in (33, 34) or (len(raw) == 34 and raw[-1] != 1):
        raise ValueError(tr("A Pepecoin mainnet WIF key is required."))
    key = ec.PrivateKey(raw[1:33], compressed=len(raw) == 34)
    return key


def single_address(pub):
    return base58.encode_check(bytes([56]) + hashes.hash160(pub.sec()))


def own_account(doc):
    if doc.get("words"):
        words = doc["words"]
        if not bip39.mnemonic_is_valid(words):
            raise ValueError(tr("Check the words in your BIP39 seed phrase."))
        return bip32.HDKey.from_seed(bip39.mnemonic_to_seed(words)).derive(ACCOUNT)
    return account_key(doc["master"])


class KeySet:
    def __init__(self, doc):
        self.kind = doc.get("kind", "standard")
        self.ledger = bool(doc.get("ledger"))
        self.threshold = doc.get("threshold", 1)
        self.entries = []
        self.accounts = []
        if self.kind == "import":
            modes = set()
            for value in dict.fromkeys(doc["entries"]):
                raw = base58.decode_check(value)
                if len(raw) == 21 and raw[0] in (22, 56):
                    modes.add("watch")
                    self.entries.append((value, None))
                else:
                    key = wif_key(value)
                    modes.add("sign")
                    self.entries.append((single_address(key.get_public_key()), key))
            if len(modes) != 1 or not self.entries:
                raise ValueError(tr("Import a list of addresses or a list of WIF keys of the same type."))
        else:
            self.accounts = [own_account(doc)]
            if self.kind == "multisig":
                others = [account_key(k) for k in doc["cosigners"]]
                if any(k.is_private for k in others):
                    raise ValueError(tr("Enter only public keys for the other cosigners."))
                self.accounts.extend(others)
                if not 1 <= self.threshold <= len(self.accounts) <= 15 or len(self.accounts) < 2:
                    raise ValueError(tr("Check the signature threshold and the number of cosigners (2–15)."))
                if len({(k.sec(),k.chain_code) for k in self.accounts}) != len(self.accounts):
                    raise ValueError(tr("Each cosigner must have a different key."))
        self.watch = (all(k is None for _, k in self.entries) if self.entries else
                      not self.accounts[0].is_private and not self.ledger)

    def children(self, branch, index):
        return [k.derive([branch,index]) for k in self.accounts]

    def redeem(self, branch, index):
        pubs = sorted((k.get_public_key() for k in self.children(branch,index)), key=lambda p:p.sec())
        return script.multisig(self.threshold, pubs)

    def address(self, branch, index):
        if self.entries:
            return self.entries[index][0]
        if self.kind == "multisig":
            return base58.encode_check(bytes([22]) + hashes.hash160(self.redeem(branch,index).data))
        return single_address(self.accounts[0].derive([branch,index]).get_public_key())

    def input_bytes(self):
        if self.kind == "multisig":
            return 50 + 74*self.threshold + 34*len(self.accounts)
        return 181 if self.entries else 149
