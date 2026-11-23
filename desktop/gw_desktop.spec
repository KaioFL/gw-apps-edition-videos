# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: gera o aplicativo do GW Apps Edition Videos.
#   Windows -> dist/GW Apps Edition Videos/GW Apps Edition Videos.exe
#   Mac     -> dist/GW Apps Edition Videos.app
# Rode antes: python preparar_build.py  (ícones, ffmpeg e leitor de fala)
import os
import re
import sys

from PyInstaller.utils.hooks import collect_all

AQUI = os.path.abspath(SPECPATH)
PROG = os.path.normpath(os.path.join(AQUI, "..", "programa"))
RECURSOS = os.path.join(AQUI, "recursos")
NOME = "GW Apps Edition Videos"
MODELO = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"

with open(os.path.join(PROG, "app.py"), encoding="utf-8") as fh:
    VERSAO = re.search(r'^VERSAO = "([^"]+)"', fh.read(), re.M).group(1)


def pasta(origem, destino):
    """Arquivos de uma pasta, sem os escondidos e sem as sobras do Mac (._arquivo, .DS_Store)."""
    out = []
    for raiz, dirs, arqs in os.walk(origem):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_", "__pycache__", "test_wavs"))]
        for a in arqs:
            if a.startswith("."):
                continue
            rel = os.path.relpath(raiz, origem)
            out.append((os.path.join(raiz, a), os.path.normpath(os.path.join(destino, rel))))
    return out


datas = []
datas += pasta(os.path.join(PROG, "painel"), "painel")
datas += pasta(os.path.join(PROG, "fontes"), "fontes")
datas += pasta(os.path.join(PROG, "marca"), "marca")
datas += [(os.path.join(PROG, "musicas", "COLOQUE_SUAS_MUSICAS_AQUI.txt"), "musicas"),
          (os.path.join(PROG, "config.json"), "."),        # ajustes de edição que já vêm configurados
          (os.path.join(PROG, "LICENCAS.txt"), ".")]

arq_modelo = os.path.join(RECURSOS, "modelo.txt")
if os.path.isfile(arq_modelo):
    with open(arq_modelo, encoding="utf-8") as fh:
        datas += pasta(fh.read().strip(), os.path.join("modelos", MODELO))

ext = ".exe" if sys.platform == "win32" else ""
binaries = [(os.path.join(RECURSOS, "bin", n + ext), "bin") for n in ("ffmpeg", "ffprobe")]

s_datas, s_bins, s_hidden = collect_all("sherpa_onnx")
datas += s_datas
binaries += s_bins

a = Analysis(
    [os.path.join(AQUI, "gw_desktop.py")],
    pathex=[PROG],
    binaries=binaries,
    datas=datas,
    hiddenimports=s_hidden + ["app", "editor", "trilhas"],
    excludes=["tkinter", "static_ffmpeg", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "gi", "cefpython3", "sherpa_onnx.cli"],
    noarchive=False,
)
pyz = PYZ(a.pure)

versao_win = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
                                                     StringStruct, VarFileInfo, VarStruct)
    nums = tuple(int(x) for x in (VERSAO.split(".") + ["0"] * 4)[:4])
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("041604B0", [
            StringStruct("CompanyName", "GW Apps Solutions, LLC"),
            StringStruct("FileDescription", NOME),
            StringStruct("FileVersion", VERSAO),
            StringStruct("InternalName", "gw-edition-videos"),
            StringStruct("LegalCopyright", "© 2026 GW Apps Solutions, LLC"),
            StringStruct("OriginalFilename", NOME + ".exe"),
            StringStruct("ProductName", NOME),
            StringStruct("ProductVersion", VERSAO)])]),
            VarFileInfo([VarStruct("Translation", [0x0416, 1200])])])
    versao_win = os.path.join(workpath, "versao_windows.txt")
    os.makedirs(workpath, exist_ok=True)
    with open(versao_win, "w", encoding="utf-8") as fh:
        fh.write(str(info))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NOME,
    console=False,                 # sem janela preta
    upx=False,
    icon=os.path.join(RECURSOS, "icone.icns" if sys.platform == "darwin" else "icone.ico"),
    version=versao_win,
    argv_emulation=False,
    codesign_identity=os.environ.get("GW_MAC_ASSINATURA") or None,
    entitlements_file=os.path.join(AQUI, "entitlements.plist") if os.environ.get("GW_MAC_ASSINATURA") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NOME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=NOME + ".app",
        icon=os.path.join(RECURSOS, "icone.icns"),
        bundle_identifier="com.gwapps.editionvideos",
        version=VERSAO,
        info_plist={
            "CFBundleName": NOME,
            "CFBundleDisplayName": NOME,
            "CFBundleShortVersionString": VERSAO,
            "CFBundleVersion": VERSAO,
            "CFBundleDevelopmentRegion": "pt_BR",
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.video",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "© 2026 GW Apps Solutions, LLC",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
