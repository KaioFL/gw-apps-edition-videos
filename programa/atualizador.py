"""Atualização automática do aplicativo: procura uma versão nova no GitHub, baixa e instala por cima
(sem desinstalar e sem mexer nos vídeos, avatares e ajustes, que ficam em outra pasta).

Windows: roda o instalador novo em modo silencioso; ele fecha o app, substitui os arquivos e abre de novo.
Mac: um pequeno script espera o app fechar, troca o .app de dentro do .dmg e abre de novo.
"""
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# repositório público só com os instaladores (o código fica no repositório privado)
REPO = os.environ.get("GW_ATUALIZACOES_REPO", "KaioFL/gw-apps-edition-videos-atualizacoes")
API = os.environ.get("GW_ATUALIZACOES_API", "https://api.github.com")
AGENTE = {"User-Agent": "GWAppsEditionVideos-atualizador", "Accept": "application/vnd.github+json"}
INTERVALO = 6 * 3600

estado = {"verificado": 0, "disponivel": None, "baixando": False, "progresso": 0.0, "aplicando": False, "erro": ""}
_trava = threading.Lock()


def _versao(t):
    return tuple(int(x) for x in re.findall(r"\d+", t or "")[:3]) or (0,)


def pode_atualizar():
    """Só o aplicativo instalado se atualiza (rodando pela pasta, quem atualiza é o código)."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("GW_ATUALIZAR_TESTE") == "1"


def _arquivo_certo(assets):
    if sys.platform == "darwin":
        chave = "AppleSilicon" if platform.machine() == "arm64" else "Intel"
        return next((a for a in assets if a["name"].endswith(".dmg") and chave in a["name"]), None)
    if os.name == "nt":
        return next((a for a in assets if a["name"].endswith("-Instalador-Windows.exe")), None)
    return None


def verificar(versao_atual, forcar=False):
    if not pode_atualizar() or estado["baixando"]:
        return estado
    if not forcar and time.time() - estado["verificado"] < INTERVALO:
        return estado
    with _trava:
        estado["verificado"] = time.time()
        try:
            req = urllib.request.Request(f"{API}/repos/{REPO}/releases/latest", headers=AGENTE)
            with urllib.request.urlopen(req, timeout=12) as r:
                rel = json.load(r)
            tag, arq = rel.get("tag_name", ""), _arquivo_certo(rel.get("assets", []))
            if arq and _versao(tag) > _versao(versao_atual):
                estado["disponivel"] = {"versao": tag.lstrip("vV"), "notas": (rel.get("body") or "")[:3000],
                                        "url": arq["browser_download_url"], "nome": arq["name"], "tamanho": arq.get("size", 0)}
            else:
                estado["disponivel"] = None
            estado["erro"] = ""
        except Exception as ex:   # sem internet, GitHub fora do ar...: tenta de novo mais tarde
            estado["erro"] = str(ex)[:200]
    return estado


def resumo():
    d = estado["disponivel"]
    return {"pode": pode_atualizar(), "versao": d["versao"] if d else "", "notas": d["notas"] if d else "",
            "tamanho": d["tamanho"] if d else 0, "baixando": estado["baixando"], "aplicando": estado["aplicando"],
            "progresso": round(estado["progresso"], 2), "erro": estado["erro"] if estado["baixando"] is False else ""}


def vigiar(versao_atual):
    """Procura atualização ao abrir e depois a cada 6 horas."""
    time.sleep(8)
    while True:
        verificar(versao_atual)
        time.sleep(600)


def instalar(sair):
    d = estado["disponivel"]
    if not d or estado["baixando"]:
        return False
    estado.update(baixando=True, progresso=0.0, aplicando=False, erro="")

    def trabalho():
        try:
            pasta = tempfile.mkdtemp(prefix="gw_atualizacao_")
            arq = os.path.join(pasta, os.path.basename(d["nome"]))
            req = urllib.request.Request(d["url"], headers={"User-Agent": AGENTE["User-Agent"]})
            with urllib.request.urlopen(req, timeout=60) as r, open(arq, "wb") as fh:
                total = int(r.headers.get("Content-Length") or d["tamanho"] or 0)
                feito = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    fh.write(b)
                    feito += len(b)
                    estado["progresso"] = min(0.99, feito / total) if total else 0
            if total and feito != total:
                raise RuntimeError("o download veio incompleto")
            estado.update(progresso=1.0, aplicando=True)
            if os.name == "nt":
                _aplicar_windows(arq)
            elif sys.platform == "darwin":
                _aplicar_mac(arq, pasta)
            else:
                raise RuntimeError("sistema sem atualização automática")
            time.sleep(1.5)
            sair()
        except Exception as ex:
            estado.update(baixando=False, aplicando=False, erro=f"Não consegui atualizar: {ex}"[:300])

    threading.Thread(target=trabalho, daemon=True).start()
    return True


def _aplicar_windows(instalador):
    # fora do "grupo" do app (senão o Windows fecha o instalador junto com o app)
    DETACHED, NOVO_GRUPO, SAIR_DO_JOB = 0x00000008, 0x00000200, 0x01000000
    args = [instalador, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART", "/ABRIR=1"]
    try:
        subprocess.Popen(args, creationflags=DETACHED | NOVO_GRUPO | SAIR_DO_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(args, creationflags=DETACHED | NOVO_GRUPO, close_fds=True)


def _aplicar_mac(dmg, pasta):
    app_atual = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
    if not app_atual.endswith(".app"):
        raise RuntimeError("não achei o aplicativo instalado")
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
    script = os.path.join(pasta, "atualizar.sh")
    with open(script, "w") as fh:
        fh.write(f"""#!/bin/bash
# espera o app fechar, troca pelo novo e abre de novo
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
MNT=$(mktemp -d)
hdiutil attach -nobrowse -quiet -mountpoint "$MNT" {q(dmg)} || {{ open {q(app_atual)}; exit 1; }}
NOVO=$(ls -d "$MNT"/*.app | head -1)
APP={q(app_atual)}
rm -rf "$APP.velho"
if mv "$APP" "$APP.velho" && ditto "$NOVO" "$APP"; then rm -rf "$APP.velho"; else rm -rf "$APP"; mv "$APP.velho" "$APP"; fi
hdiutil detach -quiet "$MNT"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
open "$APP"
rm -rf {q(pasta)}
""")
    subprocess.Popen(["/bin/bash", script], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
