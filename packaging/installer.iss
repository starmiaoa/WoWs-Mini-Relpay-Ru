; WoWs-Mini-Relpay-Ru Inno Setup 6 安装包脚本（长期维护）
; 用法: ISCC.exe /DMyAppVersion=0.2.1 /DMyDistDir=..\dist\WoWs-Mini-Relpay-Ru /DMyOutputDir=..\dist packaging\installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.2.1"
#endif
#ifndef MyDistDir
  #define MyDistDir "..\dist\WoWs-Mini-Relpay-Ru"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\dist"
#endif

#define MyAppName "WoWs-Mini-Relpay-Ru"
#define MyAppPublisher "starmiaoa"
#define MyAppURL "https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru"
#define MyAppExeName "WoWs-Mini-Relpay-Ru.exe"

[Setup]
; 固定 AppId 用于后续所有版本原地无缝升级（切勿修改！）
AppId={{7F6A4C2B-9E15-4D82-A31C-91A2B5E8D401}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 默认无需管理员权限装入当前用户目录（免 UAC 弹窗），亦可弹窗切换为所有用户安装
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyAppName}-Setup-win64
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=yes
ChangesEnvironment=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Mir Korabley Minimap Replay Renderer Setup
VersionInfoCopyright=Copyright (c) 2026 {#MyAppPublisher}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
chinesesimplified.CreateDesktopIcon=创建桌面快捷方式(&D)
chinesesimplified.LaunchAfterInstall=安装完成后立即启动 {#MyAppName}
english.CreateDesktopIcon=Create a &desktop shortcut
english.LaunchAfterInstall=Launch {#MyAppName}
russian.CreateDesktopIcon=Создать ярлык на &рабочем столе
russian.LaunchAfterInstall=Запустить {#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchAfterInstall}"; Flags: nowait postinstall skipifsilent
