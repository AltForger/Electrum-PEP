import unittest

from embit.transaction import Transaction, TransactionOutput
from pep_wallet import address, create_phrase, key_path, payment_script, restore, sign_payment


class WalletTests(unittest.TestCase):
    # Public BIP39 test mnemonic. Addresses cross-checked with an independent
    # HMAC-SHA512 / OpenSSL secp256k1 / Base58Check implementation.
    words = "abandon " * 11 + "about"

    def test_address_vectors(self):
        root = restore(self.words)
        vectors = [
            ("mainnet", 0, False, "PsGyoVVSD9VTYoDZGp5FmRB2BSoE69f9ZV"),
            ("mainnet", 1, False, "PpDMPUwck51qReGJLGZ6kwKrw3nuptQ3kc"),
            ("mainnet", 0, True, "PrrvgrDFxNvfCg4tJnBQQ5mzV6Cu3LcUVs"),
            ("testnet", 0, False, "nZVmfmUtKPmskB9Ds4P9GUJy9eYFqPKHqH"),
            ("regtest", 0, False, "mkpZhYtJu2r87Js3pDiWJDmPte2NRZ8bJV"),
        ]
        for network, index, change, expected in vectors:
            with self.subTest(network=network, index=index, change=change):
                self.assertEqual(address(root, network, index, change), expected)

    def test_fresh_phrase_and_restoration(self):
        first, second = create_phrase(), create_phrase()
        self.assertEqual(len(first.split()), 12)
        self.assertNotEqual(first, second)
        self.assertEqual(address(restore(first), "mainnet"),
                         address(restore("  ".join(first.split())), "mainnet"))

    def test_passphrase_normalization(self):
        a = address(restore(self.words, "caf\u00e9"), "mainnet")
        b = address(restore(self.words, "cafe\u0301"), "mainnet")
        self.assertEqual(a, b)
        self.assertNotEqual(a, address(restore(self.words), "mainnet"))

    def test_invalid_checksum_and_index(self):
        with self.assertRaises(ValueError):
            restore("abandon " * 12)
        for index in (-1, 2**31):
            with self.assertRaises(ValueError):
                key_path("mainnet", index)

    def test_signing_checks(self):
        root = restore(self.words)
        sender = address(root, "regtest")
        utxo = Transaction(vout=[TransactionOutput(10000, payment_script(sender, "regtest"))])
        with self.assertRaisesRegex(ValueError, "selected network"):
            sign_payment(root, "regtest", utxo, 0, address(root, "mainnet"), 500, 20)
        with self.assertRaisesRegex(ValueError, "Insufficient funds"):
            sign_payment(root, "regtest", utxo, 0, sender, 10000, 20)
        with self.assertRaisesRegex(ValueError, "selected key"):
            sign_payment(root, "regtest", utxo, 0, sender, 500, 20, receive_index=1)
        tx = sign_payment(root, "regtest", utxo, 0, sender, 9980, 20)
        self.assertEqual([o.value for o in tx.vout], [9980])


if __name__ == "__main__":
    unittest.main()
