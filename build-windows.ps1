param(
    [string]$Compiler = 'gcc',
    [string]$MakeNSIS = 'makensis'
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$version = & $python -c 'from pep_version import VERSION; print(VERSION)'
if ($LASTEXITCODE -ne 0) { throw 'Set up the Python environment first.' }
$target = & $Compiler -dumpmachine
if ($target -ne 'x86_64-w64-mingw32') { throw 'A Windows x86-64 MinGW compiler is required.' }
$native = Join-Path $PSScriptRoot 'build/native'
New-Item -ItemType Directory -Force $native | Out-Null
$archive = Join-Path $native 'secp256k1-v0.7.0.zip'
if (!(Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest 'https://codeload.github.com/bitcoin-core/secp256k1/zip/refs/tags/v0.7.0' -OutFile $archive
}
if ((Get-FileHash $archive).Hash -ne '236BB4799925716A5FA9499D31E8C4A2A708B95799ABCDED59FA9354538EBC32') {
    throw 'Unexpected secp256k1 source digest.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $native -Force
$source = Join-Path $native 'secp256k1-0.7.0/src'
$dll = Join-Path $PSScriptRoot '.venv/Lib/site-packages/electrum_ecc/libsecp256k1-6.dll'
& $Compiler -O2 -shared -static-libgcc '-Wl,--no-insert-timestamp' -DENABLE_MODULE_RECOVERY -DENABLE_MODULE_ECDH -DENABLE_MODULE_EXTRAKEYS -DENABLE_MODULE_SCHNORRSIG -o $dll "$source/secp256k1.c" "$source/precomputed_ecmult.c" "$source/precomputed_ecmult_gen.c"
if ($LASTEXITCODE -ne 0) { throw 'Native library compilation failed.' }
& $python -m unittest discover -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $python -m PyInstaller --noconfirm --distpath "dist/$version" Electrum-PEP.spec
if ($LASTEXITCODE -ne 0) { throw 'Wallet build failed.' }
& $MakeNSIS /V2 Electrum-PEP-Setup.nsi
if ($LASTEXITCODE -ne 0) { throw 'Setup build failed.' }
