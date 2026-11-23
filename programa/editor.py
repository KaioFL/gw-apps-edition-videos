"""GW Apps Edition Videos — motor de edição.

Tudo roda no próprio computador, sem enviar nada para a internet:
legenda palavra por palavra, corte de silêncio, zoom lento, J-cut, cenas repetidas,
volume da voz igualado, música de fundo, headline e transcrição de arquivos longos.

Precisa de Python 3.10+, numpy, pillow, sherpa-onnx e ffmpeg com libass
(se o ffmpeg do sistema não servir, usa o pacote static-ffmpeg).

Uso pela linha de comando:  python editor.py <pasta-do-video> [opcoes.json]
"""
import os
import re
import sys
import json
import glob
import time
import shutil
import difflib
import tarfile
import tempfile
import threading
import subprocess
import urllib.request

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PASTA_FONTES = os.path.join(BASE, "fontes")
PASTA_MUSICAS = os.environ.get("GW_MUSICAS") or os.path.join(BASE, "musicas")
# ffmpeg que vem dentro do aplicativo instalado (pasta "bin"); rodando pela pasta, usa o do sistema ou o static-ffmpeg
PASTA_BIN = os.environ.get("GW_FFMPEG_DIR") or os.path.join(BASE, "bin")
MODELO = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
URL_MODELO = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODELO}.tar.bz2"
EXT_VIDEO = (".mp4", ".mov", ".webm", ".m4v", ".mkv")
EXT_AUDIO = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
SEM_JANELA = {"creationflags": 0x08000000} if os.name == "nt" else {}
SR = 16000          # taxa usada para ouvir/transcrever
CAPA = 0.10         # primeiros quadros sem legenda, para servir de capa
LADO_MAX = 1920      # maior lado do vídeo final
ALVO_VOZ_DB = -15.5 # volume da fala depois de igualar (≈ -16 LUFS, padrão de redes sociais)

PADRAO = {
    # legenda
    "legenda": True, "leg_palavras": 3, "leg_posicao": "baixo", "leg_fonte": "Montserrat ExtraBold",
    "leg_tamanho": 84, "leg_cor": "#FFFFFF", "leg_cor_ativa": "#FFD84D", "leg_cor_chave": "#B57BFF",
    "leg_maiusculas": True, "leg_pop": True, "palavras_chave": "", "leg_correcoes": "",
    # silêncio
    "cortar_silencio": True,
    # música
    "musica": True, "musica_id": "@pulso", "musica_volume": -28, "musica_modo": "mesma",
    # montagem
    "zoom_modo": "lento", "velocidade": 1.0, "jcut": True, "jcut_quadros": 8, "pular_repetidas": True, "igualar_voz": True,
    "capa_limpa": True, "qualidade": "normal",
    # headline
    "headline": False, "hl_fonte": "Montserrat ExtraBold", "hl_tamanho": 68, "hl_cor": "#FFFFFF",
    "hl_y": 18, "hl_x": 0, "hl_margem": 80, "hl_duracao": 0,
    "hl_fundo": False, "hl_fundo_cor": "#7C3AED", "hl_fundo_opac": 90,
    "hl_contorno": True, "hl_contorno_cor": "#000000", "hl_contorno_esp": 5,
    "hl_sombra": True, "hl_sombra_opac": 60,
    # CTA (mesmo visual da headline, com tempo para aparecer e sumir)
    "cta": False, "cta_fonte": "Montserrat ExtraBold", "cta_tamanho": 62, "cta_cor": "#FFFFFF",
    "cta_y": 57, "cta_x": 0, "cta_margem": 80, "cta_antes_fim": 4,
    "cta_fundo": True, "cta_fundo_cor": "#7C3AED", "cta_fundo_opac": 95,
    "cta_contorno": True, "cta_contorno_cor": "#000000", "cta_contorno_esp": 5,
    "cta_sombra": True, "cta_sombra_opac": 60,
}
PADRAO_SILENCIO = {"modo": "remover", "respiro_ms": 250, "abaixo_fala_db": 18, "minimo_s": 0.3, "folga_ms": 50}
CAUDA_S, ATAQUE_S = 0.15, 0.08   # quanto do finzinho / do comecinho das palavras é sempre protegido

# fontes da legenda: nome no painel -> (arquivo, nome da família para o libass, negrito)
FONTES_LEGENDA = {
    "Montserrat ExtraBold": ("Montserrat-ExtraBold.ttf", "Montserrat ExtraBold", 0),
    "Montserrat Black": ("Montserrat-Black.ttf", "Montserrat Black", 0),
    "Poppins Bold": ("Poppins-Bold.ttf", "Poppins", -1),
    "Anton": ("Anton-Regular.ttf", "Anton", 0),
    "Bebas Neue": ("BebasNeue-Regular.ttf", "Bebas Neue", 0),
    "Inter": ("Inter-SemiBold.otf", "Inter SemiBold", 0),
}
# fontes da headline (desenhada como imagem)
FONTES_HEADLINE = {
    "Montserrat ExtraBold": "Montserrat-ExtraBold.ttf",
    "Montserrat Black": "Montserrat-Black.ttf",
    "Poppins Bold": "Poppins-Bold.ttf",
    "Poppins Medium": "Poppins-Medium.ttf",
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Inter": "Inter-SemiBold.otf",
    "Lora (serifa)": "Lora-Variable.ttf",
}


# =================================================================== ferramentas
def _caminho_curto(p):
    """Nome curto (8.3) do Windows, que não tem acento. Devolve o próprio caminho se não der."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)
        return buf.value if 0 < n < 1024 else p
    except Exception:
        return p


_modelo_copiado = threading.Lock()


def pasta_modelos():
    p = os.environ.get("GW_MODELOS") or os.path.join(BASE, "modelos")
    # o leitor de fala não abre caminhos com acento no Windows (ex.: usuário "João")
    if os.name == "nt" and not p.isascii():
        curto = _caminho_curto(p) if os.path.isdir(p) else p
        if curto.isascii():
            return curto
        pub = os.path.join(os.environ.get("PUBLIC") or r"C:\Users\Public", "GWEditionVideos", "modelos")
        # o aplicativo já traz o leitor de fala: copia para a pasta Pública em vez de baixar de novo
        origem, destino = os.path.join(p, MODELO), os.path.join(pub, MODELO)
        with _modelo_copiado:
            if os.path.isfile(os.path.join(origem, "tokens.txt")) and not os.path.isfile(os.path.join(destino, "tokens.txt")):
                try:
                    tmp = destino + ".copiando"
                    shutil.rmtree(tmp, ignore_errors=True)
                    shutil.copytree(origem, tmp)
                    shutil.rmtree(destino, ignore_errors=True)
                    os.replace(tmp, destino)
                except OSError:
                    pass
        p = pub
    return p


def _tem_libass(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True,
                             timeout=60, **SEM_JANELA).stdout
    except Exception:
        return False
    return re.search(r"^\s*\S+\s+ass\s", out, re.M) is not None


_bins = {}
_bins_lock = threading.Lock()


def ffbin(nome):
    """Caminho do ffmpeg/ffprobe. Prefere o do sistema se ele tiver legendas (libass); senão usa o static-ffmpeg."""
    with _bins_lock:
        if nome in _bins:
            return _bins[nome]
        ext = ".exe" if os.name == "nt" else ""
        proprio = [os.path.join(PASTA_BIN, n + ext) for n in ("ffmpeg", "ffprobe")]
        if all(os.path.isfile(x) for x in proprio):
            _bins["ffmpeg"], _bins["ffprobe"] = proprio
            return _bins[nome]
        ffm = shutil.which("ffmpeg")
        ffp = shutil.which("ffprobe")
        if not (ffm and ffp and _tem_libass(ffm)):
            try:
                from static_ffmpeg import run as _sf
                ffm, ffp = _sf.get_or_fetch_platform_executables_else_raise()
            except Exception as ex:
                if not (ffm and ffp):
                    raise RuntimeError("Não achei o ffmpeg. Feche e abra o programa de novo para reinstalar. "
                                       f"Detalhe: {ex}")
        _bins["ffmpeg"], _bins["ffprobe"] = ffm, ffp
        return _bins[nome]


def trocar(origem, destino, mover=False, tentativas=20):
    """os.replace / shutil.move com novas tentativas: no Windows, um arquivo aberto
    (por exemplo, tocando no painel) não pode ser renomeado por alguns instantes."""
    for i in range(tentativas):
        try:
            if mover:
                shutil.move(origem, destino)
            else:
                os.replace(origem, destino)
            return
        except PermissionError:
            if i == tentativas - 1:
                raise RuntimeError("O Windows não deixou trocar o arquivo porque ele está aberto em outro "
                                   "programa. Feche o vídeo e tente de novo.")
            time.sleep(0.5)


def ff(*args, cwd=None):
    """Roda o ffmpeg e, se der erro, devolve a mensagem dele."""
    p = subprocess.run([ffbin("ffmpeg"), "-hide_banner", "-v", "error", "-y", *args], cwd=cwd,
                       capture_output=True, **SEM_JANELA)
    if p.returncode != 0:
        msg = p.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError("ffmpeg: " + (" | ".join(msg[-4:]) or "erro desconhecido"))
    return p


_sondas = {}


def sondar(arq):
    """Duração, tamanho de exibição (já considerando rotação), fps e se tem áudio."""
    try:
        chave = (arq, os.path.getmtime(arq), os.path.getsize(arq))
    except OSError:
        chave = None
    if chave and chave in _sondas:
        return _sondas[chave]
    p = subprocess.run([ffbin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", arq],
                       capture_output=True, **SEM_JANELA)
    j = json.loads(p.stdout or b"{}")
    sts = j.get("streams", [])
    v = next((s for s in sts if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    info = {"dur": 0.0, "w": 0, "h": 0, "fps": 30.0, "audio": any(s.get("codec_type") == "audio" for s in sts)}
    try:
        info["dur"] = float(j.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    except ValueError:
        pass
    if v:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = rot or int(float((v.get("tags") or {}).get("rotate", 0) or 0))
        if abs(rot) % 180 == 90:
            w, h = h, w
        info["w"], info["h"] = w, h
        try:
            a, b = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
            fps = float(a) / float(b or 1)
            if 15 <= fps <= 121:
                info["fps"] = fps
        except Exception:
            pass
    if chave:
        _sondas[chave] = info
    return info


# a amostra 0 do áudio cai no tempo 0 do arquivo (mesmo relógio da imagem), mesmo quando o som
# do arquivo começa um pouco depois ou tem buracos: é isso que mantém som e imagem juntos
ALINHAR = "aresample=async=1:first_pts=0"
SR_SAIDA = 48000


def ler_audio(arq):
    """Áudio mono 16 kHz em float32 (vazio se o arquivo não tem som)."""
    p = subprocess.run([ffbin("ffmpeg"), "-v", "error", "-i", arq, "-vn", "-af", ALINHAR, "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True, **SEM_JANELA)
    if p.returncode != 0 and not p.stdout:
        return np.zeros(0, np.float32)
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768


def ler_audio_estereo(arq):
    """Áudio estéreo 48 kHz em 16 bits, formato (amostras, 2). Para cortar com precisão de amostra
    (16 bits: metade da memória, sem diferença audível para um som que já veio em AAC)."""
    p = subprocess.run([ffbin("ffmpeg"), "-v", "error", "-i", arq, "-vn", "-af", ALINHAR, "-ac", "2",
                        "-ar", str(SR_SAIDA), "-f", "s16le", "-"], capture_output=True, **SEM_JANELA)
    if p.returncode != 0 and not p.stdout:
        return np.zeros((0, 2), np.int16)
    dados = np.frombuffer(p.stdout, np.int16)
    return dados[: len(dados) // 2 * 2].reshape(-1, 2)


def verificar():
    """Confere se tudo que a edição precisa está instalado."""
    import sherpa_onnx  # noqa: F401
    import PIL  # noqa: F401
    f = ffbin("ffmpeg")
    ffbin("ffprobe")
    if not _tem_libass(f):
        raise RuntimeError("O ffmpeg encontrado não sabe desenhar legendas (falta o libass).")
    if not os.path.isfile(os.path.join(pasta_modelos(), MODELO, "tokens.txt")):
        raise RuntimeError("O leitor de fala ainda não foi baixado.")
    print("  ffmpeg:", f)
    print("  leitor de fala:", os.path.join(pasta_modelos(), MODELO))
    return True


# ============================================================ leitor de fala
def baixar_modelo(log=print):
    destino = os.path.join(pasta_modelos(), MODELO)
    if os.path.isfile(os.path.join(destino, "tokens.txt")):
        return destino
    os.makedirs(pasta_modelos(), exist_ok=True)
    tmp = destino + ".tar.bz2.parcial"
    log("Baixando o leitor de fala (~490 MB, só na primeira vez)…")
    ultimo = [0]

    def progresso(blocos, tam, total):
        if total > 0:
            pct = int(100 * blocos * tam / total)
            if pct >= ultimo[0] + 5:
                ultimo[0] = pct
                log(f"  {min(pct, 100)}%")
    urllib.request.urlretrieve(URL_MODELO, tmp, progresso)
    log("Descompactando…")
    extrair = tempfile.mkdtemp(prefix="gw_modelo_", dir=pasta_modelos())
    with tarfile.open(tmp, "r:bz2") as t:
        membros = [m for m in t.getmembers() if not m.name.startswith(("/", "..")) and ".." not in m.name.split("/")]
        t.extractall(extrair, members=membros)
    origem = os.path.join(extrair, MODELO)
    if os.path.isdir(destino):
        shutil.rmtree(destino, ignore_errors=True)
    shutil.move(origem, destino)
    shutil.rmtree(extrair, ignore_errors=True)
    os.remove(tmp)
    shutil.rmtree(os.path.join(destino, "test_wavs"), ignore_errors=True)
    return destino


_rec = None
_rec_lock = threading.Lock()


def reconhecedor(log=print):
    global _rec
    with _rec_lock:
        if _rec is None:
            import sherpa_onnx
            p = baixar_modelo(log)
            j = lambda n: os.path.join(p, n)  # noqa: E731
            _rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=j("encoder.int8.onnx"), decoder=j("decoder.int8.onnx"), joiner=j("joiner.int8.onnx"),
                tokens=j("tokens.txt"), model_type="nemo_transducer",
                num_threads=max(1, min(8, (os.cpu_count() or 4) - 1)))
        return _rec


def _decodificar(a, desloc=0.0):
    rec = reconhecedor()
    with _rec_lock:
        s = rec.create_stream()
        s.accept_waveform(SR, a)
        rec.decode_stream(s)
        toks, tempos = list(s.result.tokens), list(s.result.timestamps)
    pal = []
    for tok, t in zip(toks, tempos):
        if tok.startswith(("▁", " ")) or not pal:
            pal.append([tok.lstrip("▁ "), float(t) + desloc])
        else:
            pal[-1][0] += tok
    return [(w.strip(), t) for w, t in pal if w.strip()]


def _pontos_de_corte(a, alvo=24.0, janela=6.0):
    """Divide áudios longos em pedaços de ~24 s, sempre no trecho mais silencioso (não parte palavras)."""
    n = len(a)
    fr = int(0.02 * SR)
    if n <= (alvo + janela) * SR:
        return [0, n]
    e = np.sqrt(np.convolve(a.astype(np.float32) ** 2, np.ones(fr) / fr, "same")[::fr])
    pts = [0]
    pos = 0
    while n - pos > (alvo + janela) * SR:
        lo = int((pos + (alvo - janela / 2) * SR) / fr)
        hi = int((pos + (alvo + janela / 2) * SR) / fr)
        k = lo + int(np.argmin(e[lo:hi])) if hi > lo else lo
        pos = max(pos + SR, k * fr)
        pts.append(pos)
    pts.append(n)
    return pts


def transcrever_audio(a, log=None):
    """Lista de palavras {w, s, e} com tempos em segundos."""
    if not len(a):
        return []
    pts = _pontos_de_corte(a)
    brutas = []
    for x, y in zip(pts, pts[1:]):
        seg = a[x:y]
        if len(seg) < 0.3 * SR or np.abs(seg).max() < 0.004:
            continue
        if log and len(pts) > 2:
            log(f"Transcrevendo… {int(100 * x / len(a))}%")
        brutas += _decodificar(seg, x / SR)
    D = len(a) / SR
    out = []
    for i, (w, t) in enumerate(brutas):
        prox = brutas[i + 1][1] if i + 1 < len(brutas) else D
        out.append({"w": w, "s": round(t, 2), "e": round(max(t + 0.12, min(prox, t + 0.9, D)), 2)})
    return out


def _normal(t):
    return " ".join(re.sub(r"[^\w' ]", " ", t.lower().replace("’", "'")).split())


def _sem_gaguejo(palavras):
    """Tira a mesma palavra curta repetida em seguida ('eu eu acho')."""
    out = []
    for p in palavras:
        if out and len(_normal(p["w"])) <= 3 and _normal(p["w"]) == _normal(out[-1]["w"]) and p["s"] - out[-1]["s"] < 0.8:
            out[-1]["e"] = p["e"]
            continue
        out.append(dict(p))
    return out


def _db_quadros(a):
    fr = SR // 100
    n = len(a) // fr
    if n == 0:
        return np.zeros(0)
    return 20 * np.log10(np.sqrt((a[: n * fr].reshape(n, fr) ** 2).mean(1)) + 1e-9)


def nivel_quadros_db(a):
    """Volume (dB) a cada 10 ms, suavizado em 30 ms: o chiado de fundo vira uma linha estável."""
    fr = SR // 100
    n = len(a) // fr
    if n == 0:
        return np.zeros(0)
    e = (a[: n * fr].reshape(n, fr) ** 2).mean(1)
    e = np.convolve(e, np.ones(3) / 3, "same")
    return 10 * np.log10(e + 1e-12)


def limiar_silencio(db, abaixo=18.0):
    """Limiar de silêncio da própria cena: `abaixo` dB abaixo do volume da fala.
    Pega o silêncio e os barulhos baixos (ambiente, chiado). None se a cena não tem pausa clara."""
    if not len(db):
        return None
    piso = float(np.percentile(db, 5))
    fala = float(np.percentile(db, 95))
    if fala - piso < 10:
        return None
    return max(fala - float(abaixo), piso + 3)


def limites_fala(a, palavras, dur):
    """Início e fim do que tem som na cena (só a linha reta das pontas sai). Ancora nas palavras."""
    if not len(a) or not palavras:
        return 0.0, dur
    db = nivel_quadros_db(a)
    n = len(db)
    thr = limiar_silencio(db)
    if not n or thr is None:
        return max(0.0, palavras[0]["s"] - 0.3), min(dur, palavras[-1]["e"] + 0.4)
    ativo = db > thr - 10      # o finzinho das palavras conta como som
    i0 = min(n - 1, int(palavras[0]["s"] * 100))
    i1 = min(n - 1, int(palavras[-1]["s"] * 100))
    # para trás: enquanto houver qualquer som; para no primeiro trecho de 0,25 s de linha reta
    k, ultimo, buraco = i0, i0, 0
    while k > 0:
        k -= 1
        if ativo[k]:
            ultimo, buraco = k, 0
        else:
            buraco += 1
            if buraco > 25:
                break
    ini = ultimo * 0.01
    # para frente: idem, a partir da última palavra
    k, ultimo, buraco = i1, i1, 0
    while k < n:
        if ativo[k]:
            ultimo, buraco = k, 0
        else:
            buraco += 1
            if buraco > 25:
                break
        k += 1
    fim = max((ultimo + 1) * 0.01, palavras[-1]["e"])
    return max(0.0, ini), min(dur, fim)


def ajustar_fins(palavras, a):
    """Fim de cada palavra pelo som: a legenda não fica parada na tela durante uma pausa."""
    if not palavras or not len(a):
        return palavras
    db = _db_quadros(a)
    n = len(db)
    if not n:
        return palavras
    ativo = db > max(db.max() - 26, -55)
    for i, p in enumerate(palavras):
        k = min(n - 1, int(p["s"] * 100) + 8)
        lim = min(n, int(p["e"] * 100))
        buraco = 0
        ultimo = k
        while k < lim:
            if ativo[k]:
                ultimo, buraco = k, 0
            else:
                buraco += 1
                if buraco > 15:
                    break
            k += 1
        if k < lim:
            p["e"] = round(max(p["s"] + 0.15, (ultimo + 1) * 0.01 + 0.05), 2)
    return palavras


def nivel_fala_db(a):
    """Volume médio (dBFS) só dos trechos com fala."""
    db = _db_quadros(a)
    if not len(db):
        return None
    alto = db[db > db.max() - 20]
    if not len(alto):
        return None
    return float(10 * np.log10(np.mean(10 ** (alto / 10))))


# ============================================== transcrição guardada e correções
# A transcrição é feita UMA vez, sobre o original de cada cena (raw/ se a cena foi cortada), e fica
# guardada em _trabalho/palavras.json com as correções que você fizer no painel. A cada edição os
# tempos são levados pelos cortes da cena. Assim corrigir a legenda ou mudar o corte não refaz a escuta.
_cache_trava = threading.Lock()


def _arq_palavras(pasta):
    return os.path.join(pasta, "_trabalho", "palavras.json")


def _ler_palavras(pasta):
    try:
        with open(_arq_palavras(pasta), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _gravar_palavras(pasta, dados):
    os.makedirs(os.path.join(pasta, "_trabalho"), exist_ok=True)
    tmp = _arq_palavras(pasta) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False)
    os.replace(tmp, _arq_palavras(pasta))


def _assinatura(arq):
    st = os.stat(arq)
    return f"{st.st_size}-{int(st.st_mtime)}"


def transcricao_da_cena(pasta, nome, log=None):
    """{"asr": palavras ouvidas, "palavras": palavras da legenda (com correções), "corrigida": bool},
    com tempos do ORIGINAL da cena. Escuta só se ainda não escutou esse arquivo."""
    orig = fonte_do_corte(pasta, nome)
    sig = _assinatura(orig)
    with _cache_trava:
        ent = _ler_palavras(pasta).get(nome)
    if ent and ent.get("sig") == sig:
        return ent
    if log:
        log("Ouvindo a fala…")
    a = ler_audio(orig)
    pal = ajustar_fins(_sem_gaguejo(transcrever_audio(a)), a) if len(a) else []
    ent = {"sig": sig, "asr": pal, "palavras": pal, "corrigida": False}
    with _cache_trava:
        dados = _ler_palavras(pasta)
        dados[nome] = ent
        _gravar_palavras(pasta, dados)
    return ent


def mapear_pelos_cortes(palavras, cortes, manter=None):
    """Leva os tempos do original para a cena cortada; palavra que caiu dentro de um corte sai.
    `manter` (trechos mantidos, já nos quadros exatos do corte) é usado quando existe: é o que
    realmente ficou no vídeo. Sem ele, calcula pelos `cortes` (cenas cortadas em versões antigas)."""
    if manter:
        trechos = sorted((float(x), float(y)) for x, y in manter)

        def mapa(t):
            pos = 0.0
            for x, y in trechos:
                if t < x:
                    return pos          # caiu num corte: vai para o começo do trecho seguinte
                if t <= y:
                    return pos + t - x
                pos += y - x
            return pos

        def fica(p):
            return sum(max(0.0, min(p["e"], y) - max(p["s"], x)) for x, y in trechos)
    else:
        cs = sorted((float(x), float(y)) for x, y in (cortes or []))
        if not cs:
            return [dict(p) for p in palavras]

        def mapa(t):
            tirado = 0.0
            for x, y in cs:
                if t >= y:
                    tirado += y - x
                elif t > x:
                    return x - tirado
                else:
                    break
            return t - tirado

        def fica(p):
            return (p["e"] - p["s"]) - sum(max(0.0, min(p["e"], y) - max(p["s"], x)) for x, y in cs)

    out = []
    for p in palavras:
        dur = max(0.01, p["e"] - p["s"])
        if fica(p) < 0.4 * dur:
            continue
        s2, e2 = mapa(p["s"]), mapa(p["e"])
        out.append({"w": p["w"], "s": round(s2, 3), "e": round(max(e2, s2 + 0.05), 3)})
    return out


def palavras_para_edicao(pasta, nome, log=None):
    """(palavras ouvidas, palavras da legenda) com os tempos da cena como ela está agora (cortada ou não)."""
    ent = transcricao_da_cena(pasta, nome, log)
    salvo = cortes_salvos(pasta, nome)
    cortes = salvo["cortes"] if salvo else []
    manter = salvo.get("manter") if salvo else None
    return mapear_pelos_cortes(ent["asr"], cortes, manter), mapear_pelos_cortes(ent["palavras"], cortes, manter)


def _distribuir(textos, s0, e0, out):
    e0 = max(e0, s0 + 0.12 * len(textos))
    total = sum(max(1, len(w)) for w in textos)
    t = s0
    for w in textos:
        d = (e0 - s0) * max(1, len(w)) / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
        t += d


def corrigir_texto(palavras, texto):
    """Aplica o texto que você digitou sobre as palavras ouvidas, mantendo os tempos:
    palavra trocada fica no lugar da antiga; palavra nova divide o tempo com a vizinha; apagada sai."""
    novos = (texto or "").split()
    if not palavras or not novos:
        return []
    velhos = [_normal(p["w"]) for p in palavras]
    alvo = [_normal(w) for w in novos]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, velhos, alvo, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                p = dict(palavras[i1 + k])
                p["w"] = novos[j1 + k]
                out.append(p)
        elif tag == "replace":
            _distribuir(novos[j1:j2], palavras[i1]["s"], palavras[i2 - 1]["e"], out)
        elif tag == "insert":
            ant = out[-1] if out else None
            prox = palavras[i1] if i1 < len(palavras) else None
            if ant and prox and prox["s"] - ant["e"] >= 0.12:
                s0, e0 = ant["e"], prox["s"]
            elif ant:
                meio = (ant["s"] + ant["e"]) / 2
                s0, e0 = meio, ant["e"]
                ant["e"] = round(meio, 3)
            elif prox:
                s0, e0 = max(0.0, prox["s"] - 0.3), prox["s"]
            else:
                s0, e0 = 0.0, 0.5
            _distribuir(novos[j1:j2], s0, e0, out)
    return out


def salvar_correcao(pasta, nome, texto):
    ent = transcricao_da_cena(pasta, nome)
    with _cache_trava:
        dados = _ler_palavras(pasta)
        ent = dados.get(nome, ent)
        ent["palavras"] = corrigir_texto(ent["palavras"], texto)
        ent["corrigida"] = " ".join(p["w"] for p in ent["palavras"]) != " ".join(p["w"] for p in ent["asr"])
        dados[nome] = ent
        _gravar_palavras(pasta, dados)
    return ent


def restaurar_legenda(pasta, nome):
    with _cache_trava:
        dados = _ler_palavras(pasta)
        if nome in dados:
            dados[nome]["palavras"] = dados[nome]["asr"]
            dados[nome]["corrigida"] = False
            _gravar_palavras(pasta, dados)


def aplicar_correcoes(palavras, regras):
    """Correções automáticas ("errado = certo", uma por linha), valem para todos os vídeos.
    Aceita mais de uma palavra dos dois lados (ex.: "GLP one = GLP-1")."""
    pares = []
    for linha in (regras or "").splitlines():
        if "=" not in linha:
            continue
        a, b = linha.split("=", 1)
        de = [_normal(x) for x in a.split() if _normal(x)]
        if de:
            pares.append((de, b.split()))
    if not pares or not palavras:
        return palavras
    out, i = [], 0
    while i < len(palavras):
        for de, para in pares:
            n = len(de)
            if [_normal(p["w"]) for p in palavras[i:i + n]] == de:
                fim = re.search(r"[.,!?:;…]+$", palavras[i + n - 1]["w"])
                ws = list(para)
                if fim and ws and not re.search(r"[.,!?:;…]$", ws[-1]):
                    ws[-1] += fim.group()
                if ws:
                    _distribuir(ws, palavras[i]["s"], palavras[i + n - 1]["e"], out)
                i += n
                break
        else:
            out.append(palavras[i])
            i += 1
    return out


# ===================================================================== cenas
def _ordem(nome):
    m = re.search(r"\d+", nome)
    return (0, int(m.group()), nome.lower()) if m else (1, 0, nome.lower())


def listar_cenas(pasta):
    """Vídeos da pasta, na ordem do primeiro número do nome (cena 1, v2, take 03…)."""
    if not os.path.isdir(pasta):
        return []
    vids = [os.path.join(pasta, f) for f in os.listdir(pasta)
            if f.lower().endswith(EXT_VIDEO) and not f.startswith(("_", ".")) and os.path.isfile(os.path.join(pasta, f))]
    return sorted(vids, key=lambda f: _ordem(os.path.basename(f)))


def tamanho_saida(cenas):
    """Formato que domina o vídeo (em pé ou deitado, pela duração; empate = em pé),
    no tamanho da maior cena nesse formato (sem ampliar à toa)."""
    tams, pe, deitado = [], 0.0, 0.0
    for c in cenas:
        s = sondar(c)
        if s["w"] and s["h"]:
            tams.append((s["w"], s["h"]))
            if s["h"] >= s["w"]:
                pe += s["dur"] or 1
            else:
                deitado += s["dur"] or 1
    if not tams:
        return 1080, 1920
    em_pe = pe >= deitado
    mesmos = [t for t in tams if (t[1] >= t[0]) == em_pe] or tams
    w, h = max(mesmos, key=lambda x: x[0] * x[1])
    lado = max(w, h)
    if lado > LADO_MAX:      # vídeo de celular em 4K vira 1080×1920: igual para as redes e bem mais rápido
        w, h = round(w * LADO_MAX / lado), round(h * LADO_MAX / lado)
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


# ===================================================================== zoom
def dividir_em_trechos(palavras, D, minimo=2.2, maximo=6.0):
    """Divide uma fala longa (vídeo inteiro) em trechos, sempre logo antes de uma palavra,
    de preferência no fim de frase ou numa pausa. Tempos locais da cena."""
    if D <= maximo * 1.4 or len(palavras) < 4:
        return [(0.0, D)]
    cortes, ultimo = [0.0], 0.0
    for w, nx in zip(palavras, palavras[1:]):
        dur = nx["s"] - ultimo
        pausa = nx["s"] - w["e"]
        if dur >= minimo and (re.search(r"[.!?…:;,]$", w["w"]) or pausa >= 0.12 or dur >= maximo):
            t = nx["s"] - min(0.04, max(0.0, pausa / 2))
            if D - t < minimo:
                break
            cortes.append(t)
            ultimo = t
    cortes.append(D)
    return list(zip(cortes, cortes[1:]))


def _expr_zoom(pecas):
    """Z(in): zoom por quadro, linear em cada pedaço (n0, n1, z0, z1)."""
    def lin(n0, n1, z0, z1):
        if abs(z1 - z0) < 1e-6:
            return f"{z0:.4f}"
        return f"({z0:.4f}+{(z1 - z0) / max(1, n1 - n0):.7f}*(in-{n0}))"
    expr = lin(*pecas[-1])
    for n0, n1, z0, z1 in reversed(pecas[:-1]):
        expr = f"if(lt(in,{n1}),{lin(n0, n1, z0, z1)},{expr})"
    return expr


def filtro_zoom(pecas):
    """Zoom centralizado com interpolação suave (perspective), quadro a quadro."""
    m = f"(0.5/{_expr_zoom(pecas)})"
    cantos = {"x0": f"W*(0.5-{m})", "y0": f"H*(0.5-{m})", "x1": f"W*(0.5+{m})", "y1": f"H*(0.5-{m})",
              "x2": f"W*(0.5-{m})", "y2": f"H*(0.5+{m})", "x3": f"W*(0.5+{m})", "y3": f"H*(0.5+{m})"}
    return "perspective=" + ":".join(f"{k}='{e}'" for k, e in cantos.items()) + ":interpolation=cubic:eval=frame"


# ================================================================== legendas
def _cor_ass(hexcor, alfa=0):
    h = (hexcor or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alfa:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _cor_tag(hexcor):
    """Cor para usar dentro do texto (\\c&HBBGGRR&), sem o byte de transparência."""
    return "&H" + _cor_ass(hexcor)[4:]


def _t_ass(x):
    x = max(0.0, x)
    cs = int(round(x * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def _limpa_ass(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def escrever_ass(eventos, caminho, op, W, H):
    esc = H / 1920
    fonte = FONTES_LEGENDA.get(op.get("leg_fonte"), FONTES_LEGENDA["Montserrat ExtraBold"])
    tam = int(round(float(op.get("leg_tamanho", 80)) * esc))
    y_frac = {"alto": 0.30, "centro": 0.56, "baixo": 0.71}.get(op.get("leg_posicao"), 0.71)
    margem_v = int(round((1 - y_frac) * H))
    margem_l = int(round(0.07 * W))
    cor = _cor_ass(op.get("leg_cor"))
    ativa = _cor_tag(op.get("leg_cor_ativa"))
    chave_cor = _cor_tag(op.get("leg_cor_chave"))
    chaves = {_normal(x) for x in re.split(r"[\n,;]+", op.get("palavras_chave", "") or "") if _normal(x)}
    chaves_simples = {c for c in chaves if " " not in c}
    contorno = max(1, round(7 * esc, 1))
    sombra = round(3 * esc, 1)
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: GW,{fonte[1]},{tam},{cor},{cor},&H00000000,&H64000000,{fonte[2]},0,0,0,100,100,0,0,1,{contorno},{sombra},2,{margem_l},{margem_l},{margem_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    n = max(1, min(6, int(op.get("leg_palavras", 3))))
    grupos, g = [], []
    for e in eventos:
        if g and (len(g) >= n or re.search(r"[.,!?:;…]$", g[-1]["w"]) or g[-1]["cena"] != e["cena"]
                  or e["s"] - g[-1]["e"] > 0.7):
            grupos.append(g)
            g = []
        g.append(e)
    if g:
        grupos.append(g)
    maius = bool(op.get("leg_maiusculas", True))
    pop = bool(op.get("leg_pop", True))
    capa = CAPA if op.get("capa_limpa", True) else 0.0
    linhas = []

    def e_chave(w):
        nw = _normal(w)
        return nw in chaves_simples or any(ch.isdigit() for ch in nw)

    for gi, g in enumerate(grupos):
        fim_g = g[-1]["e"] + 0.2
        if gi + 1 < len(grupos):
            fim_g = min(max(fim_g, g[-1]["e"]) + 0.3, grupos[gi + 1][0]["s"])
        textos = []
        for p in g:
            t = _limpa_ass(p["w"].replace("—", "").strip())
            textos.append(t.upper() if maius else t)
        for wi, p in enumerate(g):
            ini = max(p["s"], capa)
            fim = g[wi + 1]["s"] if wi + 1 < len(g) else fim_g
            if fim - ini < 0.04:
                continue
            partes = []
            for wj, t in enumerate(textos):
                chave = e_chave(g[wj]["w"])
                if wj == wi:
                    c = chave_cor if chave else ativa
                    efeito = "\\fscx116\\fscy116\\t(0,90,\\fscx106\\fscy106)" if pop else ""
                    partes.append("{\\c" + c + "&" + efeito + "}" + t + "{\\r}")
                elif chave:
                    partes.append("{\\c" + chave_cor + "&}" + t + "{\\r}")
                else:
                    partes.append(t)
            linhas.append(f"Dialogue: 0,{_t_ass(ini)},{_t_ass(fim)},GW,,0,0,0,," + " ".join(partes))
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(cab + "\n".join(linhas) + "\n")
    return len(linhas)


# =================================================================== headline
_EMOJI = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2})"
    "|(?:[0-9#*]\uFE0F?\u20E3)"
    "|(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u2190-\u21FF\u2934\u2935\u3030\u303D"
    "\u3297\u3299\u00A9\u00AE\u203C\u2049\u2122\u2139\u24C2\u25AA-\u25FE]"
    "[\uFE0F\U0001F3FB-\U0001F3FF]*(?:\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF][\uFE0F\U0001F3FB-\U0001F3FF]*)*)")
_emojis = {}


def _pedacos(s):
    out, i = [], 0
    for m in _EMOJI.finditer(s):
        if m.start() > i:
            out.append(("t", s[i:m.start()]))
        out.append(("e", m.group()))
        i = m.end()
    if i < len(s):
        out.append(("t", s[i:]))
    return out


def _emoji(e, altura):
    from PIL import Image, ImageDraw, ImageFont
    k = (e, altura)
    if k not in _emojis:
        im = None
        try:
            fe = ImageFont.truetype(os.path.join(PASTA_FONTES, "NotoColorEmoji.ttf"), 109)
            tela = Image.new("RGBA", (300, 180), (0, 0, 0, 0))
            ImageDraw.Draw(tela).text((10, 10), e, font=fe, embedded_color=True)
            bb = tela.getbbox()
            if bb:
                im = tela.crop(bb)
                im = im.resize((max(1, round(im.width * altura / im.height)), altura), Image.LANCZOS)
        except Exception:
            im = None
        _emojis[k] = im
    return _emojis[k]


def _rgba(hexcor, opac=100):
    h = (hexcor or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(round(255 * max(0, min(100, float(opac))) / 100)))


def camada_headline(texto, op, W, H):
    """Imagem RGBA (W×H) transparente com a headline desenhada."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    K = 2
    s = min(W / 1080, H / 1920) * K
    tam = max(10, int(float(op.get("hl_tamanho", 68)) * s))
    arq = FONTES_HEADLINE.get(op.get("hl_fonte"), "Montserrat-ExtraBold.ttf")
    fonte = ImageFont.truetype(os.path.join(PASTA_FONTES, arq), tam)
    asc, desc = fonte.getmetrics()
    _, cap_topo, _, cap_base = fonte.getbbox("H")
    alt_emoji = max(8, int((cap_base - cap_topo) * 1.32))
    emoji_y = cap_topo + (cap_base - cap_topo) / 2 - alt_emoji / 2
    WK, HK = W * K, H * K
    margem = max(10 * K, float(op.get("hl_margem", 80)) * s)
    larg_max = WK - 2 * margem
    rascunho = ImageDraw.Draw(Image.new("L", (1, 1)))

    def largura(linha):
        return sum(rascunho.textlength(t, font=fonte) if k == "t" else alt_emoji * 1.12 for k, t in _pedacos(linha))

    linhas = []
    for par in (texto or "").replace("\r", "").strip("\n").split("\n"):
        palavras = par.split()
        if not palavras:
            linhas.append("")
            continue
        atual = ""
        for p in palavras:
            tentativa = (atual + " " + p).strip()
            if not atual or largura(tentativa) <= larg_max:
                atual = tentativa
            else:
                linhas.append(atual)
                atual = p
        linhas.append(atual)
    while linhas and not linhas[-1]:
        linhas.pop()
    if not linhas:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bb = fonte.getbbox("HÁgjy")
    topo_tinta, base_tinta = bb[1], bb[3]
    alt_linha = int((asc + desc) * 1.06)
    bloco = (len(linhas) - 1) * alt_linha + (base_tinta - topo_tinta)
    centro_y = float(op.get("hl_y", 18)) / 100 * HK
    y0 = centro_y - bloco / 2 - topo_tinta
    desl_x = float(op.get("hl_x", 0)) * s
    pos = []
    for i, ln in enumerate(linhas):
        w = largura(ln)
        pos.append((WK / 2 - w / 2 + desl_x, y0 + i * alt_linha, ln, w))

    camada = Image.new("RGBA", (WK, HK), (0, 0, 0, 0))
    fundo = bool(op.get("hl_fundo"))
    if fundo:
        d = ImageDraw.Draw(camada)
        pad = tam * 0.3
        cor = _rgba(op.get("hl_fundo_cor", "#7C3AED"), op.get("hl_fundo_opac", 90))
        for x, y, ln, w in pos:
            if ln.strip():
                d.rounded_rectangle((x - pad, y + topo_tinta - pad * 0.62, x + w + pad, y + base_tinta + pad * 0.62),
                                    radius=int(pad * 0.75), fill=cor)

    def escreve(dr, dx, dy, cor, contorno=0, cor_contorno=None, emojis=True, alvo=None):
        for x, y, ln, _ in pos:
            cx = x + dx
            for k, t in _pedacos(ln):
                if k == "t":
                    dr.text((cx, y + dy), t, font=fonte, fill=cor, stroke_width=contorno, stroke_fill=cor_contorno)
                    cx += dr.textlength(t, font=fonte)
                else:
                    if emojis and alvo is not None:
                        im = _emoji(t, alt_emoji)
                        if im is not None:
                            alvo.alpha_composite(im, (int(cx + alt_emoji * 0.06), int(y + dy + emoji_y)))
                    cx += alt_emoji * 1.12

    if not fundo and op.get("hl_sombra", True):
        sombra = Image.new("RGBA", (WK, HK), (0, 0, 0, 0))
        off = max(3, tam // 15)
        esp = int(float(op.get("hl_contorno_esp", 5)) * s) if op.get("hl_contorno", True) else 0
        escreve(ImageDraw.Draw(sombra), off, off, (0, 0, 0, _rgba("#000000", op.get("hl_sombra_opac", 60))[3]),
                esp, (0, 0, 0, _rgba("#000000", op.get("hl_sombra_opac", 60))[3]), emojis=False)
        camada = Image.alpha_composite(camada, sombra.filter(ImageFilter.GaussianBlur(off)))
    esp = int(float(op.get("hl_contorno_esp", 5)) * s) if (op.get("hl_contorno", True) and not fundo) else 0
    escreve(ImageDraw.Draw(camada), 0, 0, _rgba(op.get("hl_cor", "#FFFFFF")), esp,
            _rgba(op.get("hl_contorno_cor", "#000000")), emojis=True, alvo=camada)
    return camada.resize((W, H), Image.LANCZOS)


def opcoes_cta(op):
    """As opções do CTA no formato da headline (o desenho é o mesmo)."""
    o = dict(op)
    for k, v in op.items():
        if k.startswith("cta_"):
            o["hl_" + k[4:]] = v
    return o


# ===================================================================== setas
ESTILOS_SETA = {"grossa": "Seta grossa", "curva": "Seta curva", "chevrons": "Setas duplas",
                "desenhada": "Desenhada à mão", "mao": "Mãozinha"}
DIRECOES = {"dir": 0, "baixo_dir": 45, "baixo": 90, "baixo_esq": 135, "esq": 180, "cima_esq": 225, "cima": 270, "cima_dir": 315}
_EMOJI_MAO = {"dir": "\U0001F449", "esq": "\U0001F448", "cima": "\U0001F446", "baixo": "\U0001F447"}
_setas = {}


def desenhar_seta(estilo="grossa", direcao="baixo", cor="#FFD84D", lado=300):
    """Seta RGBA num quadrado lado×lado, apontando para `direcao`, com contorno preto e sombra suave."""
    from PIL import Image, ImageDraw, ImageFilter, ImageChops
    import math
    chave = (estilo, direcao, cor, int(lado))
    if chave in _setas:
        return _setas[chave].copy()
    lado = max(16, int(lado))
    K = 3
    S = lado * K
    angulo = DIRECOES.get(direcao, 90)
    if estilo == "mao":
        if direcao in _EMOJI_MAO:
            e, giro = _EMOJI_MAO[direcao], 0
        elif 90 < angulo < 270:                       # diagonais para a esquerda: 👈 girado
            e, giro = _EMOJI_MAO["esq"], angulo - 180
        else:                                          # diagonais para a direita: 👉 girado
            e, giro = _EMOJI_MAO["dir"], angulo if angulo < 180 else angulo - 360
        im = _emoji(e, int(S * (0.60 if giro else 0.72)))
        tela = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        if im is not None:
            if giro:
                im = im.rotate(-giro, resample=Image.BICUBIC, expand=True)
                im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
            if max(im.size) > S * 0.84:                 # cabe no quadrado, com folga para a sombra
                f = S * 0.84 / max(im.size)
                im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
            tela.alpha_composite(im, ((S - im.width) // 2, (S - im.height) // 2))
        forma = tela
        sombra = tela.split()[3].filter(ImageFilter.GaussianBlur(S * 0.02))
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        out.paste((0, 0, 0, 140), (int(S * 0.015), int(S * 0.025)), sombra)
        out.alpha_composite(forma)
    else:
        m = Image.new("L", (S, S), 0)
        d = ImageDraw.Draw(m)
        c = S / 2
        if estilo == "curva":
            w = int(S * 0.11)
            r = S * 0.40
            cx, cy = c - S * 0.04, c + S * 0.20
            a0, a1 = 205, 312
            d.arc((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=255, width=w)
            fim = math.radians(a1)
            px, py = cx + (r - w / 2) * math.cos(fim), cy + (r - w / 2) * math.sin(fim)
            tx, ty = -math.sin(fim), math.cos(fim)            # tangente no fim do arco
            nx, ny = -ty, tx
            L, B = S * 0.20, S * 0.16
            d.polygon([(px + tx * L, py + ty * L), (px + nx * B - tx * 2, py + ny * B - ty * 2),
                       (px - nx * B - tx * 2, py - ny * B - ty * 2)], fill=255)
        elif estilo == "chevrons":
            w = int(S * 0.085)
            for k, x0 in enumerate((0.16, 0.38, 0.60)):
                x = S * x0
                d.line([(x, c - S * 0.20), (x + S * 0.20, c), (x, c + S * 0.20)], fill=255, width=w, joint="curve")
                for ponta in ((x, c - S * 0.20), (x, c + S * 0.20)):
                    d.ellipse((ponta[0] - w / 2, ponta[1] - w / 2, ponta[0] + w / 2, ponta[1] + w / 2), fill=255)
        elif estilo == "desenhada":
            w = int(S * 0.055)
            pts = [(S * (0.10 + 0.70 * i / 24), c + S * 0.035 * math.sin(i / 24 * math.pi * 2.2)) for i in range(25)]
            d.line(pts, fill=255, width=w, joint="curve")
            ponta = (S * 0.84, c + S * 0.01)
            for dy in (-1, 1):
                d.line([(S * 0.66, c + dy * S * 0.17), ponta], fill=255, width=w)
            for p in pts[:1] + [ponta, (S * 0.66, c - S * 0.17), (S * 0.66, c + S * 0.17)]:
                d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=255)
        else:   # grossa
            hs, hh = S * 0.10, S * 0.27
            d.polygon([(S * 0.08, c - hs), (S * 0.54, c - hs), (S * 0.54, c - hh), (S * 0.93, c),
                       (S * 0.54, c + hh), (S * 0.54, c + hs), (S * 0.08, c + hs)], fill=255)
        if angulo:
            m = m.rotate(-angulo, resample=Image.BICUBIC, center=(c, c))
        # contorno: desfoca e corta no limiar (bem mais rápido que MaxFilter em setas grandes)
        contorno = m.filter(ImageFilter.GaussianBlur(S * 0.014)).point(lambda v: min(255, v * 20))
        sombra = contorno.filter(ImageFilter.GaussianBlur(S * 0.02))
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        out.paste((0, 0, 0, 120), (int(S * 0.012), int(S * 0.02)), sombra)
        out.paste((12, 10, 16, 255), (0, 0), contorno)
        out.paste(_rgba(cor, 100), (0, 0), m)
    out = out.resize((lado, lado), Image.LANCZOS)
    _setas[chave] = out
    return out.copy()


# ============================================================ CTA e setas sobre o vídeo pronto
def _num(v, padrao=None):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def aplicar_elementos(pasta, saida, opcoes=None, elementos=None, log=print):
    """Coloca o CTA e as setas por cima do vídeo-base (_trabalho/base.mp4) e grava em `saida`.
    Sem CTA nem setas, o vídeo pronto é o próprio base. Rápido: não ouve nem corta de novo."""
    from PIL import Image  # noqa: F401
    op = {**PADRAO, **(opcoes or {})}
    el = elementos or {}
    trab = os.path.join(pasta, "_trabalho")
    base = os.path.join(trab, "base.mp4")
    if not os.path.isfile(base):
        raise RuntimeError("Esse vídeo ainda não foi editado. Clique em Editar vídeos primeiro.")
    s = sondar(base)
    W, H, D = s["w"], s["h"], s["dur"]
    fps = round(min(60.0, s["fps"]), 3)
    camadas = []    # (arquivo, x, y, quando, ini, fim)
    texto = (el.get("cta") or "").strip() if op.get("cta") else ""
    if texto:
        ini = _num(el.get("cta_ini"))
        fim = _num(el.get("cta_fim"))
        ini = max(0.0, D - float(op.get("cta_antes_fim", 4))) if ini is None else max(0.0, ini)
        fim = D + 1 if fim is None else fim
        if fim > ini + 0.05:
            im = camada_headline(texto, opcoes_cta(op), W, H)
            bb = im.getbbox() or (0, 0, 2, 2)
            bb = (bb[0] // 2 * 2, bb[1] // 2 * 2, min(W, bb[2] + 1) // 2 * 2 or 2, min(H, bb[3] + 1) // 2 * 2 or 2)
            im.crop(bb).save(os.path.join(trab, "cta.png"))       # só a área do CTA: bem mais rápido
            camadas.append(("cta.png", str(bb[0]), str(bb[1]), f"between(t,{ini:.3f},{fim:.3f})", ini, fim))
    for k, sx in enumerate(el.get("setas") or []):
        ini = max(0.0, _num(sx.get("ini"), 0.0))
        fim = _num(sx.get("fim"))
        fim = D + 1 if fim is None else fim
        if fim <= ini + 0.05:
            continue
        lado = int(max(4.0, min(80.0, _num(sx.get("tam"), 22))) / 100 * W) // 2 * 2
        desenhar_seta(sx.get("estilo", "grossa"), sx.get("dir", "baixo"), sx.get("cor", "#FFD84D"), lado).save(
            os.path.join(trab, f"seta_{k}.png"))
        cx = max(0.0, min(1.0, _num(sx.get("x"), 0.5))) * W
        cy = max(0.0, min(1.0, _num(sx.get("y"), 0.5))) * H
        x0, y0 = cx - lado / 2, cy - lado / 2
        quando = f"between(t,{ini:.3f},{fim:.3f})"
        xe, ye = f"{x0:.1f}", f"{y0:.1f}"
        anim = sx.get("anim", "balanca")
        if anim == "balanca":
            import math
            ang = math.radians(DIRECOES.get(sx.get("dir", "baixo"), 90))
            amp = lado * 0.10
            onda = f"sin(2*PI*1.6*(t-{ini:.3f}))"
            xe = f"{x0:.1f}+{amp * math.cos(ang):.2f}*{onda}"
            ye = f"{y0:.1f}+{amp * math.sin(ang):.2f}*{onda}"
        elif anim == "pisca":
            quando += f"*lt(mod(t-{ini:.3f},0.8),0.55)"
        camadas.append((f"seta_{k}.png", xe, ye, quando, ini, fim))
    os.makedirs(os.path.dirname(saida), exist_ok=True)
    if not camadas:
        tmp = os.path.join(trab, "pronto.mp4")
        if os.path.exists(tmp):
            os.remove(tmp)
        try:
            os.link(base, tmp)
        except OSError:
            shutil.copy2(base, tmp)
        trocar(tmp, saida)
        return {"arquivo": saida, "duracao": round(D, 1), "camadas": 0}
    log("Colocando o CTA e as setas…")
    entradas = ["-i", "base.mp4"]
    filtros, atual = [], "[0:v]"
    for i, (arq, xe, ye, quando, ini, fim) in enumerate(camadas, 1):
        entradas += ["-loop", "1", "-framerate", f"{fps}", "-i", arq]
        some = f",fade=t=out:st={fim - 0.2:.3f}:d=0.2:alpha=1" if fim < D - 0.05 else ""
        filtros.append(f"[{i}:v]format=rgba,fade=t=in:st={ini:.3f}:d=0.2:alpha=1{some}[e{i}]")
        filtros.append(f"{atual}[e{i}]overlay=x='{xe}':y='{ye}':enable='{quando}':shortest=1:format=auto[v{i}]")
        atual = f"[v{i}]"
    crf = {"alta": "15", "normal": "17", "leve": "20"}.get(op.get("qualidade"), "17")   # um pouco acima do base
    tmp = os.path.join(trab, "render_elementos.mp4")
    ff(*entradas, "-filter_complex", ";".join(filtros), "-map", atual, "-map", "0:a?",
       "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p", "-r", f"{fps}",
       "-c:a", "copy", "-movflags", "+faststart", "-t", f"{D:.3f}", tmp, cwd=trab)
    trocar(tmp, saida)
    return {"arquivo": saida, "duracao": round(D, 1), "camadas": len(camadas)}


def primeiro_quadro(arq, W, H):
    from PIL import Image
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        try:
            ff("-ss", "0.1", "-i", arq, "-frames:v", "1", "-q:v", "3", tmp)
            im = Image.open(tmp).convert("RGB")
            im.load()
        except Exception:
            ff("-i", arq, "-frames:v", "1", "-q:v", "3", tmp)
            im = Image.open(tmp).convert("RGB")
            im.load()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    esc = max(W / im.width, H / im.height)
    im = im.resize((max(W, round(im.width * esc)), max(H, round(im.height * esc))), Image.LANCZOS)
    l, t = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((l, t, l + W, t + H))


def previa_headline(pasta, texto, opcoes, saida, largura=360, cta=False):
    """JPG pequeno com a headline (ou o CTA) sobre o primeiro quadro do vídeo."""
    from PIL import Image
    op = {**PADRAO, **(opcoes or {})}
    if cta:
        op = opcoes_cta(op)
    cenas = listar_cenas(pasta)
    W, H = tamanho_saida(cenas) if cenas else (1080, 1920)
    base = primeiro_quadro(cenas[0], W, H).convert("RGBA") if cenas else Image.new("RGBA", (W, H), (20, 14, 30, 255))
    im = Image.alpha_composite(base, camada_headline(texto or ("Seu CTA aqui" if cta else "Sua headline aqui"), op, W, H)).convert("RGB")
    im.resize((largura, max(1, round(largura * H / W))), Image.LANCZOS).save(saida, quality=88)
    return saida


# ===================================================================== edição
def camada_cta_previa(pasta, texto, opcoes, saida, largura=540):
    """PNG transparente (só o CTA) do tamanho do vídeo, para pôr por cima do player no painel."""
    from PIL import Image
    op = opcoes_cta({**PADRAO, **(opcoes or {})})
    base = os.path.join(pasta, "_trabalho", "base.mp4")
    if os.path.isfile(base):
        sb = sondar(base)
        W, H = sb["w"], sb["h"]
    else:
        cenas = listar_cenas(pasta)
        W, H = tamanho_saida(cenas) if cenas else (1080, 1920)
    im = camada_headline(texto or "Seu CTA aqui", op, W, H)
    im.resize((largura, max(1, round(largura * H / W))), Image.LANCZOS).save(saida)
    return saida


def musicas_do_usuario():
    os.makedirs(PASTA_MUSICAS, exist_ok=True)
    return sorted(f for f in os.listdir(PASTA_MUSICAS) if f.lower().endswith(EXT_AUDIO) and not f.startswith("."))


def editar(pasta, opcoes=None, log=print, saida=None, elementos=None):
    """Edita o vídeo cuja pasta contém as cenas. Devolve {arquivo, duracao, avisos, ordem}."""
    op = {**PADRAO, **(opcoes or {})}
    pasta = os.path.abspath(pasta)
    nome = os.path.basename(os.path.normpath(pasta))
    trab = os.path.join(pasta, "_trabalho")
    os.makedirs(trab, exist_ok=True)
    cenas = listar_cenas(pasta)
    if not cenas:
        raise RuntimeError("Essa pasta não tem nenhum vídeo (MP4, MOV ou WEBM).")
    precisa_ouvir = op["legenda"] or op["cortar_silencio"] or op["pular_repetidas"] or op["igualar_voz"]

    # 1) ouvir cada cena
    info = []
    for i, arq in enumerate(cenas, 1):
        s = sondar(arq)
        if s["dur"] <= 0.05:
            continue
        log(f"Ouvindo a cena {i} de {len(cenas)}…")
        a = ler_audio(arq) if (s["audio"] and precisa_ouvir) else np.zeros(0, np.float32)
        ouvir_fala = op["legenda"] or op["cortar_silencio"] or op["pular_repetidas"] or op.get("zoom_modo") != "nenhum"
        pal, leg = [], []
        if len(a) and ouvir_fala:
            pal, leg = palavras_para_edicao(pasta, os.path.basename(arq))
            leg = aplicar_correcoes(leg, op.get("leg_correcoes", ""))
        ini, fim = limites_fala(a, pal, s["dur"])
        info.append({"arquivo": arq, "rotulo": f"cena {i}", "sonda": s, "palavras": pal, "legenda": leg,
                     "texto_ouvido": " ".join(p["w"] for p in pal), "texto": " ".join(p["w"] for p in leg),
                     "ini": ini, "fim": fim, "nivel": nivel_fala_db(a) if len(a) else None})
    if not info:
        raise RuntimeError("As cenas estão vazias ou corrompidas.")

    # 2) cenas repetidas (o Flow costuma gerar mais de um take da mesma fala)
    avisos, mantidas = [], []
    for c in info:
        if op["pular_repetidas"]:
            t = _normal(c["texto_ouvido"])
            if len(t.split()) >= 3:
                igual = next((m for m in mantidas if difflib.SequenceMatcher(None, t, _normal(m["texto_ouvido"])).ratio() > 0.85), None)
                if igual:
                    avisos.append(f"A {c['rotulo']} repete a fala da {igual['rotulo']} e ficou de fora.")
                    continue
        mantidas.append(c)

    # 3) linha do tempo
    W, H = tamanho_saida([c["arquivo"] for c in mantidas])
    fps = round(min(60.0, mantidas[0]["sonda"]["fps"]), 3)
    vel = max(0.5, min(2.0, float(op.get("velocidade") or 1.0)))
    modo_zoom = op.get("zoom_modo") if op.get("zoom_modo") in ("lento", "pulo") else "nenhum"
    Jq = max(1, min(12, int(op["jcut_quadros"]))) if op["jcut"] else 0
    # Tudo é contado em QUADROS inteiros (imagem) e daí em amostras (som): cada cena entra com exatamente
    # nq quadros e nq/fps segundos de som, e a próxima começa onde a anterior terminou. Nada se acumula.
    segs, eventos, Vq = [], [], 0
    for c in mantidas:
        d = c["sonda"]["dur"]
        a, b = (max(0.0, c["ini"] - 0.10), min(d, c["fim"] + 0.25)) if op["cortar_silencio"] else (0.0, d)
        a = round(a * fps) / fps
        nq = max(1, int(round((b - a) * fps)))
        if a + nq / fps > d + 0.5 / fps:
            nq = max(1, int((d - a) * fps))
        D = nq / fps
        if D < 0.2:
            avisos.append(f"A {c['rotulo']} ficou curta demais depois do corte e saiu.")
            continue
        Jqi = Jq if (segs and nq > Jq + fps / 2 and segs[-1]["nq"] > Jq + fps / 2) else 0
        Ji = Jqi / fps
        t_audio = (Vq - Jqi) / fps
        idx = len(segs)
        for p in c["legenda"]:
            if p["e"] <= a + 0.02 or p["s"] >= b - 0.02:
                continue
            eventos.append({"w": p["w"], "s": round(max(p["s"], a) - a + t_audio, 3),
                            "e": round(min(p["e"], b) - a + t_audio, 3), "cena": idx})
        ganho = 0.0
        if op["igualar_voz"] and c["nivel"] is not None:
            ganho = float(np.clip(ALVO_VOZ_DB - c["nivel"], -12, 16))
        locais = [{"w": p["w"], "s": p["s"] - a, "e": p["e"] - a} for p in c["palavras"] if a <= p["s"] < b]
        trechos = dividir_em_trechos(locais, D) if modo_zoom != "nenhum" else [(0.0, D)]
        segs.append({"arquivo": c["arquivo"], "a": a, "D": D, "nq": nq, "J": Ji, "Jq": Jqi, "t_audio": t_audio,
                     "trechos": trechos, "audio": c["sonda"]["audio"], "ganho": ganho, "rotulo": c["rotulo"]})
        Vq += nq - Jqi
    if not segs:
        raise RuntimeError("Nenhuma cena sobrou depois dos cortes.")
    total = Vq / fps
    total_am = int(round(total * SR_SAIDA))
    em_pe = H >= W
    outro_formato, pequenas = [], []
    for s in segs:
        sw, sh = sondar(s["arquivo"])["w"], sondar(s["arquivo"])["h"]
        if sw and sh and (sh >= sw) != em_pe:
            outro_formato.append(s["rotulo"])
        elif sw * sh < W * H:
            pequenas.append(s["rotulo"])
    if outro_formato:
        avisos.append(f"{', '.join(outro_formato)}: está {'deitada' if em_pe else 'em pé'}, foi cortada para preencher a tela.")
    if pequenas:
        avisos.append(f"{', '.join(pequenas)}: menor que as outras, foi ampliada para {W}×{H}.")

    # 4) arquivos de apoio. A montagem tem duas passadas: primeiro o SOM (voz + música), que é medido e
    # levado ao volume padrão das redes; depois a IMAGEM (cenas + headline + legenda) com esse som.
    entradas, fv, fa = [], [], []
    for s in segs:
        # lê um pouco a mais: o tamanho exato é cortado nos filtros, quadro a quadro e amostra a amostra
        entradas += ["-ss", f"{s['a']:.4f}", "-t", f"{s['D'] + 0.3:.4f}", "-i", s["arquivo"]]
    n = len(segs)
    contador_zoom = 0
    for i, s in enumerate(segs):
        # fps com start_time=0: quadro n = tempo n/fps (mesmo relógio do som). tpad: se a imagem da cena
        # acabar antes do som, repete o último quadro em vez de encurtar (encurtar atrasava o som do resto)
        v = (f"[{i}:v]fps={fps}:start_time=0,scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={W}:{H},setsar=1,tpad=stop_mode=clone:stop_duration=1")
        if modo_zoom != "nenhum":
            # lento: aproxima e afasta devagar, sem emenda visível
            # pulo:  a cada frase o enquadramento "pula" para mais perto e volta (estilo jump cut)
            pecas = []
            for t0, t1 in s["trechos"][:60]:
                n0, n1 = int(round(t0 * fps)), max(int(round(t0 * fps)) + 1, int(round(t1 * fps)))
                if modo_zoom == "lento":
                    z0, z1 = (1.0, 1.045) if contador_zoom % 2 == 0 else (1.045, 1.0)
                else:
                    z0, z1 = (1.0, 1.015) if contador_zoom % 2 == 0 else (1.11, 1.125)
                pecas.append((n0, n1, z0, z1))
                contador_zoom += 1
            v += "," + filtro_zoom(pecas)
        # exatamente os quadros [Jq, nq) da cena
        v += f",trim=start_frame={s['Jq']}:end_frame={s['nq']},setpts=PTS-STARTPTS"
        fv.append(v + f",format=yuv420p,setsar=1[v{i}]")
        amostras = int(round(s["nq"] / fps * SR_SAIDA))
        atraso = int(round(max(0.0, s["t_audio"]) * SR_SAIDA))
        if s["audio"]:
            au = (f"[{i}:a]aresample={SR_SAIDA}:async=1:first_pts=0,"
                  "aformat=sample_fmts=fltp:channel_layouts=stereo")
        else:
            au = f"anullsrc=r={SR_SAIDA}:cl=stereo"
        au += f",apad,atrim=end_sample={amostras},asetpts=PTS-STARTPTS"
        if abs(s["ganho"]) > 0.3:
            au += f",volume={s['ganho']:.1f}dB"
        # 10 ms de rampa nas pontas de cada cena: emenda sem estalo
        au += f",afade=t=in:d=0.01,afade=t=out:st={max(0.0, s['nq'] / fps - 0.01):.4f}:d=0.01"
        fa.append(au + f",adelay=delays={atraso}S:all=1[a{i}]")
    fv.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vcat]")
    if n == 1:
        fa.append(f"[a0]apad,atrim=end_sample={total_am}[vozc]")
    else:
        fa.append("".join(f"[a{i}]" for i in range(n)) +
                  f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,"
                  f"apad,atrim=end_sample={total_am}[vozc]")
    if op["igualar_voz"]:
        # voz mais limpa e uniforme: tira o ronco grave e segura os picos (compressão leve)
        fa.append("[vozc]highpass=f=75,acompressor=threshold=0.089:ratio=2.2:attack=8:release=180:"
                  "knee=2.8:makeup=1.3[voz]")
    else:
        fa.append("[vozc]anull[voz]")

    cadeia_v, voz = "[vcat]", "[voz]"
    if abs(vel - 1.0) > 0.004:
        # acelera imagem e voz juntas (a voz mantém o tom); legenda e música acompanham o novo tempo
        fv.append(f"[vcat]setpts=PTS/{vel:.4f},fps={fps}[vvel]")
        fa.append(f"[voz]atempo={vel:.4f}[vozvel]")
        cadeia_v, voz = "[vvel]", "[vozvel]"
        total = total / vel
        for e in eventos:
            e["s"], e["e"] = round(e["s"] / vel, 3), round(e["e"] / vel, 3)
    extras_v = []          # entradas só da passada de imagem (depois das cenas)
    texto_hl = (op.get("headline_texto") or "").strip() if op.get("headline") else ""
    if texto_hl:
        log("Desenhando a headline…")
        camada_headline(texto_hl, op, W, H).save(os.path.join(trab, "headline.png"))
        extras_v += ["-i", "headline.png"]
        dur_hl = float(op.get("hl_duracao") or 0)
        quando = f":enable='lt(t,{dur_hl:.2f})'" if dur_hl > 0 else ""
        fv.append(f"{cadeia_v}[{n}:v]overlay=0:0:format=auto{quando}[vhl]")
        cadeia_v = "[vhl]"
    if op["legenda"] and eventos:
        pf = os.path.join(trab, "fontes")
        shutil.rmtree(pf, ignore_errors=True)
        os.makedirs(pf, exist_ok=True)
        arq_f = FONTES_LEGENDA.get(op.get("leg_fonte"), FONTES_LEGENDA["Montserrat ExtraBold"])[0]
        shutil.copy(os.path.join(PASTA_FONTES, arq_f), pf)
        escrever_ass(eventos, os.path.join(trab, "legendas.ass"), op, W, H)
        fv.append(f"{cadeia_v}ass=legendas.ass:fontsdir=fontes[vout]")
    else:
        fv.append(f"{cadeia_v}null[vout]")

    extras_a = []          # entradas só da passada de som (depois das cenas)
    musica_usada = "@nenhuma"
    if op["musica"]:
        escolha = op.get("musica_id") or "@pulso"
        arq_m = os.path.join(PASTA_MUSICAS, os.path.basename(escolha))
        if not escolha.startswith("@") and os.path.isfile(arq_m):
            extras_a += ["-stream_loop", "-1", "-i", arq_m]
        else:
            import trilhas
            if escolha not in trilhas.TRILHAS:
                escolha = trilhas.PADRAO
            log("Gerando a música…")
            trilhas.gerar(escolha, total + 0.3, os.path.join(trab, "musica.wav"))
            extras_a += ["-i", "musica.wav"]
        musica_usada = escolha
        vol = float(op.get("musica_volume", -28))
        fa.append(f"[{n}:a]aresample={SR_SAIDA},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"atrim=0:{total:.4f},asetpts=PTS-STARTPTS,afade=t=in:d=0.8,"
                  f"afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5,volume={vol:.0f}dB[mus]")
        fa.append(f"{voz}[mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
    else:
        fa.append(f"{voz}anull[mix]")

    # 5) som: monta, mede e deixa no volume das redes (-14 LUFS, pico até -1 dB)
    log("Montando o som…")
    som = os.path.join(trab, "som.wav")
    ff(*entradas, *extras_a, "-filter_complex", ";".join(fa), "-map", "[mix]", "-c:a", "pcm_f32le",
       "-ar", str(SR_SAIDA), "-t", f"{total:.4f}", som, cwd=trab)
    ganho_final = ganho_para_volume_padrao(som)
    i_som = n + extras_v.count("-i")
    fv.append(f"[{i_som}:a]volume={ganho_final:.2f}dB,alimiter=limit={LIMITE_PICO}:level=0:attack=3:release=60[aout]")

    # 6) imagem: montagem + headline + legenda, com o som pronto
    crf = {"alta": "18", "normal": "21", "leve": "24"}.get(op.get("qualidade"), "21")
    if saida is None:
        saida = os.path.join(pasta, "final", nome + ".mp4")
    os.makedirs(os.path.dirname(saida), exist_ok=True)
    tmp = os.path.join(trab, "render.mp4")
    base = os.path.join(trab, "base.mp4")
    log("Renderizando o vídeo…")
    ff(*entradas, *extras_v, "-i", "som.wav", "-filter_complex", ";".join(fv), "-map", "[vout]", "-map", "[aout]",
       "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p", "-r", f"{fps}",
       "-c:a", "aac", "-b:a", "192k", "-ar", str(SR_SAIDA), "-movflags", "+faststart", "-t", f"{total:.3f}",
       tmp, cwd=trab)
    try:
        os.remove(som)
    except OSError:
        pass
    trocar(tmp, base)
    try:
        with open(os.path.join(trab, "base.versao"), "w") as fh:
            fh.write(str(VERSAO_MONTAGEM))
    except OSError:
        pass
    # CTA e setas por cima (sem nada, o pronto é o próprio base)
    aplicar_elementos(pasta, saida, op, elementos, log)

    # transcrição do vídeo, para conferir/copiar
    linhas = [f"Transcrição — {nome}", ""]
    for c in info:
        linhas.append(f"[{c['rotulo']}] {c['texto'] or '(sem fala)'}")
    linhas += ["", "Ordem usada: " + " → ".join(s["rotulo"] for s in segs) + f"  ({total:.1f} s"
               + (f", velocidade {vel:.2f}×" if abs(vel - 1) > 0.004 else "") + ")"]
    linhas += ["- " + a for a in avisos]
    try:
        with open(os.path.join(pasta, "transcricao.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(linhas) + "\n")
    except OSError:
        pass
    return {"arquivo": saida, "duracao": round(sondar(saida)["dur"] or total, 1), "avisos": avisos,
            "ordem": [s["rotulo"] for s in segs], "musica_usada": musica_usada}


# ============================================================ cortar silêncios
def analisar_silencios(arq, opcoes=None):
    """Envelope da onda (para desenhar) e os trechos de silêncio sugeridos para corte."""
    o = {**PADRAO_SILENCIO, **(opcoes or {})}
    a = ler_audio(arq)
    D = len(a) / SR if len(a) else sondar(arq)["dur"]
    if not len(a):
        return {"dur": round(D, 3), "onda": [], "silencios": []}
    fr = SR // 100
    n = len(a) // fr
    pico = np.abs(a[: n * fr].reshape(n, fr)).max(1)
    db = nivel_quadros_db(a)
    m = min(n, 20000)          # 1 ponto a cada 10 ms (dá para dar zoom na onda)
    cortes = np.linspace(0, n, m + 1).astype(int)
    onda = [round(float(pico[cortes[i]:max(cortes[i] + 1, cortes[i + 1])].max()), 3) for i in range(m)]
    thr = limiar_silencio(db, o.get("abaixo_fala_db", 18))
    if thr is None:     # fala ou som o tempo todo: nada a cortar
        return {"dur": round(D, 3), "onda": onda, "silencios": []}
    quieto = np.concatenate([[False], db < thr, [False]]).astype(np.int8)
    bordas = np.diff(quieto)
    inicios, fins = np.where(bordas == 1)[0], np.where(bordas == -1)[0]
    folga = float(o["folga_ms"]) / 1000
    manter = 2 * folga if o["modo"] != "encurtar" else max(float(o["respiro_ms"]) / 1000, 2 * folga)
    minimo = float(o["minimo_s"])
    out = []
    for i, j in zip(inicios, fins):
        borda_ini, borda_fim = i == 0, j >= n
        if (j - i) * 0.01 < (min(minimo, 0.2) if (borda_ini or borda_fim) else minimo):
            continue
        # protege o finzinho da palavra que acabou e o comecinho da próxima, enquanto ainda
        # estão acima do barulho de fundo desta pausa
        cauda = max(thr - 10, float(np.median(db[i:j])) + 4)
        x, y = i, j
        if not borda_ini:
            while x < min(j, i + int(CAUDA_S * 100)) and db[x] > cauda:
                x += 1
        if not borda_fim:
            while y > max(x, j - int(ATAQUE_S * 100)) and db[y - 1] > cauda:
                y -= 1
        if borda_ini:
            ini, fim = 0.0, y * 0.01 - folga
        elif borda_fim:
            ini, fim = x * 0.01 + folga, D
        else:
            ini, fim = x * 0.01 + manter / 2, y * 0.01 - manter / 2
        if fim - ini >= 0.05:
            out.append([round(ini, 3), round(fim, 3)])
    return {"dur": round(D, 3), "onda": onda, "silencios": out}


def cortar_automatico(pasta, arquivo, opcoes=None, log=None):
    """Corta as pausas sugeridas, sempre a partir do original. Refaz o corte automático anterior se os
    ajustes mudaram. Cena que é só silêncio (b-roll) fica inteira. Devolve os segundos removidos."""
    r = analisar_silencios(fonte_do_corte(pasta, arquivo), opcoes)
    cs = r["silencios"]
    if cs and r["dur"] - sum(b - a for a, b in cs) < 0.4:
        cs = []
    if not cs:
        if os.path.isfile(os.path.join(pasta, "raw", arquivo)):
            restaurar_cena(pasta, arquivo)
        return 0.0
    salvo = cortes_salvos(pasta, arquivo)
    if salvo and salvo["auto"] and salvo.get("manter") and len(salvo["cortes"]) == len(cs) and all(
            abs(a - x) < 0.02 and abs(b - y) < 0.02 for (a, b), (x, y) in zip(salvo["cortes"], cs)):
        return 0.0      # já está cortado exatamente assim (e com a versão nova do corte)
    return cortar_silencios(pasta, arquivo, cs, log, auto=True)["removido"]


def consertar_corte_antigo(pasta, arquivo, log=None):
    """Cenas cortadas até a versão 1.6 ficaram com a imagem cortada e o som inteiro (o filtro de som do
    ffmpeg 8 não cortava): o som atrasava. Refaz o corte a partir do original, com os MESMOS cortes
    (inclusive os revisados à mão). Devolve True se refez."""
    salvo = cortes_salvos(pasta, arquivo)
    if not salvo or salvo.get("manter") or not salvo["cortes"]:
        return False
    if log:
        log(f"Acertando o som da {arquivo} (corte feito na versão anterior)…")
    cortar_silencios(pasta, arquivo, salvo["cortes"], auto=salvo["auto"])
    return True


def fonte_do_corte(pasta, arquivo):
    """O corte sempre parte do original guardado em raw/, se existir."""
    raw = os.path.join(pasta, "raw", arquivo)
    return raw if os.path.isfile(raw) else os.path.join(pasta, arquivo)


RAMPA_EMENDA = 0.004   # 4 ms de rampa em cada emenda: tira o "estalo" do corte sem comer a fala
VERSAO_MONTAGEM = 2    # 2 = som e imagem contados juntos (1.7). Base montado antes disso precisa ser refeito.


ALVO_LUFS = -14.0      # volume das redes (Instagram, TikTok, Shorts, YouTube)
LIMITE_PICO = 0.891    # -1 dBFS: margem para a compressão do áudio das redes não distorcer


def medir_lufs(arq):
    """Volume integrado (LUFS, norma EBU R128) de um arquivo de áudio. None se for só silêncio."""
    p = subprocess.run([ffbin("ffmpeg"), "-hide_banner", "-nostats", "-i", arq, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, **SEM_JANELA)
    achados = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", p.stderr.decode("utf-8", "ignore"))
    try:
        v = float(achados[-1])
    except (IndexError, ValueError):
        return None
    return v if v > -70 else None


def ganho_para_volume_padrao(arq):
    """Quantos dB faltam para o som chegar no volume das redes (limitado a ±12 dB)."""
    v = medir_lufs(arq)
    if v is None:
        return 0.0
    return float(np.clip(ALVO_LUFS - v, -12.0, 12.0))


def base_atualizado(pasta):
    """True se o _trabalho/base.mp4 foi montado pela versão atual (pode só colocar CTA e setas por cima)."""
    try:
        with open(os.path.join(pasta, "_trabalho", "base.versao")) as fh:
            return int(fh.read().strip() or 0) >= VERSAO_MONTAGEM
    except (OSError, ValueError):
        return False


def juntar_trechos_audio(a, quadros, fps):
    """Junta os trechos [quadro inicial, quadro final) do áudio estéreo `a` (48 kHz).
    Cada trecho sai com exatamente o tamanho do vídeo dele, contado pelo total acumulado
    (sem erro que se soma de um corte para o outro)."""
    total = sum(n - m for m, n in quadros)
    out = np.zeros((int(round(total / fps * SR_SAIDA)), 2), a.dtype)
    rampa = int(RAMPA_EMENDA * SR_SAIDA)
    pos_q = 0
    for m, n in quadros:
        o0 = int(round(pos_q / fps * SR_SAIDA))
        o1 = int(round((pos_q + n - m) / fps * SR_SAIDA))
        pos_q += n - m
        i0 = int(round(m / fps * SR_SAIDA))
        L = o1 - o0
        j0, j1 = max(0, i0), min(len(a), i0 + L)
        if j1 > j0:
            out[o0 + j0 - i0: o0 + j1 - i0] = a[j0:j1]      # o que faltar (fim do som) fica em silêncio
        r = min(rampa, L // 2)
        if r > 1:
            if m > 0:                    # começo de trecho que veio depois de um corte
                ini = out[o0:o0 + r].astype(np.float32) * np.linspace(0.0, 1.0, r, dtype=np.float32)[:, None]
                out[o0:o0 + r] = ini.astype(a.dtype)
            if i0 + L < len(a) - 1:      # fim de trecho antes de um corte
                fim = out[o1 - r:o1].astype(np.float32) * np.linspace(1.0, 0.0, r, dtype=np.float32)[:, None]
                out[o1 - r:o1] = fim.astype(a.dtype)
    return out


def cortar_silencios(pasta, arquivo, cortes, log=None, auto=False):
    """Remove os trechos `cortes` da cena. O original fica intacto em raw/ (a cena nunca some da pasta).
    Funciona com vídeos longos: tudo em uma passada, sem guardar quadros na memória."""
    origem = os.path.join(pasta, arquivo)
    raw_dir = os.path.join(pasta, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    raw = os.path.join(raw_dir, arquivo)
    if not os.path.isfile(raw):
        try:
            os.link(origem, raw)            # mesmo arquivo com dois nomes: não ocupa espaço extra
        except OSError:
            shutil.copy2(origem, raw)
    s = sondar(raw)
    D = s["dur"]
    fps = round(min(60.0, s["fps"]), 3)
    cs = sorted([max(0.0, float(x)), min(D, float(y))] for x, y in cortes if float(y) - float(x) > 0.02)
    manter, t = [], 0.0
    for x, y in cs:
        if x > t + 0.02:
            manter.append([t, x])
        t = max(t, y)
    if D > t + 0.02:
        manter.append([t, D])
    # trechos mantidos em QUADROS inteiros: a imagem sai por número de quadro e o som por número de
    # amostra, os dois a partir da mesma conta. Assim nada escorrega, não importa quantos cortes.
    quadros = [[int(round(x * fps)), int(round(y * fps))] for x, y in manter]
    quadros = [[m, n] for m, n in quadros if n - m >= 2]
    if not quadros:
        raise RuntimeError("Esses cortes apagariam a cena inteira.")
    if log:
        log(f"Cortando {arquivo}…")
    total_q = sum(n - m for m, n in quadros)
    sel_v = "+".join(f"between(n,{m},{n - 1})" for m, n in quadros)
    escala = ""
    if max(s["w"], s["h"]) > LADO_MAX:
        escala = (f",scale='if(gt(iw,ih),{LADO_MAX},-2)':'if(gt(iw,ih),-2,{LADO_MAX})':flags=lanczos")
    # fps com start_time=0: o quadro n fica no tempo n/fps do arquivo (o mesmo relógio do som);
    # tpad: se a imagem acabar um pouco antes do som, repete o último quadro em vez de encurtar
    f = (f"[0:v]fps={fps}:start_time=0,tpad=stop_mode=clone:stop_duration=1,select='{sel_v}',"
         f"setpts=N/FRAME_RATE/TB{escala},format=yuv420p[v]")
    tmp = os.path.join(pasta, "_corte_" + arquivo)
    tmp_som = os.path.join(pasta, "_corte_som.pcm")
    entradas_som = []
    if s["audio"]:
        som = juntar_trechos_audio(ler_audio_estereo(raw), quadros, fps)
        som.tofile(tmp_som)
        del som
        entradas_som = ["-f", "s16le", "-ar", str(SR_SAIDA), "-ac", "2", "-i", tmp_som]
    try:
        ff("-i", raw, *entradas_som, "-filter_complex", f, "-map", "[v]", *(["-map", "1:a"] if s["audio"] else []),
           "-c:v", "libx264", "-crf", "15", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", f"{fps}",
           *(["-c:a", "aac", "-b:a", "256k"] if s["audio"] else []), "-movflags", "+faststart",
           "-t", f"{total_q / fps:.4f}", tmp)
        trocar(tmp, origem)
    finally:
        for x in (tmp, tmp_som):
            if os.path.isfile(x):
                os.remove(x)
    try:
        with open(raw + ".cortes.json", "w") as fh:
            json.dump({"cortes": [[round(x, 3), round(y, 3)] for x, y in cs], "auto": bool(auto),
                       "manter": [[round(m / fps, 4), round(n / fps, 4)] for m, n in quadros]}, fh)
    except OSError:
        pass
    novo = sondar(origem)["dur"]
    return {"original": round(D, 2), "cortado": round(novo, 2), "removido": round(D - novo, 2), "cortes": len(cs)}


def restaurar_cena(pasta, arquivo):
    raw = os.path.join(pasta, "raw", arquivo)
    if os.path.isfile(raw):
        trocar(raw, os.path.join(pasta, arquivo))
    try:
        os.remove(raw + ".cortes.json")
    except OSError:
        pass


def cortes_salvos(pasta, arquivo):
    """{"cortes": [[ini, fim], …], "auto": True/False} ou None. auto=False = revisado à mão no painel.
    (Arquivos da versão 1.0/1.1 eram só a lista e contam como automáticos.)"""
    if not os.path.isfile(os.path.join(pasta, "raw", arquivo)):
        return None
    try:
        with open(os.path.join(pasta, "raw", arquivo + ".cortes.json")) as fh:
            j = json.load(fh)
    except Exception:
        return {"cortes": [], "auto": True}
    if isinstance(j, list):
        return {"cortes": j, "auto": True}
    return {"cortes": j.get("cortes") or [], "auto": bool(j.get("auto", True)), "manter": j.get("manter")}


# ============================================================ transcrever arquivo
def _srt_t(t):
    ms = int(round(max(t, 0) * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def transcrever_arquivo(entrada, pasta_saida, nome=None, log=print):
    """Transcreve um áudio/vídeo inteiro (pode ser longo). Grava <nome>.txt e <nome>.srt."""
    os.makedirs(pasta_saida, exist_ok=True)
    nome = nome or os.path.splitext(os.path.basename(entrada))[0]
    log("Extraindo o áudio…")
    a = ler_audio(entrada)
    if not len(a):
        raise RuntimeError("Esse arquivo não tem áudio.")
    D = len(a) / SR
    reconhecedor(log)
    pal = transcrever_audio(a, log=log)
    pars, atual, frases = [], [], 0
    for i, p in enumerate(pal):
        atual.append(p["w"])
        fim_frase = p["w"][-1:] in ".!?…"
        frases += fim_frase
        pausa = (pal[i + 1]["s"] - p["e"]) if i + 1 < len(pal) else 0
        if fim_frase and (pausa > 1.0 or frases >= 5):
            pars.append(" ".join(atual))
            atual, frases = [], 0
    if atual:
        pars.append(" ".join(atual))
    texto = "\n\n".join(pars)
    with open(os.path.join(pasta_saida, nome + ".txt"), "w", encoding="utf-8") as fh:
        fh.write(texto + "\n")
    blocos, c = [], []
    for p in pal:
        c.append(p)
        if len(c) >= 7 or p["w"][-1:] in ".!?…":
            blocos.append(c)
            c = []
    if c:
        blocos.append(c)
    with open(os.path.join(pasta_saida, nome + ".srt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{i + 1}\n{_srt_t(b[0]['s'])} --> {_srt_t(b[-1]['e'])}\n{' '.join(x['w'] for x in b)}\n"
                           for i, b in enumerate(blocos)))
    return {"nome": nome, "texto": texto, "duracao": round(D, 1), "palavras": len(pal)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: python editor.py <pasta-do-video> [opcoes.json]")
        sys.exit(1)
    ops = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
    t0 = time.time()
    r = editar(sys.argv[1], ops)
    r["segundos"] = round(time.time() - t0, 1)
    print(json.dumps(r, ensure_ascii=False, indent=1))
