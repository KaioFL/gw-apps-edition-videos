"""GW Apps Edition Videos — aplicativo de janela (Windows e Mac).

Abre o editor numa janela própria do programa: sem navegador, sem janela preta e
sem instalar Python. Tudo o que o editor precisa (ffmpeg, leitor de fala, fontes)
vem dentro do aplicativo.

Onde ficam os arquivos de quem usa:
  - Vídeos e músicas (pasta visível):  Vídeos/GW Apps Edition Videos   (Mac: Filmes/GW Apps Edition Videos)
  - Ajustes, prévias e avatares:        Windows: %LOCALAPPDATA%/GW Apps Edition Videos
                                        Mac: ~/Library/Application Support/GW Apps Edition Videos
Dá para trocar tudo pelas variáveis GW_DADOS, GW_VIDEOS e GW_MUSICAS.
"""
import os
import sys
import json
import time
import shutil
import socket
import threading
import traceback
import urllib.request

NOME = "GW Apps Edition Videos"
ID_APP = "GWApps.EditionVideos"
PORTA_INICIAL = 8790


# ------------------------------------------------------------------ pastas
def pasta_programa():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "programa"))


def _pasta_conhecida_windows(guid):
    """Pasta do sistema pelo identificador (respeita quando a pessoa mudou 'Vídeos' de lugar)."""
    try:
        import ctypes
        import uuid
        g = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
            return None
        valor = p.value
        ctypes.windll.ole32.CoTaskMemFree(p)
        return valor
    except Exception:
        return None


def pasta_dados():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, NOME)


def pasta_visivel():
    if sys.platform == "win32":
        base = _pasta_conhecida_windows("18989B1D-99B5-455B-841C-AB7C74E4DDFC") or os.path.expanduser("~/Videos")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Movies")
    else:
        base = os.path.expanduser("~/Videos")
    if not os.path.isdir(base):
        base = os.path.expanduser("~")
    return os.path.join(base, NOME)


# ------------------------------------------------------------------ registro (sem janela preta, os avisos vão para um arquivo)
def mandar_saida_para_arquivo(dados):
    pasta = os.path.join(dados, "logs")
    os.makedirs(pasta, exist_ok=True)
    arq = os.path.join(pasta, "gw-app.log")
    try:
        if os.path.isfile(arq) and os.path.getsize(arq) > 5 * 1024 * 1024:
            os.replace(arq, arq + ".anterior")
    except OSError:
        pass
    fh = open(arq, "a", encoding="utf-8", buffering=1, errors="replace")
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} abrindo {NOME} =====\n")
    sys.stdout = sys.stderr = fh
    return arq


# ------------------------------------------------------------------ uma cópia só
def _resposta(porta, caminho, dados=None, espera=1.5):
    req = urllib.request.Request(f"http://127.0.0.1:{porta}{caminho}", data=dados,
                                 headers={"Content-Type": "application/json"} if dados is not None else {})
    with urllib.request.urlopen(req, timeout=espera) as r:
        return json.loads(r.read() or b"{}")


def _nosso_app_em(porta):
    try:
        return _resposta(porta, "/api/ping").get("app") == NOME
    except Exception:
        return False


def _porta_livre(porta):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
        s.bind(("127.0.0.1", porta))
        return True
    except OSError:
        return False
    finally:
        s.close()


def escolher_porta():
    """Devolve a porta para abrir. Se o editor já está aberto, traz a janela dele para a frente e devolve None."""
    for porta in range(PORTA_INICIAL, PORTA_INICIAL + 10):
        if _nosso_app_em(porta):
            try:
                _resposta(porta, "/api/janela/focar", b"{}")
            except Exception:
                pass
            return None
        if _porta_livre(porta):
            return porta
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


# ------------------------------------------------------------------ ao fechar, fecha junto o que estava rodando (ffmpeg)
_job = None


def fechar_filhos_junto():
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class Basico(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RT", "WT", "OT")]

        class Estendido(ctypes.Structure):
            _fields_ = [("Basico", Basico), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        info = Estendido()
        info.Basico.LimitFlags = 0x2000 | 0x800  # KILL_ON_JOB_CLOSE + BREAKAWAY_OK (o instalador da atualização sai do grupo)
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            _job = job
    except Exception:
        traceback.print_exc()


def encerrar_filhos():
    if os.name != "nt":
        try:
            import signal
            if os.getpgrp() == os.getpid():
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                os.killpg(os.getpgrp(), signal.SIGTERM)
        except Exception:
            pass


# ------------------------------------------------------------------ janela
TEXTOS = {
    "global.quitConfirmation": "Ainda tem vídeo sendo editado. Se fechar agora, a edição para no meio.\n\nFechar mesmo assim?",
    "global.ok": "OK", "global.quit": "Fechar", "global.cancel": "Cancelar", "global.saveFile": "Salvar arquivo",
    "cocoa.menu.about": "Sobre o", "cocoa.menu.services": "Serviços", "cocoa.menu.view": "Visualizar",
    "cocoa.menu.edit": "Editar", "cocoa.menu.hide": "Ocultar", "cocoa.menu.hideOthers": "Ocultar Outros",
    "cocoa.menu.showAll": "Mostrar Tudo", "cocoa.menu.quit": "Encerrar", "cocoa.menu.fullscreen": "Entrar em Tela Cheia",
    "cocoa.menu.cut": "Recortar", "cocoa.menu.copy": "Copiar", "cocoa.menu.paste": "Colar",
    "cocoa.menu.selectAll": "Selecionar Tudo",
    "windows.fileFilter.allFiles": "Todos os arquivos", "windows.fileFilter.otherFiles": "Outros tipos de arquivo",
}


def tamanho_janela(webview):
    try:
        tela = webview.screens[0]
        return max(1000, min(1500, int(tela.width * 0.92))), max(680, min(960, int(tela.height * 0.88)))
    except Exception:
        return 1360, 860


def abrir_janela(app, url, dados):
    import webview
    webview.settings["ALLOW_DOWNLOADS"] = True              # baixar a legenda .srt e o texto das transcrições
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    largura, altura = tamanho_janela(webview)
    janela = webview.create_window(NOME, url, width=largura, height=altura, min_size=(1000, 660),
                                   background_color="#000000", text_select=True, localization=TEXTOS)

    def trazer_para_frente():
        try:
            janela.restore()
            janela.show()
            janela.on_top = True
            time.sleep(0.4)
            janela.on_top = False
        except Exception:
            traceback.print_exc()

    def vigiar_fila():
        # só pergunta "fechar mesmo assim?" quando tem edição em andamento
        while True:
            try:
                janela.confirm_close = app.ocupado()
            except Exception:
                pass
            time.sleep(0.7)

    def escolher_pasta(inicial):
        # "Escolher pasta" do próprio sistema (Explorador no Windows, Finder no Mac), para achar o SSD externo
        try:
            tipo = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
            r = janela.create_file_dialog(tipo, directory=inicial or "")
        except Exception:
            traceback.print_exc()
            return None
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else r

    app.ao_pedir_foco = trazer_para_frente
    app.ao_escolher_pasta = escolher_pasta

    def sair_para_atualizar():
        # a atualização baixou: fecha o app sem perguntar (o instalador/script abre a versão nova)
        try:
            janela.confirm_close = False
            janela.destroy()
        except Exception:
            traceback.print_exc()
        time.sleep(3)
        os._exit(0)

    app.ao_sair = sair_para_atualizar
    threading.Thread(target=vigiar_fila, daemon=True).start()
    webview.start(private_mode=False, storage_path=os.path.join(dados, "janela"),
                  debug=os.environ.get("GW_DEBUG") == "1")


def abrir_no_navegador(app, url):
    """Plano B (ex.: Windows sem o WebView2): abre no navegador e fica aberto enquanto a página estiver em uso."""
    import webbrowser
    webbrowser.open(url)
    ultimo = [time.time()]
    antigo = app.Painel._host_ok

    def marcar(self):
        ultimo[0] = time.time()
        return antigo(self)
    app.Painel._host_ok = marcar
    while app.ocupado() or time.time() - ultimo[0] < 300:
        time.sleep(5)


def criar_atalhos_windows(dados):
    """Versão portátil (sem instalador): cria, uma vez só, os atalhos na Área de Trabalho e no Menu Iniciar."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    exe = sys.executable
    pasta_exe = os.path.dirname(exe)
    marca = os.path.join(dados, ".atalhos_criados")
    if os.path.isfile(os.path.join(pasta_exe, "unins000.exe")) or os.path.isfile(marca):
        return      # instalado pelo instalador (ele já criou os atalhos) ou já criados antes
    import subprocess
    q = lambda s: s.replace("'", "''")  # noqa: E731
    destinos = [_pasta_conhecida_windows("B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),    # Área de Trabalho
                _pasta_conhecida_windows("A77F5D77-2E2B-44C3-A6A2-ABA601054A51")]    # Menu Iniciar > Programas
    comandos = []
    for d in destinos:
        if d and os.path.isdir(d):
            lnk = os.path.join(d, NOME + ".lnk")
            comandos.append(f"$s=$w.CreateShortcut('{q(lnk)}');$s.TargetPath='{q(exe)}';"
                            f"$s.WorkingDirectory='{q(pasta_exe)}';$s.IconLocation='{q(exe)},0';"
                            f"$s.Description='Editor automático de vídeos';$s.Save()")
    if not comandos:
        return
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        "$w=New-Object -ComObject WScript.Shell;" + ";".join(comandos)],
                       capture_output=True, timeout=60, creationflags=0x08000000)
        with open(marca, "w") as fh:
            fh.write(exe)
    except Exception:
        traceback.print_exc()


def aviso(msg):
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, NOME, 0x10)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["osascript", "-e", f'display alert "{NOME}" message {json.dumps(msg)} as critical'])
    except Exception:
        pass


# ------------------------------------------------------------------ início
def main():
    dados = os.path.abspath(os.environ.get("GW_DADOS") or pasta_dados())
    visivel = pasta_visivel()
    os.makedirs(dados, exist_ok=True)
    if getattr(sys, "frozen", False) or sys.stdout is None:
        mandar_saida_para_arquivo(dados)

    if os.name == "nt" and not getattr(sys, "frozen", False):
        try:   # rodando pelo Python: mostra o ícone do editor na barra de tarefas (e não o do Python)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ID_APP)
        except Exception:
            pass

    porta = escolher_porta()
    if porta is None:
        return      # já estava aberto: a janela dele veio para a frente

    os.environ.setdefault("GW_DADOS", dados)
    os.environ.setdefault("GW_VIDEOS", os.path.join(visivel, "Meus videos"))
    os.environ.setdefault("GW_MUSICAS", os.path.join(visivel, "Minhas musicas"))
    os.environ["GW_PORTA"] = str(porta)
    os.environ["GW_SEM_NAVEGADOR"] = "1"

    prog = pasta_programa()
    musicas = os.environ["GW_MUSICAS"]
    if not os.path.isdir(musicas):
        os.makedirs(musicas, exist_ok=True)
        leia = os.path.join(prog, "musicas", "COLOQUE_SUAS_MUSICAS_AQUI.txt")
        if os.path.isfile(leia):
            shutil.copy2(leia, musicas)

    sys.path.insert(0, prog)
    fechar_filhos_junto()
    try:
        import app
        srv = app.iniciar_servidor()
    except Exception:
        traceback.print_exc()
        aviso("O editor não conseguiu abrir. Feche e tente de novo.\n\n"
              f"Detalhes no arquivo: {os.path.join(dados, 'logs', 'gw-app.log')}")
        sys.exit(1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=criar_atalhos_windows, args=(dados,), daemon=True).start()
    url = f"http://127.0.0.1:{porta}/"
    print(f"{NOME} {app.VERSAO} em {url}")
    print(f"  dados: {dados}\n  vídeos: {os.environ['GW_VIDEOS']}\n  músicas: {musicas}")

    try:
        abrir_janela(app, url, dados)
    except Exception:
        traceback.print_exc()
        print("Não deu para abrir a janela própria. Abrindo no navegador.")
        abrir_no_navegador(app, url)
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
        encerrar_filhos()
    os._exit(0)


if __name__ == "__main__":
    main()
