#ifndef MyAppVersion
  #define MyAppVersion "8.0"
#endif

#define MyAppName "MixSplitR"
#define MyAppPublisher "MixSplitR"
#define MyAppExeName "MixSplitR.exe"
#define ProjectRoot ".."
#ifexist AddBackslash(ProjectRoot) + "dist\MixSplitR\MixSplitR.exe"
  #define MyAppDistDir AddBackslash(ProjectRoot) + "dist\\MixSplitR"
#else
  #ifexist AddBackslash(ProjectRoot) + "MixSplitR\MixSplitR.exe"
    #define MyAppDistDir AddBackslash(ProjectRoot) + "MixSplitR"
  #else
    #define MyAppDistDir AddBackslash(ProjectRoot) + "dist\\MixSplitR"
  #endif
#endif

[Setup]
AppId={{F11B91F0-9EAA-4E40-91B1-51D0F4D3FEA9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir={#ProjectRoot}\dist\installers
OutputBaseFilename=MixSplitR-Setup-{#MyAppVersion}
SetupIconFile={#ProjectRoot}\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#MyAppDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
