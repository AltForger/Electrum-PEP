"""Run two wallet RPC methods against a loopback Electrum-to-Pepecoin P2P relay.

Only blocks named on the command line are indexed. The wallet sends ordinary
newline-delimited Electrum requests. The relay has no keys and no HTTP backend.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import socketserver
from threading import Thread

from embit.transaction import Transaction
from testnet_peer import GENESIS, Peer, block_transactions, peers


class TestnetRelay:
    def __init__(self, block_hashes):
        self.block_hashes = block_hashes
        self.hosts = peers()
        self.transactions = {}

    def with_peer(self, action):
        failures = []
        for host in self.hosts:
            try:
                with Peer(host) as peer:
                    peer.block(GENESIS)
                    value = action(peer)
                    return value, host
            except (OSError, ValueError) as exc:
                failures.append(f"{host}: {exc}")
        raise ValueError("No testnet peer completed request: " + ", ".join(failures))

    def handle(self, method, params):
        if method == "server.version" and len(params) == 2 and params[1] == "1.4":
            return ["Electrum-PEP test relay", "1.4"]
        if method == "blockchain.block.header" and params == [0]:
            header, _ = self.with_peer(lambda peer: peer.block(GENESIS)[:80])
            return header.hex()
        if method == "blockchain.transaction.get" and len(params) == 1:
            txid, = params
            if txid not in self.transactions:
                for block_hash in self.block_hashes:
                    block, host = self.with_peer(lambda peer: peer.block(block_hash))
                    for tx in block_transactions(block):
                        self.transactions[tx.txid().hex()] = tx.serialize().hex()
                    if txid in self.transactions:
                        print(f"Indexed {block_hash} from {host}", flush=True)
                        break
            if txid not in self.transactions:
                raise ValueError("Transaction not found in indexed blocks")
            return self.transactions[txid]
        if method == "blockchain.transaction.broadcast" and len(params) == 1:
            raw = bytes.fromhex(params[0])
            result, host = self.with_peer(lambda peer: peer.relay(raw))
            print(f"Relayed {result} to {host}", flush=True)
            return result
        raise ValueError("Unsupported test relay request")


@contextmanager
def local_relay(block_hashes):
    relay = TestnetRelay(block_hashes)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            while line := self.rfile.readline(2_000_001):
                if len(line) > 2_000_000 or not line.endswith(b"\n"):
                    return
                request = json.loads(line)
                response = {"jsonrpc": "2.0", "id": request["id"]}
                try:
                    response["result"] = relay.handle(request["method"], request["params"])
                except (ValueError, OSError) as exc:
                    response["error"] = {"code": 1, "message": str(exc)}
                self.wfile.write(json.dumps(response).encode() + b"\n")

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join()


def run(block_hashes, lookup, payment=None):
    from pep_headers import hash256

    with local_relay(block_hashes) as endpoint, socket.create_connection(endpoint, timeout=180) as sock:
        with sock.makefile("rb") as stream:
            def call(request_id, method, params):
                sock.sendall(json.dumps(dict(id=request_id, method=method, params=params)).encode() + b"\n")
                reply = json.loads(stream.readline(2_000_001))
                if reply["id"] != request_id or "error" in reply:
                    raise RuntimeError(str(reply))
                return reply["result"]

            call(1, "server.version", ["Electrum-PEP", "1.4"])
            genesis = bytes.fromhex(call(2, "blockchain.block.header", [0]))
            if hash256(genesis)[::-1].hex() != GENESIS:
                raise ValueError("Wrong Electrum network")
            raw = call(3, "blockchain.transaction.get", [lookup])
            if Transaction.from_string(raw).txid().hex() != lookup:
                raise ValueError("Lookup TXID mismatch")
            print("Electrum lookup verified:", lookup, flush=True)
            if payment:
                expected = Transaction.from_string(payment["hex"]).txid().hex()
                if expected != payment["txid"]:
                    raise ValueError("Signed payment TXID mismatch")
                result = call(4, "blockchain.transaction.broadcast", [payment["hex"]])
                if result != expected:
                    raise ValueError("Broadcast TXID mismatch")
                print("Electrum broadcast returned:", result, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", action="append", required=True)
    parser.add_argument("--lookup", required=True)
    parser.add_argument("--broadcast", type=Path, help="JSON file with a signed testnet payment")
    args = parser.parse_args()
    payment = json.loads(args.broadcast.read_text(encoding="utf-8")) if args.broadcast else None
    if payment and payment.get("network") != "PEP testnet":
        parser.error("Expected a PEP testnet payment")
    run(args.block, args.lookup, payment)
