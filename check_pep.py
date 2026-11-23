"""Check recorded Pepecoin block samples offline or compare them with two TLS servers."""

import argparse
import json
from pathlib import Path
import socket
import ssl

from pep_headers import next_mainnet_bits, verify_header


SERVERS = ("electrum.pepecoinservice.org", "electrum.pepe.tips")
SAMPLES = Path(__file__).with_name("mainnet_samples.json")


def fetch_headers(host, heights):
    with socket.create_connection((host, 50002), timeout=10) as tcp:
        with ssl.create_default_context().wrap_socket(tcp, server_hostname=host) as sock:
            with sock.makefile("rb") as stream:
                def call(request_id, method, params):
                    request = {"id": request_id, "method": method, "params": params}
                    sock.sendall((json.dumps(request) + "\n").encode("ascii"))
                    line = stream.readline(8 * 1024 * 1024 + 1)
                    if not line.endswith(b"\n") or len(line) > 8 * 1024 * 1024:
                        raise ValueError("incomplete or oversized Electrum response")
                    reply = json.loads(line)
                    if reply.get("id") != request_id or reply.get("error"):
                        raise ValueError(f"unexpected Electrum response for {method}")
                    return reply["result"]

                call(1, "server.version", ["Electrum-PEP header prototype", "1.4"])
                results = {}
                for request_id, height in enumerate(sorted(set(heights) | {0}), start=2):
                    raw = bytes.fromhex(call(request_id, "blockchain.block.header", [height]))
                    results[height] = raw
                    if height == 0:
                        verify_header(raw, 0)
                return results


def check_samples(samples, headers):
    previous_height, previous_id = -2, None
    for entry in samples:
        height = entry["height"]
        raw = headers[height]
        block_id = verify_header(raw, height)
        if block_id != entry["block_id"]:
            raise ValueError(f"block differs from reference at {height}")
        if height == previous_height + 1 and raw[4:36][::-1].hex() != previous_id:
            raise ValueError(f"broken link at {height}")
        if height >= 2 and height - 2 in headers and height - 1 in headers:
            expected = next_mainnet_bits(headers[height - 2][:80], headers[height - 1][:80])
            if int.from_bytes(raw[72:76], "little") != expected:
                raise ValueError(f"unexpected difficulty at {height}")
        previous_height, previous_id = height, block_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also query both public Electrum servers")
    args = parser.parse_args()
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    local = {item["height"]: bytes.fromhex(item["header"]) for item in samples}
    check_samples(samples, local)
    print(f"Offline: verified {len(samples)} mainnet sample headers and adjacent links.")
    if args.live:
        for host in SERVERS:
            check_samples(samples, fetch_headers(host, local))
            print(f"{host}: verified {len(samples)} matching mainnet sample blocks.")


if __name__ == "__main__":
    main()
