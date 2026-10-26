; InitializeSetup 只运行真实目录判据并写测试结果，不创建安装目录、登记项或快捷方式。
[Setup]
AppName=Peach directory contract
AppVersion=1.0
AppId=PeachDirectoryContract
PrivilegesRequired=lowest
CreateAppDir=no
Uninstallable=no
OutputBaseFilename=directory-contract

[Code]
#include "directory-validation.iss"

function InitializeSetup(): Boolean;
var
  Error: String;
begin
  Error := DirectoryError(ExpandConstant('{param:program}'),
    ExpandConstant('{param:data}'), ExpandConstant('{param:previous}'));
  if Error = '' then
    Error := 'ok';
  SaveStringToFile(ExpandConstant('{param:result}'), Error, False);
  Result := False;
end;
