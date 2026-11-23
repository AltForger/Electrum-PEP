"""BIP39 recovery, BIP44 P2PKH addresses and local legacy transaction signing."""

import argparse
from getpass import getpass
import secrets
import sys
import unicodedata

from embit import base58, bip32, bip39, script
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput


# Mainnet coin type from SLIP-0044. Test chains share coin type 1.
# Address version bytes from Pepecoin Core C{Main,TestNet,RegTest}Params.
NETWORKS = {"mainnet": (3434, 56), "testnet": (1, 113), "regtest": (1, 111)}
COIN = 100_000_000


def create_phrase():
    return bip39.mnemonic_from_bytes(secrets.token_bytes(16))


def restore(phrase, passphrase=""):
    words = " ".join(unicodedata.normalize("NFKD", phrase).split())
    if not bip39.mnemonic_is_valid(words):
        raise ValueError("Invalid BIP39 words or checksum")
    password = unicodedata.normalize("NFKD", passphrase)
    return bip32.HDKey.from_seed(bip39.mnemonic_to_seed(words, password))


def key_path(network, index=0, change=False):
    if not 0 <= index < 2**31:
        raise ValueError("Address index out of range")
    coin_type, _ = NETWORKS[network]
    return f"m/44'/{coin_type}'/0'/{int(change)}/{index}"


def address(root, network, index=0, change=False):
    key = root.derive(key_path(network, index, change))
    return script.p2pkh(key.get_public_key()).address({"p2pkh": bytes([NETWORKS[network][1]])})


def payment_script(destination, network):
    payload = base58.decode_check(destination)
    if len(payload) != 21 or payload[0] != NETWORKS[network][1]:
        raise ValueError("Expected a P2PKH address for the selected network")
    return script.Script(b"\x76\xa9\x14" + payload[1:] + b"\x88\xac")


def sign_payment(root, network, previous_tx, vout, destination, amount, fee,
                 receive_index=0, change_index=0, *, from_change=False):
    """Spend one owned P2PKH output. Amount and fee are integer base units."""
    if not isinstance(amount, int) or not isinstance(fee, int) or amount <= 0 or fee < 0:
        raise ValueError("Invalid amount or fee")
    if not 0 <= vout < len(previous_tx.vout):
        raise ValueError("Previous output does not exist")
    key = root.derive(key_path(network, receive_index, change=from_change)).key
    previous_output = previous_tx.vout[vout]
    if previous_output.script_pubkey.data != script.p2pkh(key.get_public_key()).data:
        raise ValueError("Previous output does not belong to the selected key")
    remainder = previous_output.value - amount - fee
    if remainder < 0:
        raise ValueError("Insufficient funds")
    outputs = [TransactionOutput(amount, payment_script(destination, network))]
    if remainder:
        change_address = address(root, network, change_index, change=True)
        outputs.append(TransactionOutput(remainder, payment_script(change_address, network)))
    tx = Transaction(version=1, vin=[TransactionInput(previous_tx.txid(), vout)], vout=outputs)
    digest = tx.sighash_legacy(0, previous_output.script_pubkey, SIGHASH.ALL)
    signature = key.sign(digest).serialize() + bytes([SIGHASH.ALL])
    tx.vin[0].script_sig.push(signature)
    tx.vin[0].script_sig.push(key.get_public_key().sec())
    return tx


def main():
    parser = argparse.ArgumentParser(description="Create or restore a Pepecoin BIP39 receiving address")
    parser.add_argument("action", choices=("create", "restore"))
    parser.add_argument("--network", choices=NETWORKS, default="mainnet")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--passphrase", action="store_true", help="prompt for an optional BIP39 passphrase")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("Open an interactive terminal to create or enter a recovery phrase")
    password = getpass("BIP39 passphrase: ") if args.passphrase else ""
    if args.action == "create" and args.passphrase and password != getpass("Repeat passphrase: "):
        parser.error("Passphrases do not match")
    words = create_phrase() if args.action == "create" else getpass("BIP39 words: ")
    try:
        derived = address(restore(words, password), args.network, args.index)
    except ValueError as exc:
        parser.error(str(exc))
    if args.action == "create":
        print("Recovery phrase (store privately):", words)
    print(args.network, key_path(args.network, args.index), derived)


if __name__ == "__main__":
    main()
