{ 安装器与隔离回归共用的目录判据。返回 CustomMessages 的键，空串表示可安装。 }
function DirectoryAttributes(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function DirectoryPath(Name: String): String;
var
  Parent: String;
begin
  Name := RemoveBackslashUnlessRoot(ExpandFileName(Name));
  Parent := ExtractFileDir(Name);
  if DirExists(Name) or (Parent = Name) or (Parent = '') then
    Result := AddBackslash(LowerCase(GetShortName(Name)))
  else
    Result := DirectoryPath(Parent) + AddBackslash(LowerCase(ExtractFileName(Name)));
end;

function HasDirectoryLink(Name: String): Boolean;
var
  Parent: String;
  Attributes: Cardinal;
begin
  Result := False;
  Name := RemoveBackslashUnlessRoot(ExpandFileName(Name));
  while Name <> '' do
  begin
    Attributes := DirectoryAttributes(Name);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then
    begin
      Result := True;
      Exit;
    end;
    Parent := ExtractFileDir(Name);
    if Parent = Name then
      Exit;
    Name := Parent;
  end;
end;

function CanWriteDirectory(Name: String): Boolean;
var
  Probe: String;
begin
  Result := False;
  try
    Probe := GenerateUniqueName(Name, '.tmp');
    if SaveStringToFile(Probe, '', False) then
      Result := DeleteFile(Probe);
  except
    Result := False;
  end;
end;

function DirectoryError(ProgramDirectory, DataDirectory, PreviousDirectory: String): String;
var
  ProgramPath, DataPath, Parent: String;
  Found: TFindRec;
  Installed: Boolean;
begin
  Result := '';
  ProgramPath := DirectoryPath(ProgramDirectory);
  DataPath := DirectoryPath(DataDirectory);
  if (Pos(ProgramPath, DataPath) = 1) or (Pos(DataPath, ProgramPath) = 1) then
    Result := 'DirectoryOverlapsData'
  else if HasDirectoryLink(ProgramDirectory) or HasDirectoryLink(DataDirectory) then
    Result := 'DirectoryLink';
  if Result <> '' then
    Exit;
  if FileExists(ProgramDirectory) or
     (RemoveBackslashUnlessRoot(ExpandFileName(ProgramDirectory)) =
      AddBackslash(ExtractFileDrive(ExpandFileName(ProgramDirectory)))) then
  begin
    Result := 'DirectoryNotDedicated';
    Exit;
  end;

  Installed := FileExists(AddBackslash(ProgramDirectory) + 'Peach.exe') and
    FileExists(AddBackslash(ProgramDirectory) + 'unins000.exe');
  if not Installed and FindFirst(AddBackslash(ProgramDirectory) + '*', Found) then
  begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then
        begin
          Result := 'DirectoryNotDedicated';
          Exit;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
  if (PreviousDirectory <> '') and
     (DirectoryPath(PreviousDirectory) <> ProgramPath) and
     FileExists(AddBackslash(PreviousDirectory) + 'unins000.exe') then
  begin
    Result := 'DirectoryMove';
    Exit;
  end;

  { 更新事务在程序目录的上级创建，现有程序目录本身也必须可写。 }
  if DirExists(ProgramDirectory) and not CanWriteDirectory(ProgramDirectory) then
  begin
    Result := 'DirectoryNotWritable';
    Exit;
  end;
  Parent := ExtractFileDir(RemoveBackslashUnlessRoot(ExpandFileName(ProgramDirectory)));
  while (Parent <> '') and not DirExists(Parent) do
  begin
    if ExtractFileDir(Parent) = Parent then
    begin
      Result := 'DirectoryNotWritable';
      Exit;
    end;
    Parent := ExtractFileDir(Parent);
  end;
  if (Parent = '') or not CanWriteDirectory(Parent) then
    Result := 'DirectoryNotWritable';
end;
