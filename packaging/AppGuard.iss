; AppGuard 0.3.0 installer (Inno Setup 6)
; Build: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\AppGuard.iss

[Setup]
AppId={{8C3B6D2E-4A57-4F3E-9B6D-2A9C7E1F5D40}
AppName=AppGuard
AppVersion=0.3.0
AppVerName=AppGuard 0.3.0
AppPublisher=AppGuard
DefaultDirName={autopf}\AppGuard
DefaultGroupName=AppGuard
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=AppGuardSetup-0.3.0-x64
SetupIconFile=..\appguard.ico
UninstallDisplayIcon={app}\AppGuard.exe
UninstallDisplayName=AppGuard
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
AppMutex=Local\AppGuard.TrayLauncher.v1
VersionInfoVersion=0.3.0.0
VersionInfoDescription=AppGuard setup
VersionInfoProductName=AppGuard
VersionInfoProductVersion=0.3.0

[Files]
Source: "..\dist\AppGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; DestName: "README-zh.md"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Icons]
Name: "{group}\AppGuard"; Filename: "{app}\AppGuard.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall AppGuard"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AppGuard"; Filename: "{app}\AppGuard.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AppGuard.exe"; Description: "Launch AppGuard"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\AppGuard.exe"; Parameters: "uninstall --quiet"; Flags: runhidden waituntilterminated; RunOnceId: "AppGuardUninstall"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\AppGuard"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Stop a guard from the previous installation before files are replaced. }
  if FileExists(ExpandConstant('{app}\AppGuard.exe')) then
  begin
    Exec(ExpandConstant('{app}\AppGuard.exe'), 'cli stop', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
  end;
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/end /tn AppGuard', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if (not Exec(ExpandConstant('{app}\AppGuard.exe'), 'install --quiet', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode))
       or (ResultCode <> 0) then
    begin
      MsgBox('AppGuard was installed, but the elevated login task could not be started.' + #13#10 +
             'Run the app as administrator, or reinstall, then check the log:' + #13#10 +
             ExpandConstant('{localappdata}\AppGuard\appguard.log'),
             mbError, MB_OK);
    end;
  end;
end;
