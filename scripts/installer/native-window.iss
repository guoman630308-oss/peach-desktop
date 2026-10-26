#ifndef AppVersion
  #define AppVersion "0.46.1"
#endif

[Setup]
AppId={{A74968D7-59A0-4DE7-B288-079EF3E03ED7}
AppName=Peach 独立窗口测试版
AppVersion={#AppVersion}
DefaultDirName={userpf}\PeachDesktop
DefaultGroupName=Peach 独立窗口测试版
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PeachDesktop-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Peach.exe
CloseApplications=no
RestartApplications=no
SetupIconFile={#SourceDir}\_internal\resources\peach.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Icons]
Name: "{userprograms}\Peach 独立窗口测试版"; Filename: "{app}\Peach.exe"; Parameters: "--show"
Name: "{userdesktop}\Peach 独立窗口测试版"; Filename: "{app}\Peach.exe"; Parameters: "--show"; Tasks: desktopicon

[Run]
Filename: "{app}\Peach.exe"; Parameters: "--show"; Description: "打开 Peach 独立窗口"; Flags: nowait postinstall skipifsilent

[Code]
function OpenProgramForWrite(Name: String; Access, Sharing, Security, Creation, Flags, Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseProgramHandle(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function WaitForProgramRelease(Name: String): Boolean;
var
  Attempt: Integer;
  Handle: LongWord;
begin
  Result := False;
  for Attempt := 1 to 120 do begin
    Handle := OpenProgramForWrite(Name, $40000000, 0, 0, 3, $80, 0);
    if Handle <> $FFFFFFFF then begin
      CloseProgramHandle(Handle);
      Result := True;
      Exit;
    end;
    Sleep(250);
  end;
end;

function StopWindow(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\Peach.exe')) then begin
    Result := Exec(ExpandConstant('{app}\Peach.exe'), '--quit', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
    if Result then Result := WaitForProgramRelease(ExpandConstant('{app}\Peach.exe'));
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopWindow() then Result := '请先从托盘退出 Peach，等待当前任务结束后重试。';
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := StopWindow();
  if Result then
    Result := Exec(ExpandConstant('{app}\Peach.exe'), '--installer-uninstall', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;
