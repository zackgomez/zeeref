; ZeeRef Inno Setup installer script
; Produces a single ZeeRef-Setup.exe that installs the PyInstaller-built exe,
; registers .zref file association, and creates Start Menu shortcuts.
;
; Usage:
;   1. Build the exe:  pyinstaller ZeeRef.spec
;   2. Build installer: iscc installer.iss
;
; The installer exe lands in dist/ZeeRef-Setup.exe

#define AppName "ZeeRef"
#define AppExeName "ZeeRef-*.exe"
; Read version from constants.py at compile time
#define ConstantsFile "zeeref\constants.py"
#define VersionLine Local[0] = Copy(GetStringFileInfo(AddBackslash(SourcePath) + "dist\" + AppExeName, "ProductVersion"), 1)
; Fallback: set version manually if the above doesn't work with single-file exe
#define AppVersion "0.3.4-dev"
#define AppPublisher "Zack Gomez"
#define AppURL "https://github.com/zackgomez/zeeref"

[Setup]
AppId={{E8A3F2B1-7C45-4D89-9B6E-2F1A3C5D7E90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ZeeRef-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Per-user install by default (no admin required)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=zeeref\assets\logo.ico
UninstallDisplayIcon={app}\ZeeRef.exe
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; The PyInstaller single-file exe. Wildcard handles the version in the filename.
; Rename to ZeeRef.exe on install for a clean path.
Source: "dist\ZeeRef-{#AppVersion}.exe"; DestDir: "{app}"; DestName: "ZeeRef.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ZeeRef.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ZeeRef.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "fileassoc"; Description: "Associate .zref files with {#AppName}"; GroupDescription: "File associations:"; Flags: checkedonce

[Registry]
; File association: .zref → ZeeRef.Document
Root: HKA; Subkey: "Software\Classes\.zref"; ValueType: string; ValueName: ""; ValueData: "ZeeRef.Document"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ZeeRef.Document"; ValueType: string; ValueName: ""; ValueData: "ZeeRef Scene"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ZeeRef.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ZeeRef.exe,0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ZeeRef.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ZeeRef.exe"" ""%1"""; Tasks: fileassoc
