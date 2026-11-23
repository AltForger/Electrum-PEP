# -*- mode: python ; coding: utf-8 -*-
import os
import runpy
import sys
from pathlib import Path

# Keep unrelated programs' ICU and OpenSSL DLLs out of the frozen application.
qt_bin = Path(sys.prefix) / 'Lib/site-packages/PyQt6/Qt6/bin'
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join(map(str, (
    qt_bin, Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs',
    windows / 'System32', windows,
)))

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32 import versioninfo as vi

version = runpy.run_path(str(Path(SPECPATH) / 'pep_version.py'))['VERSION']
win_version = tuple(map(int, version.split('.'))) + (0,)
portable_name = f'Electrum-PEP-{version}-win64-portable'

def file_info(filename, description):
    fields = {
        'ProductName': 'Electrum-PEP', 'CompanyName': 'AltForge',
        'FileDescription': description, 'OriginalFilename': filename,
        'InternalName': Path(filename).stem, 'ProductVersion': version,
        'FileVersion': '.'.join(map(str, win_version)),
    }
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=win_version, prodvers=win_version),
        kids=[
            vi.StringFileInfo([vi.StringTable('040904B0', [vi.StringStruct(k, v) for k, v in fields.items()])]),
            vi.VarFileInfo([vi.VarStruct('Translation', [1033, 1200])]),
        ],
    )

datas = [('licenses', 'licenses'), ('LICENSE', 'licenses/project'), ('DEPENDENCIES.md', 'licenses'), ('assets/pepecoin.png', 'assets'), ('assets/startup-notice.json', 'assets')]
datas += collect_data_files('electrum')
datas += [('assets/languages', 'assets/languages')]
datas += collect_data_files('embit')
datas += collect_data_files('certifi')
datas += [('.venv/Lib/site-packages/PyQt6/Qt6/translations/qtbase_*.qm', 'PyQt6/Qt6/translations')]


a = Analysis(
    ['pep_qt.py', 'pep_portable.py'],
    pathex=[],
    binaries=[('.venv/Lib/site-packages/electrum_ecc/libsecp256k1-6.dll', 'electrum_ecc'), ('.venv/Lib/site-packages/embit/util/prebuilt/libsecp256k1_windows_amd64.dll', 'embit/util/prebuilt')],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['check_electrum_testnet', 'testnet_peer', 'PyQt5', 'PySide6', 'tkinter', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtQuick', 'PyQt6.QtQml', 'PyQt6.QtMultimedia'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    [entry for entry in a.scripts if entry[0] != 'pep_portable'],
    a.binaries,
    a.datas,
    [],
    name='Electrum-PEP',
    version=file_info('Electrum-PEP.exe', 'Electrum-PEP wallet for Pepecoin'),
    icon='.venv/Lib/site-packages/electrum/gui/icons/electrum.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

portable = EXE(
    pyz, [entry for entry in a.scripts if entry[0] != 'pep_qt'],
    a.binaries, a.datas, [], name=portable_name,
    version=file_info(portable_name + '.exe', 'Electrum-PEP portable wallet for Pepecoin'),
    icon='.venv/Lib/site-packages/electrum/gui/icons/electrum.ico',
    console=False, upx=True,
)


