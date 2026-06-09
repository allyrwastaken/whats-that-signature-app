; Inno Setup script for What's That Signature.
; Compile with:  ISCC.exe installer\WhatsThatSignature.iss
; Produces installer\Output\WhatsThatSignature-Setup-<version>.exe

#define MyAppName "What's That Signature"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "allyrwastaken"
#define MyAppURL "https://github.com/allyrwastaken/whats-that-signature-app"
#define MyAppExeName "WhatsThatSignature.exe"

[Setup]
; Same AppId as the old "Signature Overlay" so existing installs upgrade in
; place (the [InstallDelete] below cleans up the old-named files/shortcuts).
AppId={{A7F3C2E1-9B4D-4E6A-8C12-5F0D3B7A2E91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Streamlined wizard (keeps in-app updates to a couple of clicks).
DisableWelcomePage=yes
DisableReadyPage=yes
DisableDirPage=auto
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=WhatsThatSignature-Setup-{#MyAppVersion}
SetupIconFile=..\assets\signature_overlay.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The bundled app is 64-bit; installer writes to the 64-bit Program Files.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installing into Program Files needs admin (the app self-elevates separately).
PrivilegesRequired=admin
; For in-app updates: close the running app (Restart Manager) before replacing
; files; the [Run] entry below relaunches it afterwards.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Remove leftovers from the old "Signature Overlay" branding on upgrade.
Type: files; Name: "{app}\SignatureOverlay.exe"
Type: filesandordirs; Name: "{autoprograms}\Signature Overlay"
Type: files; Name: "{autodesktop}\Signature Overlay.lnk"

[Files]
Source: "..\dist\WhatsThatSignature\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; No skipifsilent: this also runs after a silent in-app update, relaunching
; the app automatically.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall
