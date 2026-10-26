; Peach Windows 安装包。由 scripts/build_windows.ps1 -Installer 调用 ISCC 编译：
;   ISCC /DAppVersion=<版本> /DSourceDir=<独立目录包 Peach\> /DOutputDir=<输出目录> peach.iss
; 装的内容与免安装包逐字节相同，安装器提供目录选择、开始菜单项和「应用和功能」里的登记。
; 自更新、开机自启、桌面图标和应用内卸载仍由 Peach 管，约定见 src/peach/desktop_installer.py。

#ifndef AppVersion
  #error 缺少 /DAppVersion
#endif
#ifndef SourceDir
  #error 缺少 /DSourceDir
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
; 与 desktop_installer.APP_ID 相同；改了它，已装的机器会并排出现第二份。
AppId={{02D9748A-78F6-4866-AC75-918177194DAB}
AppName=Peach
AppVersion={#AppVersion}
AppVerName=Peach {#AppVersion}
AppPublisher=Peach
AppPublisherURL=https://github.com/peach-mitao/peach
AppSupportURL=https://github.com/peach-mitao/peach/issues
; 只装到当前用户：自更新在程序目录旁边建事务目录再整目录切换，Program Files 不可写。
PrivilegesRequired=lowest
DefaultDirName={userpf}\Peach
DisableDirPage=no
UsePreviousAppDir=yes
UsePreviousTasks=no
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
#ifdef LiteInstaller
OutputBaseFilename=Peach-{#AppVersion}-windows-x64-lite-setup
#else
OutputBaseFilename=Peach-{#AppVersion}-windows-x64-setup
#endif
SetupIconFile=..\..\resources\peach.ico
UninstallDisplayIcon={app}\Peach.exe
UninstallDisplayName=Peach
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
chs.StillRunning=Peach 仍在运行，未能自动退出。请从托盘菜单退出 Peach 后重试。
chs.TaskCheckFailed=无法确认后台任务状态，安装已取消。请等待任务结束后重试。
chs.MigrationFailed=本地数据库未能更新到新版本，Peach 启动后可能出错。升级前的备份在数据目录的 database 文件夹里，文件名以 ledger.pre-migrate- 开头。
chs.DataKept=Peach 已卸载。设置、本地数据库和观看记录保留在 %1，需要时可手动删除；媒体文件不受影响。
chs.DirectoryOverlapsData=程序目录不能与 Peach 数据目录重叠。请选择独立的程序文件夹。
chs.DirectoryNotDedicated=请选择空文件夹或已有的 Peach 安装目录。卸载 Peach 时会移除整个程序文件夹。
chs.DirectoryNotWritable=当前用户无法写入程序目录或其上级目录。请选择有写入权限的位置，以便安装和更新 Peach。
chs.DirectoryLink=程序目录和数据目录不能经过目录链接。请选择普通文件夹。
chs.DirectoryMove=请先卸载现有 Peach（保留数据），再选择新的安装目录。
chs.ReportingFailed=版本统计与错误诊断选项未能保存。首次设置或设置页可再次选择；未保存的同意不会启用上传。
chs.LibrarySharingFailed=馆藏与口味上传选项未能保存。可在设置页重新选择。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "reporting"; Description: "发送版本统计与错误诊断（推荐）：版本、系统信息、故障类型、代码位置与发生次数"; GroupDescription: "数据上传（可在设置页关闭）："
Name: "librarysharing"; Description: "上传馆藏与各类偏好明细（匿名化处理）"; GroupDescription: "数据上传（可在设置页关闭）："; Flags: unchecked

[InstallDelete]
; PyInstaller 的依赖都在 _internal 里；覆盖安装留下上一版多出来的库会被新版误加载。
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\*"; DestDir: "{tmp}\Peach-preflight"; Flags: dontcopy recursesubdirs createallsubdirs
#ifdef LiteInstaller
Source: "{tmp}\Peach-preflight\tools\*"; DestDir: "{app}\tools"; ExternalSize: 0; Flags: external ignoreversion recursesubdirs createallsubdirs
Source: "{tmp}\Peach-preflight\cloudflared.exe"; DestDir: "{app}"; ExternalSize: 0; Flags: external ignoreversion
Source: "{tmp}\Peach-preflight\cloudflared-manifest.json"; DestDir: "{app}"; ExternalSize: 0; Flags: external ignoreversion
#endif

[Icons]
Name: "{userprograms}\Peach"; Filename: "{app}\Peach.exe"; Parameters: "--show"
Name: "{userdesktop}\Peach"; Filename: "{app}\Peach.exe"; Parameters: "--show"; Tasks: desktopicon

[Run]
Filename: "{app}\Peach.exe"; Parameters: "--show"; Description: "{cm:LaunchProgram,Peach}"; Flags: nowait postinstall skipifsilent
Filename: "https://github.com/peach-mitao/peach"; Description: "打开 Peach 仓库，给项目加星"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallDelete]
; 自更新整目录换过之后，卸载记录里的文件清单不再完整，按目录整个移除。
Type: filesandordirs; Name: "{app}"

[Code]
var
  GuardPrepared: Boolean;
  LibrarySharingPage: TOutputMsgMemoWizardPage;
#ifdef LiteInstaller
  ToolsPrepared: Boolean;
  ToolsPage: TOutputMarqueeProgressWizardPage;
#endif

function GetCurrentProcessId(): LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';

procedure InitializeWizard();
begin
  LibrarySharingPage := CreateOutputMsgMemoPage(wpSelectTasks,
    '馆藏与口味明细上传', '用于 Peach 用户群体的馆藏结构与偏好研究。',
    '继续表示同意每周上传以下明细；不参与可返回取消勾选。', '');
  LibrarySharingPage.RichEditViewer.Text :=
    '作品：去重后的作品编号、评分、喜欢、不喜欢、已看、稍后看、播放次数、观看比例与手动添加的标签。' + #13#10#13#10 +
    '偏好汇总：出演者、创作者、厂牌、系列、手动标签、年代与时长分组的收藏、喜欢、不喜欢、观看数量和评分分布（有编号作品与无编号条目分开），以及主动收藏的实体名称。' + #13#10#13#10 +
    '馆藏覆盖：文件数量、有编号作品数量与无编号条目数量。无编号馆藏只参与本机汇总。' + #13#10#13#10 +
    '版本与周期：Peach 版本、字段版本、UTC 自然周。' + #13#10#13#10 +
    '不上传账号、设备或安装标识、内部行号、文件名、路径、标题、媒体、网址、搜索词及逐条浏览或观看记录。' + #13#10#13#10 +
    '完整明细可反映成人内容偏好；接收服务为独立 Sentry 项目，可见连接 IP。' + #13#10#13#10 +
    '可在设置页查看完整本机预览或关闭。关闭停止后续发送，已发送数据无法通过本机开关撤回。';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = LibrarySharingPage.ID) and not WizardIsTaskSelected('librarysharing');
end;

procedure FinishTaskGuard();
var
  ResultCode: Integer;
begin
  if GuardPrepared then
  begin
    if Exec(ExpandConstant('{tmp}\Peach-preflight\Peach.exe'),
      '--installer-finish "' + ExpandConstant('{app}') + '" ' + IntToStr(GetCurrentProcessId()),
      ExpandConstant('{tmp}\Peach-preflight'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
      GuardPrepared := False;
  end;
end;

procedure DeinitializeSetup();
begin
  FinishTaskGuard();
end;

function DataRoot(): String;
begin
  Result := GetEnv('PEACH_DATA_ROOT');
  if Result = '' then
    Result := ExpandConstant('{localappdata}\Peach\peach-data');
end;

#include "directory-validation.iss"

function InstallationDirectoryError(): String;
var
  PreviousDirectory: String;
begin
  PreviousDirectory := '';
  RegQueryStringValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{02D9748A-78F6-4866-AC75-918177194DAB}_is1',
    'Inno Setup: App Path', PreviousDirectory);
  Result := DirectoryError(WizardDirValue(), DataRoot(), PreviousDirectory);
  if Result = '' then
    Result := DirectoryError(WizardDirValue(), ExpandConstant('{localappdata}\Peach\peach-data'), PreviousDirectory);
  if Result <> '' then
    Result := CustomMessage(Result);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Error: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Error := InstallationDirectoryError();
    Result := Error = '';
    if not Result then
      SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ErrorText: AnsiString;
begin
  Result := InstallationDirectoryError();
  if Result <> '' then
    Exit;
  if GuardPrepared then
    Exit;
  ExtractTemporaryFiles('{tmp}\Peach-preflight\*');
#ifdef LiteInstaller
  if not ToolsPrepared then
  begin
    if ToolsPage = nil then
      ToolsPage := CreateOutputMarqueeProgressPage('准备媒体工具', '已有工具会校验后复用，缺少时自动下载。');
    ToolsPage.SetText('正在检查、下载并校验媒体工具，请稍候。', '下载失败可重试，现有安装会保留。');
    ToolsPage.Show;
    ToolsPage.Animate;
    try
      ToolsPrepared := Exec(ExpandConstant('{tmp}\Peach-preflight\Peach.exe'),
        '--installer-tools "' + ExpandConstant('{app}') + '" "' + ExpandConstant('{src}\.') +
        '" "' + ExpandConstant('{tmp}\Peach-preflight\tools-error.txt') + '"',
        ExpandConstant('{tmp}\Peach-preflight'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
    finally
      ToolsPage.Hide;
    end;
    if not ToolsPrepared then
    begin
      Result := '媒体工具未能准备完成，请检查网络后重试。';
      if LoadStringFromFile(ExpandConstant('{tmp}\Peach-preflight\tools-error.txt'), ErrorText) then
        Result := UTF8Decode(ErrorText);
      Exit;
    end;
  end;
#endif
  if not Exec(ExpandConstant('{tmp}\Peach-preflight\Peach.exe'),
      '--installer-prepare "' + ExpandConstant('{app}') + '" ' + IntToStr(GetCurrentProcessId()) +
      ' "' + ExpandConstant('{tmp}\Peach-preflight\prepare-error.txt') + '"',
      ExpandConstant('{tmp}\Peach-preflight'), SW_HIDE, ewWaitUntilTerminated, ResultCode) or
      (ResultCode <> 0) then
  begin
    Result := CustomMessage('TaskCheckFailed');
    if LoadStringFromFile(ExpandConstant('{tmp}\Peach-preflight\prepare-error.txt'), ErrorText) then
      Result := UTF8Decode(ErrorText);
  end
  else
    GuardPrepared := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ReportingConsent: String;
  LibraryConsent: String;
begin
  if (CurStep = ssPostInstall) and not WizardSilent() then
  begin
    ReportingConsent := '0';
    if WizardIsTaskSelected('reporting') then ReportingConsent := '1';
    if not Exec(ExpandConstant('{app}\Peach.exe'),
      '--installer-reporting ' + ReportingConsent + ' ' + ReportingConsent,
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      SuppressibleMsgBox(CustomMessage('ReportingFailed'), mbError, MB_OK, IDOK);
    LibraryConsent := '0';
    if WizardIsTaskSelected('librarysharing') then LibraryConsent := '1';
    if not Exec(ExpandConstant('{app}\Peach.exe'),
      '--installer-library-sharing ' + LibraryConsent,
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      SuppressibleMsgBox(CustomMessage('LibrarySharingFailed'), mbError, MB_OK, IDOK);
  end;
  { 服务启动时不迁移账本；覆盖安装换了程序，要在这里补上。没有账本的新机器不跑，
    否则会凭空建出账本，首次设置页就再也不出现。migrate 自己会先备份。 }
  if CurStep = ssPostInstall then
    if not Exec(ExpandConstant('{tmp}\Peach-preflight\Peach.exe'),
                '--installer-migrate "' + ExpandConstant('{app}') + '"', ExpandConstant('{tmp}\Peach-preflight'),
                SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      SuppressibleMsgBox(CustomMessage('MigrationFailed'), mbError, MB_OK, IDOK);
  if CurStep = ssPostInstall then
    FinishTaskGuard();
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Exec(ExpandConstant('{app}\Peach.exe'), '--installer-uninstall', ExpandConstant('{app}'),
          SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 1) then
  begin
    SuppressibleMsgBox(CustomMessage('StillRunning'), mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DirExists(DataRoot()) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('DataKept'), [DataRoot()]), mbInformation, MB_OK, IDOK);
end;
