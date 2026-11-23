import json
import unittest

from check_pep import SAMPLES, check_samples
from pep_headers import AUX_START, verify_header


class HeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
        cls.headers = {s["height"]: bytes.fromhex(s["header"]) for s in cls.samples}

    def test_real_samples_links_and_difficulty(self):
        check_samples(self.samples, self.headers)

    def test_activation_allows_ordinary_work(self):
        self.assertEqual(len(self.headers[AUX_START]), 80)
        verify_header(self.headers[AUX_START], AUX_START)

    def test_auxpow_preactivation(self):
        with self.assertRaisesRegex(ValueError, "version for height"):
            verify_header(self.headers[43000], AUX_START - 1)

    def test_legacy_after_activation(self):
        with self.assertRaisesRegex(ValueError, "version for height"):
            verify_header(self.headers[0], AUX_START)

    def test_corrupt_child_commitment(self):
        header = bytearray(self.headers[43000])
        header[36] ^= 1  # Child merkle root. Parent PoW is still intact.
        with self.assertRaisesRegex(ValueError, "chain commitment"):
            verify_header(header, 43000)

    def test_corrupt_parent_transaction_proof(self):
        header = bytearray(self.headers[43000])
        header[-80 + 36] ^= 1  # Parent header no longer commits to the coinbase.
        with self.assertRaisesRegex(ValueError, "parent merkle root"):
            verify_header(header, 43000)

    def test_corrupt_work(self):
        for height in (42000, 43000):
            with self.subTest(height=height):
                header = bytearray(self.headers[height])
                header[-1] ^= 1  # Nonce of whichever header carries the work.
                with self.assertRaisesRegex(ValueError, "insufficient scrypt work"):
                    verify_header(header, height)

    def test_wrong_network(self):
        header = bytearray(self.headers[43000])
        header[2] ^= 1
        with self.assertRaisesRegex(ValueError, "wrong Pepecoin chain ID"):
            verify_header(header, 43000)

    def test_wrong_genesis(self):
        with self.assertRaisesRegex(ValueError, "wrong Pepecoin genesis"):
            verify_header(self.headers[1], 0)

    def test_truncated_and_trailing(self):
        for height in (42000, 43000):
            for raw in (self.headers[height][:-1], self.headers[height] + b"\x00"):
                with self.subTest(height=height, size=len(raw)), self.assertRaises(ValueError):
                    verify_header(raw, height)


if __name__ == "__main__":
    unittest.main()
