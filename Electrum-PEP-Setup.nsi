Unicode true
!include "MUI2.nsh"
!searchparse /file "pep_version.py" 'VERSION = "' VERSION '"'
!define SETUP_FILE "Electrum-PEP-${VERSION}-win64-setup.exe"
!define APP "Electrum-PEP"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Electrum-PEP"
Name "${APP}"
OutFile "dist\${VERSION}\${SETUP_FILE}"
InstallDir "$LOCALAPPDATA\Programs\Electrum-PEP"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor zlib
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Electrum-PEP"
VIAddVersionKey "FileDescription" "Electrum-PEP Setup"
VIAddVersionKey "FileVersion" "${VERSION}.0"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "AltForge"
VIAddVersionKey "OriginalFilename" "${SETUP_FILE}"
VIAddVersionKey "LegalCopyright" "Electrum-PEP contributors"

!define MUI_ICON "assets\pepecoin.ico"
!define MUI_UNICON "assets\pepecoin.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "assets\pepecoin-small.bmp"
!define MUI_HEADERIMAGE_BITMAP_STRETCH NoStretchNoCropNoAlign
!define MUI_FINISHPAGE_RUN "$INSTDIR\Electrum-PEP.exe"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

Section
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File "dist\${VERSION}\Electrum-PEP.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\Electrum-PEP.lnk" "$INSTDIR\Electrum-PEP.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Electrum-PEP.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\Electrum-PEP.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  Delete "$INSTDIR\Electrum-PEP.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
