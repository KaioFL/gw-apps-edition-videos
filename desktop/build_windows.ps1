# Gera o aplicativo do Windows e o instalador.
# Uso (PowerShell, na pasta desktop):   .\build_windows.ps1
#   -Portatil  também gera um .zip para usar sem instalar
# Resultado na pasta desktop\saida.
param([switch]$Portatil)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Nome = "GW Apps Edition Videos"

function Rodar {
    # o PyInstaller e o pip escrevem avisos no stderr: no PowerShell 5.1 isso viraria erro.
    # Aqui o stderr só é mostrado; quem diz se falhou é o código de saída.
    $ErrorActionPreference = "Continue"
    & $args[0] $args[1..($args.Count - 1)] 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ }
    }
    if ($LASTEXITCODE -ne 0) { throw "Falhou: $($args -join ' ')" }
}

$env:PYTHONIOENCODING = "utf-8"
$vpy = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    $py = if ($env:GW_PYTHON) { $env:GW_PYTHON } else { "python" }
    Write-Host "Criando o ambiente de build..."
    Rodar $py -m venv .venv-build
}
Write-Host "[1/4] Instalando as ferramentas de build..."
Rodar $vpy -m pip install --disable-pip-version-check -q --upgrade pip
Rodar $vpy -m pip install --disable-pip-version-check -q -r requisitos-build.txt

Write-Host "[2/4] Juntando ffmpeg, leitor de fala e ícones..."
Rodar $vpy preparar_build.py

Write-Host "[3/4] Montando o aplicativo..."
Rodar $vpy -m PyInstaller --noconfirm --clean gw_desktop.spec --distpath dist --workpath build
Copy-Item "..\COMO USAR.txt" "dist\$Nome\COMO USAR.txt"

$versao = (Select-String -Path ..\programa\app.py -Pattern '^VERSAO = "([^"]+)"').Matches[0].Groups[1].Value
New-Item -ItemType Directory -Force saida | Out-Null

Write-Host "[4/4] Gerando o instalador..."
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    Rodar $iscc "/Q" "/DAppVersion=$versao" instalador_windows.iss
    Write-Host "Instalador: saida\GW-Apps-Edition-Videos-$versao-Instalador-Windows.exe"
} else {
    Write-Host "Inno Setup 6 não encontrado (https://jrsoftware.org/isdl.php): gerando só a versão portátil."
    $Portatil = $true
}

if ($Portatil) {
    $zip = "saida\GW-Apps-Edition-Videos-$versao-Windows-portatil.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Rodar tar.exe -a -c -f $zip -C dist $Nome
    Write-Host "Portátil: $zip"
}
Write-Host "Pronto."
