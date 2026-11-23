@echo off
setlocal
chcp 65001 >nul
title GW Apps Edition Videos - deixe esta janela aberta
if exist "%~dp0programa\app.py" goto na_pasta
rem Aberto de fora da pasta do programa (por exemplo, de dentro do .zip):
rem procura uma instalacao ja extraida nos lugares de sempre e abre ela.
for %%P in ("%USERPROFILE%\Downloads\GW Apps Edition Videos - Windows\GW Apps Edition Videos" "%USERPROFILE%\Downloads\GW Apps Edition Videos" "%USERPROFILE%\Desktop\GW Apps Edition Videos" "%USERPROFILE%\Documents\GW Apps Edition Videos") do (
  if exist "%%~P\programa\app.py" if exist "%%~P\Abrir GW Apps Edition Videos.bat" (
    call "%%~P\Abrir GW Apps Edition Videos.bat"
    exit /b
  )
)
goto fora_da_pasta

:na_pasta
cd /d "%~dp0programa"
if errorlevel 1 goto fora_da_pasta

rem O programa usa um ambiente Python proprio (pasta programa\.venv).
rem Assim funciona com qualquer Python instalado (python.org, Microsoft Store ou uv)
rem e nao mexe no Python do computador.
set "VPY=%CD%\.venv\Scripts\python.exe"

if not exist ".instalado" goto instalar
if not exist "%VPY%" goto instalar
"%VPY%" -c "import numpy, PIL, sherpa_onnx" >nul 2>nul
if errorlevel 1 goto instalar
goto abrir

:instalar
if exist ".instalado" del ".instalado"
echo.
echo  GW APPS EDITION VIDEOS - primeira vez
echo  Instalando o que o editor precisa. Isso so acontece uma vez e leva alguns minutos.
echo.
call :achar_python
if defined PY goto criar_ambiente
where uv >nul 2>nul
if errorlevel 1 goto sem_python

:criar_ambiente
echo  [1/5] Criando o ambiente do programa...
if exist ".venv" rmdir /s /q ".venv"
if defined PY %PY% -m venv ".venv"
if not exist "%VPY%" call :ambiente_uv
if not exist "%VPY%" goto falhou

echo  [2/5] Instalando os pacotes (alguns minutos)...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip >nul 2>nul
"%VPY%" -m pip install --disable-pip-version-check -q numpy pillow sherpa-onnx static-ffmpeg
"%VPY%" -c "import numpy, PIL, sherpa_onnx, static_ffmpeg" >nul 2>nul
if errorlevel 1 call :pacotes_uv
"%VPY%" -c "import numpy, PIL, sherpa_onnx, static_ffmpeg" >nul 2>nul
if errorlevel 1 goto falhou

echo  [3/5] Baixando o ffmpeg...
"%VPY%" -c "import editor; editor.ffbin('ffmpeg')"
if errorlevel 1 goto falhou
echo  [4/5] Baixando o leitor de fala (cerca de 490 MB)...
"%VPY%" -c "import editor; editor.baixar_modelo()"
if errorlevel 1 goto falhou
echo  [5/5] Conferindo...
"%VPY%" -c "import editor; editor.verificar()"
if errorlevel 1 goto falhou
echo ok> ".instalado"
echo.
echo  Pronto! Instalado.
echo.

:abrir
"%VPY%" app.py
echo.
pause
exit /b 0


rem ------------------------------------------------------------------ rotinas
:achar_python
rem Procura um Python 3.10 a 3.14 (o 3.15 ainda nao tem o leitor de fala).
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,15) else 1)" >nul 2>nul && set "PY=python"
exit /b 0

:ambiente_uv
rem Se o Python do computador nao conseguir criar o ambiente, tenta pelo uv (que baixa um Python sozinho).
where uv >nul 2>nul
if errorlevel 1 exit /b 0
if exist ".venv" rmdir /s /q ".venv"
uv venv --seed --python 3.12 ".venv"
exit /b 0

:pacotes_uv
where uv >nul 2>nul
if errorlevel 1 exit /b 0
uv pip install --python "%VPY%" numpy pillow sherpa-onnx static-ffmpeg
exit /b 0

:sem_python
echo.
echo  Falta instalar o Python (e gratis).
echo  Vou abrir a pagina de download: baixe o "Windows installer (64-bit)".
echo  IMPORTANTE: na primeira tela da instalacao, marque "Add python.exe to PATH".
echo  Depois abra este arquivo de novo.
echo  (Use o Python 3.14 ou anterior. O 3.15 ainda nao funciona com o editor.)
echo.
start "" "https://www.python.org/downloads/release/python-3148/"
pause
exit /b 1

:fora_da_pasta
echo.
echo  Nao achei a pasta "programa" ao lado deste arquivo.
echo  Isso acontece quando o arquivo e aberto de dentro do .zip.
echo.
echo  Feche esta janela e abra pela PASTA extraida:
echo  Downloads ^> GW Apps Edition Videos - Windows ^> GW Apps Edition Videos
echo  ^> "Abrir GW Apps Edition Videos"
echo  (Se ainda nao extraiu: botao direito no .zip ^> "Extrair tudo".)
echo.
pause
exit /b 1

:falhou
echo.
echo  Algo deu errado na instalacao (veja a mensagem acima).
echo  Confira sua internet e abra este arquivo de novo.
echo  Se continuar, tire um print desta janela e mande para o suporte.
echo.
pause
exit /b 1
