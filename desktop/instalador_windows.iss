; Instalador do Windows (Inno Setup 6) — gerado pelo build_windows.ps1
; Instala para o usuário (não pede senha de administrador), cria atalho na Área de Trabalho e no Menu Iniciar.

#define AppName "GW Apps Edition Videos"
#ifndef AppVersion
  #define AppVersion "2.2"
#endif

[Setup]
AppId={{8F3C2B1E-6A4D-4E7F-9C2B-5D1A7E3F9B42}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=GW Apps Solutions, LLC
AppCopyright=© 2026 GW Apps Solutions, LLC
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=saida
OutputBaseFilename=GW-Apps-Edition-Videos-{#AppVersion}-Instalador-Windows
SetupIconFile=recursos\icone.ico
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; atualização: tira os arquivos da versão anterior do programa (os vídeos e ajustes ficam em outra pasta)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Comment: "Editor automático de vídeos"
Name: "{autoprograms}\{#AppName} - Como usar"; Filename: "{app}\COMO USAR.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Comment: "Editor automático de vídeos"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent
; atualização automática (instalador rodado pelo próprio app com /ABRIR=1): abre a versão nova no fim
Filename: "{app}\{#AppName}.exe"; Flags: nowait; Check: AbrirDepois

[Code]
function AbrirDepois: Boolean;
begin
  Result := ExpandConstant('{param:ABRIR|0}') = '1';
end;
