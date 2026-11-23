"""Pepecoin mainnet header proofs. Block membership requires a trusted chain anchor."""

import hashlib
import struct


GENESIS = "37981c0c48b8d48965376c8a42ece9a0838daadb93ff975cb091f57f8c2a5faa"
CHAIN_ID = 63
AUX_START = 42000
POW_LIMIT = (1 << 236) - 1
MERGED_MINING = bytes.fromhex("fabe6d6d")


def hash256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        end = self.pos + size
        if end > len(self.data):
            raise ValueError("truncated proof")
        result = self.data[self.pos:end]
        self.pos = end
        return result

    def uint(self, size):
        return int.from_bytes(self.take(size), "little")

    def compact(self):
        marker = self.uint(1)
        if marker < 253:
            return marker
        size, minimum = {253: (2, 253), 254: (4, 65536), 255: (8, 1 << 32)}[marker]
        value = self.uint(size)
        if value < minimum:
            raise ValueError("noncanonical CompactSize")
        return value


def parent_transaction(reader):
    start = reader.pos
    version = reader.take(4)
    inputs_start = reader.pos
    count = reader.compact()
    witness = count == 0
    if witness:
        if reader.uint(1) != 1:
            raise ValueError("invalid parent transaction flags")
        inputs_start = reader.pos
        count = reader.compact()
    if not count:
        raise ValueError("no parent transaction inputs")
    for index in range(count):
        reader.take(36)
        script = reader.take(reader.compact())
        if index == 0:
            commitment_script = script
        reader.take(4)
    for _ in range(reader.compact()):
        reader.take(8)
        reader.take(reader.compact())
    outputs_end = reader.pos
    if witness:
        has_stack = False
        for _ in range(count):
            items = reader.compact()
            has_stack |= bool(items)
            for _ in range(items):
                reader.take(reader.compact())
        if not has_stack:
            raise ValueError("empty witness serialization")
    locktime = reader.take(4)
    if witness:
        tx = version + reader.data[inputs_start:outputs_end] + locktime
    else:
        tx = reader.data[start:reader.pos]
    return hash256(tx), commitment_script


def merkle_branch(reader, leaf, *, chain):
    count = reader.compact()
    if chain and count > 30:
        raise ValueError("chain merkle branch too long")
    siblings = reader.take(32 * count)
    index = reader.uint(4)
    if (not chain and index != 0) or (chain and index >= 1 << count):
        raise ValueError("invalid merkle index")
    for level in range(count):
        sibling = siblings[32 * level:32 * (level + 1)]
        leaf = hash256(sibling + leaf if (index >> level) & 1 else leaf + sibling)
    return leaf, count, index


def check_commitment(script, root, depth, index):
    pos = script.find(root[::-1])
    marker = script.find(MERGED_MINING)
    if pos < 0 or len(script) < pos + 40:
        raise ValueError("missing chain commitment")
    if marker >= 0:
        if marker + 4 != pos or script.find(MERGED_MINING, marker + 1) >= 0:
            raise ValueError("invalid merge-mining marker")
    elif pos > 20:
        raise ValueError("misplaced legacy commitment")
    size, nonce = struct.unpack_from("<II", script, pos + 32)
    if size != 1 << depth:
        raise ValueError("incorrect chain tree size")
    slot = 1103515245 * nonce + 12345 + CHAIN_ID
    if (1103515245 * slot + 12345) % size != index:
        raise ValueError("incorrect chain tree slot")


def target_from_bits(bits):
    exponent, mantissa = bits >> 24, bits & 0x007fffff
    shift = 8 * (exponent - 3)
    target = mantissa << shift if shift >= 0 else mantissa >> -shift
    if bits & 0x00800000 or not 0 < target <= POW_LIMIT:
        raise ValueError("invalid target")
    return target


def next_mainnet_bits(older, previous):
    """Mainnet DigiShield interval is one block. Both inputs are 80-byte headers."""
    older_time = int.from_bytes(older[68:72], "little")
    previous_time, bits = struct.unpack_from("<II", previous, 68)
    deviation = previous_time - older_time - 60
    adjustment = abs(deviation) // 8
    if deviation < 0:
        adjustment = -adjustment  # Core uses integer division toward zero.
    timespan = max(45, min(90, 60 + adjustment))
    target = min(POW_LIMIT, target_from_bits(bits) * timespan // 60)
    size = (target.bit_length() + 7) // 8
    shift = 8 * (size - 3)
    compact = target >> shift if shift >= 0 else target << -shift
    if compact & 0x00800000:
        compact >>= 8
        size += 1
    return (size << 24) | compact


def verify_header(raw, height):
    """Check a single header against its declared target and mainnet proof rules."""
    if height < 0:
        raise ValueError("negative height")
    reader = Reader(raw)
    child = reader.take(80)
    version = int.from_bytes(child[:4], "little")
    auxpow = bool(version & 256)
    legacy = version in (1, 2)
    if not legacy and version >> 16 != CHAIN_ID:
        raise ValueError("wrong Pepecoin chain ID")
    if (height < AUX_START and auxpow) or (height >= AUX_START and legacy):
        raise ValueError("invalid version for height")
    digest = hash256(child)
    block_id = digest[::-1].hex()
    if height == 0 and block_id != GENESIS:
        raise ValueError("wrong Pepecoin genesis")
    target = target_from_bits(int.from_bytes(child[72:76], "little"))
    work_header = child
    if auxpow:
        txid, script = parent_transaction(reader)
        reader.take(32)  # Historical hashBlock field, unused by Pepecoin Core.
        parent_root, _, _ = merkle_branch(reader, txid, chain=False)
        chain_root, depth, index = merkle_branch(reader, digest, chain=True)
        work_header = reader.take(80)
        if int.from_bytes(work_header[:4], "little") >> 16 == CHAIN_ID:
            raise ValueError("parent has Pepecoin chain ID")
        if work_header[36:68] != parent_root:
            raise ValueError("parent merkle root mismatch")
        check_commitment(script, chain_root, depth, index)
    if reader.pos != len(raw):
        raise ValueError("unexpected trailing data")
    work = hashlib.scrypt(work_header, salt=work_header, n=1024, r=1, p=1, dklen=32)
    if int.from_bytes(work, "little") > target:
        raise ValueError("insufficient scrypt work")
    return block_id
