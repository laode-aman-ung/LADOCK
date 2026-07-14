; Inno Setup script for LADOCK Desktop (Windows installer).
; Build:  ISCC.exe /DVariant=windows        /DSourceDir=..\..\build\dist-windows\LADOCK
;         ISCC.exe /DVariant=windows-hybrid /DSourceDir=..\..\build\dist-windows-hybrid\LADOCK
; (SourceDir = the PyInstaller one-dir output produced by build_release.py)

#ifndef Variant
  #define Variant "windows"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist-" + Variant + "\LADOCK"
#endif
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

#define AppName "LADOCK Desktop"
#define Publisher "La Ode Aman"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\LADOCK
DefaultGroupName=LADOCK
UninstallDisplayIcon={app}\LADOCK.exe
OutputDir=..\..\build\installers
OutputBaseFilename=LADOCK-{#AppVersion}-{#Variant}-setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\LADOCK Desktop"; Filename: "{app}\LADOCK.exe"
Name: "{autodesktop}\LADOCK Desktop"; Filename: "{app}\LADOCK.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\LADOCK.exe"; Description: "Launch LADOCK Desktop"; Flags: nowait postinstall skipifsilent

#if Variant == "windows-hybrid"
[Messages]
WelcomeLabel2=This is the HYBRID edition. Vina/Vinardo run natively; AutoDock4 / AutoDock-GPU are dispatched to WSL. Enable WSL + an Ubuntu distro, then turn on Hybrid mode in Tools -> Settings -> Backend. AutoDock-GPU also needs CUDA-on-WSL.
#endif
