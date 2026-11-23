"""Pepecoin wallet services for the Windows desktop application."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from electrum import bitcoin, constants
from electrum.bip32 import BIP32Node
from electrum.crypto import hash_160
from electrum.descriptor import get_singlesig_descriptor_from_legacy_leaf
from electrum.keystore import bip39_is_checksum_valid, bip39_to_seed
from electrum.storage import WalletStorage
from electrum.stored_dict import StorageEncryptionVersion
from electrum.transaction import PartialTransaction, PartialTxInput, PartialTxOutput, Transaction, TxOutpoint

import mainnet_electrum
from pep_wallet import COIN, NETWORKS
from wallet_keys import KeySet


BASE = "https://testnet.pepeblocks.com"
DUST = COIN // 100


def coins(value):
    return format(Decimal(value) / COIN, ".8f").rstrip("0").rstrip(".") or "0"


def parse_amount(text):
    try:
        amount = Decimal(text.strip().replace(",", "."))
        if not amount.is_finite() or not Decimal("0.01") <= amount < Decimal("1e11"):
            raise ValueError
        base = amount * COIN
        if base != base.to_integral_value():
            raise ValueError
        return int(base)
    except (InvalidOperation, ValueError):
        raise ValueError("Введите сумму от 0,01 PEP, до 8 знаков после запятой.") from None


def destination_script(address, network="testnet"):
    try:
        payload = bitcoin.DecodeBase58Check(address)
        if len(payload) == 21 and network == "mainnet" and payload[0] == 22:
            return b"\xa9\x14" + payload[1:] + b"\x87"
        if len(payload) != 21 or payload[0] != NETWORKS[network][1]:
            raise ValueError
    except Exception:
        raise ValueError(f"Введите адрес Pepecoin для сети {network}.") from None
    return b"\x76\xa9\x14" + payload[1:] + b"\x88\xac"


def api(path, body=None):
    request = Request(BASE + path, data=body, headers={
        "User-Agent": "Electrum-PEP/0.1 (Windows; PEP testnet)", "Accept": "application/json",
        "Content-Type": "text/plain",
    })
    with urlopen(request, timeout=12) as response:
        return response.read(8_000_001).decode().strip()


def network_genesis(network):
    if network == "mainnet":
        return mainnet_electrum.GENESIS
    from testnet_peer import GENESIS
    return GENESIS


def check_network():
    if api("/api/getblockhash?index=0").strip('"') != network_genesis("testnet"):
        raise ValueError("Сервер вернул начальный блок другой сети.")


def account_from_words(words, network="testnet"):
    words = " ".join(words.lower().split())
    if len(words.split()) not in (12, 15, 18, 21, 24) or bip39_is_checksum_valid(words) != (True, True):
        raise ValueError("Проверьте порядок и написание слов сид-фразы BIP39.")
    root = BIP32Node.from_rootseed(bip39_to_seed(words, passphrase=""), xtype="standard")
    return root.subkey_at_private_derivation(f"m/44'/{NETWORKS[network][0]}'/0'")


class Wallet:
    def __init__(self, storage, document, network="testnet"):
        if network not in ("mainnet", "testnet"):
            raise ValueError("Неизвестная сеть кошелька.")
        self.network = network
        self.genesis = network_genesis(network)
        if document.get("format") != f"electrum-pep-{network}-1" or document.get("genesis") != self.genesis:
            raise ValueError(f"Файл не принадлежит выбранной сети {network}.")
        self.storage = storage
        self.document = document
        self.keys = KeySet(document) if network == "mainnet" else None
        self.advanced = bool(document.get("kind") or document.get("master"))
        if self.advanced:
            if self.keys is None:
                raise ValueError("Требуется основная сеть.")
            return
        self.account = account_from_words(document["words"], network)
        self.branches = [self.account.subkey_at_private_derivation([i]).convert_to_public() for i in range(2)]

    @classmethod
    def create(cls, path, words, password, network="testnet"):
        return cls.create_with_keys(path, {"words": " ".join(words.lower().split())}, password, network)

    @classmethod
    def create_with_keys(cls, path, keys, password, network="mainnet"):
        if not password:
            raise ValueError("Введите пароль для шифрования файла кошелька.")
        storage = WalletStorage(str(path))
        if storage.file_exists():
            raise ValueError("Файл с таким именем уже существует.")
        doc = {**keys, "format": f"electrum-pep-{network}-1", "genesis": network_genesis(network)}
        wallet = cls(storage, doc, network)
        storage.set_password(password, StorageEncryptionVersion.USER_PASSWORD)
        wallet.save()
        return wallet

    @classmethod
    def open(cls, path, password, network="testnet"):
        storage = WalletStorage(str(path))
        if not storage.file_exists() or not storage.is_encrypted_with_user_pw():
            raise ValueError("Выберите зашифрованный файл кошелька Electrum-PEP.")
        try:
            storage.decrypt(password)
        except Exception as e:
            raise ValueError("Не удалось открыть кошелёк. Проверьте пароль и файл.") from e
        return cls(storage, json.loads(storage.read()), network)

    def change_address(self, index):
        if self.keys and self.keys.entries:
            return self.address(0, 0)
        return self.address(1, index)

    def save(self):
        self.storage.write(json.dumps(self.document))

    def address(self, branch, index):
        if self.advanced:
            return self.keys.address(branch,index)
        key = self.branches[branch].subkey_at_public_derivation([index]).eckey
        return bitcoin.hash160_to_b58_address(hash_160(key.get_public_key_bytes(compressed=True)), NETWORKS[self.network][1])


@dataclass(frozen=True)
class Coin:
    txid: str
    index: int
    value: int
    branch: int
    child: int


@dataclass
class Snapshot:
    confirmed: int
    pending: int
    utxos: list
    next_indices: list
    addresses: list = field(default_factory=list)
    transactions: list = field(default_factory=list)


def discover(wallet, *, history=False, run=None):
    if wallet.network == "mainnet":
        return (run or mainnet_electrum.connected)(lambda client: mainnet_electrum.scan(client, wallet, history))
    check_network()
    xpub = wallet.account.convert_to_public().to_xpub(net=constants.BitcoinTestnet)
    details = "txs" if history else "tokenBalances"
    query = f"/api/v2/xpub/{xpub}?details={details}&tokens=used&gap=20&pageSize=100"
    summary = json.loads(api(query))
    if summary.get("address") != xpub:
        raise ValueError("Сервер вернул данные другого счёта.")
    rows = json.loads(api(f"/api/v2/utxo/{xpub}?gap=20"))
    next_indices = [0, 0]
    utxos = []

    def locate(path, addr):
        # Blockbook supplies its own account prefix. Match only children of our xpub.
        branch, child = map(int, path.rsplit("/", 2)[1:])
        if branch not in (0, 1) or not 0 <= child < 2**31 or wallet.address(branch, child) != addr:
            raise ValueError("Адрес сервера не совпал с адресом кошелька.")
        next_indices[branch] = max(next_indices[branch], child + 1)
        return branch, child

    addresses = []
    for token in summary.get("tokens", []):
        if token.get("type") == "XPUBAddress":
            branch, child = locate(token["path"], token["name"])
            addresses.append((branch, child, token["name"], int(token.get("balance", 0)), int(token["transfers"])))
    for row in rows:
        branch, child = locate(row["path"], row["address"])
        confirmations = int(row["confirmations"])
        if confirmations > 0 and (not row.get("coinbase") or confirmations >= 240):
            utxos.append(Coin(row["txid"], int(row["vout"]), int(row["value"]), branch, child))
    transactions = list(summary.get("transactions", []))
    if history:
        for page in range(2, int(summary.get("totalPages", 1)) + 1):
            data = json.loads(api(query + f"&page={page}"))
            if data.get("address") != xpub:
                raise ValueError("Счёт истории не совпал.")
            transactions.extend(data.get("transactions", []))
        transactions = list({tx["txid"]: tx for tx in transactions}.values())
    return Snapshot(int(summary["balance"]), int(summary["unconfirmedBalance"]), utxos, next_indices,
                    addresses, transactions)


def estimated_fee(input_count, rate, input_size=149):
    return ((10 + input_size * input_count + 68) * rate + 999) // 1000


def prepare(wallet, snapshot, destination, amount, fee_rate=COIN // 100, *, run=None):
    script = destination_script(destination, wallet.network)
    if amount < DUST:
        raise ValueError("Минимальная сумма — 0,01 PEP.")
    selected = []
    total = 0
    for coin in sorted(snapshot.utxos, key=lambda u: u.value, reverse=True):
        selected.append(coin)
        total += coin.value
        # Core's recommended 0.01 PEP/kB, using a conservative P2PKH size.
        fee = estimated_fee(len(selected), fee_rate, wallet.keys.input_bytes() if wallet.keys else 149)
        if total >= amount + fee:
            break
    else:
        raise ValueError("Недостаточно подтверждённых PEP с учётом комиссии.")
    inputs, keypairs = [], {}
    parents = []
    for coin in selected:
        if wallet.network == "mainnet":
            raw = mainnet_electrum.raw_transaction(coin.txid, run)
        else:
            raw = json.loads(api("/api/v2/tx/" + coin.txid))["hex"]
        parent = Transaction(raw)
        if parent.txid() != coin.txid or not 0 <= coin.index < len(parent.outputs()):
            raise ValueError("Предыдущая транзакция не прошла проверку.")
        output = parent.outputs()[coin.index]
        expected = destination_script(wallet.address(coin.branch, coin.child), wallet.network)
        if output.value != coin.value or output.scriptpubkey != expected:
            raise ValueError("Сумма или владелец входа не совпали.")
        if wallet.advanced:
            from embit.transaction import Transaction as LegacyTransaction
            parents.append(LegacyTransaction.from_string(raw))
            continue
        key = wallet.account.subkey_at_private_derivation([coin.branch, coin.child]).eckey
        pub = key.get_public_key_bytes(compressed=True)
        txin = PartialTxInput(prevout=TxOutpoint(bytes.fromhex(coin.txid), coin.index), nsequence=0xffffffff)
        txin.utxo = parent
        txin.script_descriptor = get_singlesig_descriptor_from_legacy_leaf(pubkey=pub.hex(), script_type="p2pkh")
        inputs.append(txin)
        keypairs[pub] = key.get_secret_bytes()
    change = total - amount - fee
    if wallet.advanced:
        from pep_psbt import proposal
        return proposal(wallet, selected, parents, destination, amount,
                        change if change >= DUST else 0, snapshot.next_indices[1])
    outputs = [PartialTxOutput(scriptpubkey=script, value=amount)]
    if change >= DUST:
        change_addr = wallet.address(1, snapshot.next_indices[1])
        outputs.append(PartialTxOutput(scriptpubkey=destination_script(change_addr, wallet.network), value=change))
    else:
        fee += change
    tx = PartialTransaction.from_io(inputs, outputs, version=1, locktime=0, BIP69_sort=False)
    tx.sign(keypairs)
    if not tx.is_complete():
        raise ValueError("Не удалось подписать все входы.")
    raw = tx.serialize_to_network()
    return {"network": wallet.network, "txid": Transaction(raw).txid(), "hex": raw, "address": destination, "amount": amount, "fee": fee}


def seen(payment, *, run=None):
    if payment.get("network") == "mainnet":
        return (run or mainnet_electrum.connected)(lambda c: mainnet_electrum.is_seen(c, payment))
    try:
        tx = json.loads(api("/api/v2/tx/" + payment["txid"]))
    except HTTPError as e:
        if e.code in (400, 404):
            return False
        raise
    return tx.get("hex") == payment["hex"] and tx.get("txid") == payment["txid"]


def broadcast(payment, network="testnet", *, run=None):
    if network != payment.get("network", "testnet"):
        raise ValueError("Сеть перевода отличается от сети кошелька.")
    if network == "mainnet":
        if Transaction(payment["hex"]).txid() != payment["txid"]:
            raise ValueError("TXID не соответствует подписанной транзакции.")
        return (run or mainnet_electrum.connected)(lambda c: mainnet_electrum.broadcast(c, payment))
    check_network()
    if seen(payment):
        return
    from check_electrum_testnet import local_relay
    with local_relay([]) as endpoint, socket.create_connection(endpoint, timeout=180) as sock:
        with sock.makefile("rb") as stream:
            method = "blockchain.transaction.broadcast"
            sock.sendall(json.dumps(dict(id=1, method=method, params=[payment["hex"]])).encode() + b"\n")
            reply = json.loads(stream.readline(65536))
    if reply.get("id") != 1 or reply.get("result") != payment["txid"]:
        if not seen(payment):
            raise ValueError("Узел не подтвердил передачу. Можно повторить тот же перевод.")

