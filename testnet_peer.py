"""Fetch identified testnet blocks and relay signed transactions over Pepecoin P2P."""

from contextlib import ExitStack
from io import BytesIO
import secrets
import socket
import struct
import time

from embit.transaction import Transaction
from pep_headers import Reader, hash256, parent_transaction


MAGIC = bytes.fromhex("fec1dbcc")
GENESIS = "f9f4ea4ae7f6ea4c55040ede2019ba0a53e262f46ec9bce3dcda2cb11f96fc52"
SEEDS = ("seeds-testnet.pepecoin.org", "seeds-testnet.pepeblocks.com")
PORT = 44874


def peers():
    hosts = []
    for seed in SEEDS:
        try:
            records = socket.getaddrinfo(seed, PORT, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        for record in records:
            host = record[4][0]
            if host not in hosts:
                hosts.append(host)
    return hosts


class Peer(ExitStack):
    def __init__(self, host):
        super().__init__()
        self.host = host

    def __enter__(self):
        try:
            self.sock = self.enter_context(socket.create_connection((self.host, PORT), timeout=7))
            self.sock.settimeout(20)
            self.stream = self.enter_context(self.sock.makefile("rb"))
            endpoint = bytes(18) + b"\xff\xff" + socket.inet_aton(self.host) + struct.pack(">H", PORT)
            agent = b"/Electrum-PEP-test:0.1/"
            version = (struct.pack("<iQq", 70015, 0, int(time.time())) + endpoint + bytes(26)
                       + secrets.token_bytes(8) + bytes([len(agent)]) + agent + struct.pack("<i?", 0, True))
            self.send("version", version)
            self.wait_for("version")
            self.send("verack")
            self.wait_for("verack")
            return self
        except BaseException:
            self.close()
            raise

    def send(self, command, data=b""):
        header = MAGIC + command.encode().ljust(12, b"\0") + struct.pack("<I", len(data))
        self.sock.sendall(header + hash256(data)[:4] + data)

    def wait_for(self, command):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            header = self.stream.read(24)
            if len(header) != 24 or header[:4] != MAGIC:
                raise ConnectionError("invalid testnet message or disconnect")
            name = header[4:16].rstrip(b"\0").decode("ascii")
            size = int.from_bytes(header[16:20], "little")
            if size > 8_000_000:
                raise ValueError("unexpected message size")
            payload = self.stream.read(size)
            if len(payload) != size or hash256(payload)[:4] != header[20:24]:
                raise ValueError("invalid P2P message")
            if name == command:
                return payload
            if name == "ping":
                self.send("pong", payload)
            if name in ("reject", "notfound"):
                raise ValueError(f"peer returned {name}")
        raise TimeoutError(command)

    def block(self, block_hash):
        self.send("getdata", b"\x01\x02\x00\x00\x00" + bytes.fromhex(block_hash)[::-1])
        block = self.wait_for("block")
        if len(block) < 81 or hash256(block[:80])[::-1].hex() != block_hash:
            raise ValueError("unexpected block")
        return block

    def relay(self, raw):
        tx = Transaction.parse(raw)
        inv_hash = tx.txid()[::-1]
        self.send("inv", b"\x01\x01\x00\x00\x00" + inv_hash)
        request = Reader(self.wait_for("getdata"))
        entries = [(request.uint(4), request.take(32)) for _ in range(request.compact())]
        if not any(kind & 0x3fffffff == 1 and h == inv_hash for kind, h in entries):
            raise ValueError("peer did not request the announced transaction")
        self.send("tx", raw)
        token = secrets.token_bytes(8)
        self.send("ping", token)
        if self.wait_for("pong") != token:
            raise ValueError("peer did not acknowledge the exchange")
        return tx.txid().hex()


def block_transactions(block):
    reader = Reader(block)
    header = reader.take(80)
    if int.from_bytes(header[:4], "little") & 256:
        parent_transaction(reader)
        reader.take(32)
        for _ in range(2):
            reader.take(32 * reader.compact() + 4)
        reader.take(80)
    count = reader.compact()
    if not 0 < count <= (len(block) - reader.pos) // 10:
        raise ValueError("invalid block transaction count")
    stream = BytesIO(block[reader.pos:])
    transactions = [Transaction.read_from(stream) for _ in range(count)]
    hashes = [tx.txid()[::-1] for tx in transactions]
    while len(hashes) > 1:
        if len(hashes) % 2:
            hashes.append(hashes[-1])
        hashes = [hash256(a + b) for a, b in zip(hashes[::2], hashes[1::2])]
    if hashes[0] != header[36:68] or stream.read(1):
        raise ValueError("block merkle root or serialization mismatch")
    return transactions
