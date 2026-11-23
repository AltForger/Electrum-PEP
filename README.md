# Electrum-PEP

Electrum-PEP is an independent Windows desktop wallet for Pepecoin (PEP) mainnet. It uses Python, Qt and components from the Electrum Bitcoin wallet. It connects to Pepecoin Electrum servers over TLS. The server supplies history and confirmations. The wallet checks the network genesis and does not independently validate the full header chain for each transaction.

## Windows downloads

Choose a file from [GitHub Releases](https://github.com/AltForger/Electrum-PEP/releases).

- **Setup** installs the wallet for the current Windows user. Wallet data lives in `%LOCALAPPDATA%\Electrum-PEP`.
- **Portable** runs directly from a writable folder. Keep the executable beside its existing `Electrum-PEP-data` folder when updating.

This is a pre-release for Windows x64. The current Windows downloads are unsigned. Compare the SHA-256 digest of your downloaded file with its entry in `SHA256SUMS.txt` from the same release. In PowerShell, run `Get-FileHash .\filename.exe -Algorithm SHA256` with the name you downloaded.

Create a wallet or restore an existing one in the startup wizard. Back up the recovery information for your wallet type before receiving funds. Use **Network** to inspect or select an Electrum server.

## Build the Windows release

Use Windows x64, 64-bit Python 3.13, a MinGW-w64 x86-64 GCC toolchain and NSIS 3 with the Russian and English Modern UI language files. Put GCC and makensis on PATH or pass their full paths to the build script.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-win64.lock
.\build-windows.ps1
```

The build fetches the pinned libsecp256k1 sources and verifies their SHA-256 before compiling. It runs the offline tests, builds both wallet binaries and packages the installer in the versioned directory under `dist`. The version used by Qt, the network client, Windows file properties and the installer is defined in `pep_version.py`.

## Licenses and sources

The AltForge application code is licensed under the [MIT License](LICENSE). The Windows distribution includes GPLv3 PyQt6 and other third-party components. The combined distribution is provided under GPLv3, with each component retaining its own license. See [dependency sources and versions](DEPENDENCIES.md) and the [license texts](licenses/).
