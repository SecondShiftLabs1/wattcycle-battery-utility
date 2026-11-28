#ifndef AppVersion
  #define AppVersion "0.3.0-beta.1"
#endif
[Setup]
AppId=WattCycleBatteryUtility
AppName=WattCycle Battery Utility
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\WattCycle
DefaultGroupName=WattCycle
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=WattCycle-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\WattCycle.exe
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\WattCycle.exe"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\WattCycle"; Filename: "{app}\WattCycle.exe"
Name: "{autodesktop}\WattCycle"; Filename: "{app}\WattCycle.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\WattCycle.exe"; Description: "Launch WattCycle"; Flags: nowait postinstall skipifsilent
