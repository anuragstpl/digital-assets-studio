; Inno Setup script for the Windows installer.
; Built by .github/workflows/release.yml; to build locally run packaging\build_windows.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Digital Assets Studio"
#define AppPublisher "AIpath"
#define AppURL "https://github.com/aidiginext/digital-assets-studio"
#define AppExeName "DigitalAssetsStudio.exe"

[Setup]
AppId={{7B2F5C41-9E3A-4D8B-A6C2-1F0E5D9A4B37}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=DigitalAssetsStudio-{#AppVersion}-windows-setup
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; per-user install by default, so no admin prompt is needed
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\DigitalAssetsStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; ffmpeg is optional, so say what it unlocks rather than blocking the install
FinishedLabel=Setup has installed {#AppName}.%n%nVideo and audio steps need ffmpeg. If you do not have it:%n    winget install Gyan.FFmpeg%n%nOpen Settings inside the app to add your model and publishing keys.
