"""Junta o que vai dentro do aplicativo antes do PyInstaller:
  - ícones (desktop/recursos/icone.*)
  - ffmpeg e ffprobe com suporte a legendas (desktop/recursos/bin)
  - leitor de fala (programa/modelos/...; baixa ~490 MB se ainda não tiver)
Uso: python preparar_build.py
"""
import os
import sys
import shutil
import stat

AQUI = os.path.dirname(os.path.abspath(__file__))
PROG = os.path.normpath(os.path.join(AQUI, "..", "programa"))
RECURSOS = os.path.join(AQUI, "recursos")
sys.path.insert(0, AQUI)
sys.path.insert(0, PROG)

import gerar_icones  # noqa: E402
import editor        # noqa: E402


def ffmpeg():
    destino = os.path.join(RECURSOS, "bin")
    ext = ".exe" if os.name == "nt" else ""
    alvo = {n: os.path.join(destino, n + ext) for n in ("ffmpeg", "ffprobe")}
    if not all(os.path.isfile(f) for f in alvo.values()):
        from static_ffmpeg import run
        print("Baixando o ffmpeg…")
        ffm, ffp = run.get_or_fetch_platform_executables_else_raise()
        os.makedirs(destino, exist_ok=True)
        for origem, nome in ((ffm, "ffmpeg"), (ffp, "ffprobe")):
            shutil.copy2(origem, alvo[nome])
            os.chmod(alvo[nome], os.stat(alvo[nome]).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not editor._tem_libass(alvo["ffmpeg"]):
        raise SystemExit("O ffmpeg baixado não desenha legendas (falta o libass).")
    print("ffmpeg:", alvo["ffmpeg"])


def modelo():
    if os.environ.get("GW_SEM_MODELO") == "1":
        print("Leitor de fala: NÃO vai junto (baixa na primeira transcrição).")
        return
    p = editor.baixar_modelo()
    for f in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
        if not os.path.isfile(os.path.join(p, f)):
            raise SystemExit(f"Falta o arquivo {f} do leitor de fala em {p}")
    with open(os.path.join(RECURSOS, "modelo.txt"), "w", encoding="utf-8") as fh:
        fh.write(p)
    print("leitor de fala:", p)


if __name__ == "__main__":
    os.makedirs(RECURSOS, exist_ok=True)
    gerar_icones.main()
    ffmpeg()
    modelo()
