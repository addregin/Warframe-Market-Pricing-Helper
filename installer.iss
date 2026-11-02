; Inno Setup script - works with Inno Setup 6 and Inno Setup 7 (free: https://jrsoftware.org/isdl.php)
; Builds "installer\Warframe Market Prices Setup.exe", which installs the app, adds a Start menu
; entry, and registers it in Windows Settings > Apps > Installed apps as "Warframe Market Prices".
; Run build_exe.bat first (it creates dist\Warframe Market Prices.exe), or let build_exe.bat
; compile this for you when Inno Setup is installed.
; (Keep this file plain ASCII: Inno Setup 7 refuses files with characters its code page cannot read.)

#define MyAppName "Warframe Market Prices"
#define MyAppVersion "1.3.0"
#define MyAppExe "Warframe Market Prices.exe"

[Setup]
AppId={{B7F1B4A2-6C0E-4E57-9B2B-3F4F6D1A8E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; the app is 64-bit only (PySide6)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; installs for the current user only, so no administrator prompt
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=Warframe Market Prices Setup
SetupIconFile=wfm_icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; offers to close the running app so an update can replace it
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[InstallDelete]
; the old file name from earlier versions
Type: files; Name: "{app}\WarframeMarketPrices.exe"

[Files]
Source: "dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; the picture cache and logs (your saved settings live in the registry)
Type: filesandordirs; Name: "{%USERPROFILE}\.wfm_price_checker"
