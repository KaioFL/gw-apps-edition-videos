"""GW Apps Edition Videos — painel local. Abre em http://127.0.0.1:8790"""
import os
import re
import sys
import json
import base64
import hashlib
import time
import shutil
import random
import threading
import traceback
import mimetypes
import subprocess
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import editor   # noqa: E402
import trilhas  # noqa: E402
import atualizador  # noqa: E402

NOME_APP = "GW Apps Edition Videos"
VERSAO = "2.3"
PORTA = int(os.environ.get("GW_PORTA", "8790"))
# BASE = arquivos do programa (no aplicativo instalado é só leitura).
# DADOS = ajustes, estado, prévias e avatares de quem usa. Rodando pela pasta (iniciadores .bat/.command)
# continua tudo junto em "programa"; o aplicativo instalado aponta GW_DADOS para a pasta de dados do sistema.
DADOS = os.path.abspath(os.environ.get("GW_DADOS") or BASE)
ARQ_CFG = os.path.join(DADOS, "config.json")
ARQ_EST = os.path.join(DADOS, "estado.json")
PASTA_PAINEL = os.path.join(BASE, "painel")
PASTA_MARCA = os.path.join(DADOS, "marca")
PASTA_PREVIAS = os.path.join(DADOS, "_previas")
PASTA_AVATARES = os.path.join(DADOS, "avatares")      # fotos dos avatares
RAIZ_PADRAO = os.path.normpath(os.environ.get("GW_VIDEOS") or os.path.join(BASE, "..", "Meus videos"))


def _preparar_dados():
    """Primeira abertura do aplicativo instalado: leva os ajustes e a logo que vêm com o programa para a pasta de dados."""
    if os.path.normcase(DADOS) == os.path.normcase(BASE):
        return
    os.makedirs(DADOS, exist_ok=True)
    if not os.path.isfile(ARQ_CFG) and os.path.isfile(os.path.join(BASE, "config.json")):
        shutil.copy2(os.path.join(BASE, "config.json"), ARQ_CFG)
    if not os.path.isdir(PASTA_MARCA) and os.path.isdir(os.path.join(BASE, "marca")):
        shutil.copytree(os.path.join(BASE, "marca"), PASTA_MARCA)


_preparar_dados()
REDES = ("instagram", "facebook")
PRONTOS, TRANSC, LIXEIRA, ENTRADA = "_Prontos", "_Transcricoes", "_lixeira", "_Entrada"
EXT_TRANSC = (".mp3", ".mp4", ".m4a", ".wav", ".mov", ".webm", ".aac", ".ogg", ".mkv", ".flac")

trava = threading.RLock()
_sem_mini = threading.Semaphore(2)


# ------------------------------------------------------------------ arquivos de estado
def ler_json(p, padrao):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return padrao


def gravar_json(p, v):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(v, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


cfg = ler_json(ARQ_CFG, {})
cfg.setdefault("raiz", RAIZ_PADRAO)
cfg.setdefault("avatares", [])
cfg["opcoes"] = {k: cfg.get("opcoes", {}).get(k, v) for k, v in editor.PADRAO.items()}
if cfg.get("versao_silencio") != 3:
    # 1.2: o corte passou a ser medido pelo volume da fala de cada cena. Mantém o modo e o respiro; o resto volta ao padrão.
    _antigo = cfg.get("silencio", {})
    cfg["silencio"] = {k: _antigo[k] for k in ("modo", "respiro_ms") if k in _antigo}
    cfg["versao_silencio"] = 3
    try:
        gravar_json(ARQ_CFG, cfg)
    except OSError:
        pass
cfg["silencio"] = {k: cfg.get("silencio", {}).get(k, v) for k, v in editor.PADRAO_SILENCIO.items()}
cfg.setdefault("estilos", [])          # 1.8: conjuntos de ajustes salvos com um nome
AUTOMACAO_PADRAO = {"auto_editar": False, "vigiar": True}
cfg["automacao"] = {k: cfg.get("automacao", {}).get(k, v) for k, v in AUTOMACAO_PADRAO.items()}
# 2.1: apagar o vídeo depois de postado (para não lotar o disco)
LIMPEZA_PADRAO = {"ligada": False, "quando": "todas", "o_que": "tudo"}
cfg["limpeza"] = {k: cfg.get("limpeza", {}).get(k, v) for k, v in LIMPEZA_PADRAO.items()}
ARQ_HIST = os.path.join(DADOS, "historico.json")
historico = ler_json(ARQ_HIST, [])
ESPERA_APAGAR = 12        # segundos para desfazer antes de apagar de vez
movendo = {}              # troca da pasta dos vídeos em andamento
if os.path.normpath(cfg["raiz"]) == RAIZ_PADRAO:
    os.makedirs(RAIZ_PADRAO, exist_ok=True)

estado = ler_json(ARQ_EST, {})
for _n, _e in estado.items():
    if _e.get("status") in ("na fila", "editando"):
        _e["status"] = "novo"

fila = {"itens": [], "atual": None, "etapa": "", "feitos": 0, "total": 0, "cancelar": False, "log": []}
opcoes_da_fila = {}
transc_jobs = {}


def raiz():
    return cfg["raiz"]


def pasta_prontos():
    return os.path.join(raiz(), PRONTOS)


def pasta_transc():
    return os.path.join(raiz(), TRANSC)


def nome_valido(n):
    """Nome de vídeo seguro para pasta no Windows e no Mac ('' se não der)."""
    n = re.sub(r'[<>:"/\\|?*#%\x00-\x1f]', "", (n or "")).strip().strip(".").strip()
    return n[:60] if n and not n.startswith(("_", ".")) else ""


def pasta_video(nome):
    n = os.path.basename(nome or "")
    if not n or n.startswith(("_", ".")):
        return None
    p = os.path.join(raiz(), n)
    return p if os.path.isdir(p) else None


# ------------------------------------------------------------------ avatares (pastas)
CORES_AVATAR = ["#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#3B82F6", "#EF4444", "#14B8A6", "#A855F7", "#F97316", "#84CC16"]
_SEM = object()


def avatares():
    return cfg["avatares"]


def avatar_por_id(i):
    return next((a for a in avatares() if a["id"] == i), None) if i else None


def avatar_do_video(nome):
    a = estado.get(nome, {}).get("avatar")
    return a if avatar_por_id(a) else None


def pasta_prontos_de(av_id=None):
    a = avatar_por_id(av_id)
    return os.path.join(pasta_prontos(), a["pasta"]) if a else pasta_prontos()


def final_de(nome, av=_SEM):
    """Vídeo pronto: _Prontos/<nome>.mp4, ou _Prontos/<avatar>/<nome>.mp4 se o vídeo for de um avatar."""
    if av is _SEM:
        av = avatar_do_video(nome)
    return os.path.join(pasta_prontos_de(av), nome + ".mp4")


def localizar_final(nome):
    """Acha o pronto mesmo se ele ficou na pasta de outro avatar (e põe no lugar certo)."""
    certo = final_de(nome)
    if os.path.isfile(certo):
        return certo
    for c in [pasta_prontos()] + [os.path.join(pasta_prontos(), a["pasta"]) for a in avatares()]:
        f = os.path.join(c, nome + ".mp4")
        if f != certo and os.path.isfile(f):
            try:
                os.makedirs(os.path.dirname(certo), exist_ok=True)
                editor.trocar(f, certo, tentativas=2)
                return certo
            except Exception:
                return f
    return certo


def mover_final(nome, de_av, para_av):
    de, para = final_de(nome, de_av), final_de(nome, para_av)
    if de != para and os.path.isfile(de):
        os.makedirs(os.path.dirname(para), exist_ok=True)
        editor.trocar(de, para, mover=True)


def pasta_livre_avatar(nome, ignorar=None):
    base = nome_valido(nome) or "Avatar"
    usadas = {a["pasta"].lower() for a in avatares() if a["id"] != ignorar}
    pasta, k = base, 2
    while pasta.lower() in usadas or (os.path.exists(os.path.join(pasta_prontos(), pasta))
                                      and not any(a["pasta"] == pasta and a["id"] == ignorar for a in avatares())):
        pasta = f"{base} ({k})"
        k += 1
    return pasta


def criar_avatar(nome):
    usadas = {a.get("cor") for a in avatares()}
    cor = next((c for c in CORES_AVATAR if c not in usadas), CORES_AVATAR[len(avatares()) % len(CORES_AVATAR)])
    a = {"id": "av" + "".join(random.choice("0123456789abcdef") for _ in range(8)), "nome": nome,
         "pasta": pasta_livre_avatar(nome), "cor": cor}
    avatares().append(a)
    gravar_json(ARQ_CFG, cfg)
    return a


def nome_novo_video(titulo=""):
    """Nome livre para um vídeo novo: o título (se não existir ainda) ou o próximo número (001, 002…)."""
    t = nome_valido(titulo)
    if t and not os.path.exists(os.path.join(raiz(), t)):
        return t
    usados = {int(x) for x in os.listdir(raiz()) if x.isdigit()}
    k = 1
    while k in usados or os.path.exists(os.path.join(raiz(), f"{k:03d}")):
        k += 1
    return f"{k:03d}"


def foto_avatar(i):
    return os.path.join(PASTA_AVATARES, re.sub(r"\W", "", i) + ".jpg")


def avatares_para_painel():
    out = []
    for a in avatares():
        f = foto_avatar(a["id"])
        out.append({"id": a["id"], "nome": a["nome"], "cor": a.get("cor", "#8B5CF6"), "pasta": a["pasta"],
                    "estilo": a.get("estilo", "") if estilo_por_id(a.get("estilo")) else "",
                    "foto": f"/avatar/{a['id']}.jpg?t={int(os.path.getmtime(f))}" if os.path.isfile(f) else None})
    return out


def estilo_por_id(i):
    return next((e for e in cfg["estilos"] if e["id"] == i), None) if i else None


def opcoes_do_video(nome):
    """Ajustes do painel; se o vídeo é de um avatar com estilo próprio, valem os ajustes desse estilo."""
    o = dict(cfg["opcoes"])
    a = avatar_por_id(avatar_do_video(nome))
    es = estilo_por_id(a.get("estilo")) if a else None
    if es:
        o.update({k: v for k, v in es["opcoes"].items() if k in editor.PADRAO})
    return o


def opcoes_para_fila(nome, so_elementos=False):
    o = opcoes_do_video(nome)
    e = estado.setdefault(nome, {})
    o["headline_texto"] = e.get("headline", "")
    o["_so_elementos"] = so_elementos
    if not so_elementos:
        esc = e.get("musica", "")
        if esc == "@nenhuma":
            o["musica"] = False
        elif esc:
            o["musica"], o["musica_id"] = True, esc
        elif o.get("musica") and o.get("musica_modo") == "variar":
            o["musica_id"] = sortear_musica()
    return o


def pedir_edicao(nomes):
    """Põe os vídeos na fila de edição (chame com a trava)."""
    nomes = [n for n in nomes if pasta_video(n) and not na_fila("editar", n)]
    for n in nomes:
        opcoes_da_fila[n] = opcoes_para_fila(n)
        e = estado.setdefault(n, {})
        e["status"], e["erro"] = "na fila", ""
        enfileirar("editar", n)
    gravar_json(ARQ_EST, estado)
    return nomes


def raiz_ok():
    return os.path.isdir(raiz())


def garantir_raiz():
    """A pasta padrão é recriada se sumir; uma pasta escolhida (ex.: SSD externo) não: ela pode só estar desconectada."""
    if not raiz_ok() and os.path.normcase(os.path.normpath(raiz())) == os.path.normcase(RAIZ_PADRAO):
        os.makedirs(raiz(), exist_ok=True)
    return raiz_ok()


ERRO_RAIZ = "A pasta dos vídeos não está disponível. Se ela fica num SSD ou HD externo, conecte e tente de novo."


def postado_completo(e):
    pg = e.get("postagens") or {}
    return all(r in pg for r in REDES) if cfg["limpeza"]["quando"] == "todas" else bool(pg)


def agendar_limpeza(n):
    """Depois de postar: o vídeo é apagado daqui a alguns segundos (dá tempo de desfazer). Chame com a trava."""
    e = estado.get(n)
    if e is None:
        return False
    if cfg["limpeza"]["ligada"] and postado_completo(e) and not na_fila("editar", n):
        e.setdefault("apagar_em", time.time() + ESPERA_APAGAR)
        return True
    e.pop("apagar_em", None)
    return False


def _tamanho(caminho):
    t = 0
    for d, _, fs in os.walk(caminho):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return t


def resumo_historico():
    r = {}
    for h in historico:
        x = r.setdefault(h.get("avatar") or "", {"n": 0, "instagram": 0, "facebook": 0, "liberado": 0})
        x["n"] += 1
        x["liberado"] += h.get("liberado", 0)
        for rede in REDES:
            if rede in (h.get("postagens") or {}):
                x[rede] += 1
    return r


def apagar_postado(n):
    """Apaga o vídeo postado (as cenas e os arquivos de trabalho; e o pronto, se for "tudo") e guarda no histórico."""
    pasta, e, fin = pasta_video(n), estado.get(n, {}), localizar_final(n)
    manter = cfg["limpeza"]["o_que"] == "trabalho"
    liberado = (_tamanho(pasta) if pasta else 0) + (os.path.getsize(fin) if os.path.isfile(fin) and not manter else 0)
    if not manter and os.path.isfile(fin):
        os.remove(fin)
    if pasta:
        shutil.rmtree(pasta)
    historico.append({"nome": n, "avatar": e.get("avatar", ""), "headline": e.get("headline", ""), "postagens": e.get("postagens", {}),
                      "editado_em": e.get("editado_em", ""), "apagado_em": time.strftime("%Y-%m-%d %H:%M"),
                      "pronto_mantido": bool(manter and os.path.isfile(fin)), "liberado": liberado})
    del historico[:-3000]
    gravar_json(ARQ_HIST, historico)
    estado.pop(n, None)
    gravar_json(ARQ_EST, estado)
    return liberado


def limpar_postados():
    agora = time.time()
    with trava:
        alvos = [n for n, e in estado.items() if e.get("apagar_em") and e["apagar_em"] <= agora]
    for n in alvos:
        with trava:
            e = estado.get(n)
            if not e or not e.get("apagar_em"):
                continue
            if (not cfg["limpeza"]["ligada"] or not postado_completo(e) or na_fila("editar", n)
                    or na_fila("silencio", n) or movendo or not raiz_ok()):
                e.pop("apagar_em", None)
                continue
            try:
                lib = apagar_postado(n)
                fila["log"].append(f"{time.strftime('%H:%M:%S')} Postado e apagado: vídeo {n} (liberou {lib / 1e6:.0f} MB)")
            except OSError as ex:
                e["apagar_em"] = agora + 30
                fila["log"].append(f"{time.strftime('%H:%M:%S')} Não consegui apagar o vídeo {n} agora ({ex.strerror or ex}). Tento de novo em 30 s.")
            del fila["log"][:-200]


def _itens_da_raiz(r):
    try:
        return [x for x in sorted(os.listdir(r)) if not x.startswith(".")]
    except OSError:
        return []


def _juntar(de, para, mesmo_disco, movidos):
    """Junta o conteúdo de uma pasta (ex.: _Prontos) com a que já existe no destino, sem sobrescrever nada."""
    for d, _, fs in os.walk(de):
        for f in fs:
            origem = os.path.join(d, f)
            alvo = os.path.join(para, os.path.relpath(origem, de))
            if os.path.exists(alvo):
                continue
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            (os.rename if mesmo_disco else shutil.copy2)(origem, alvo)
            movidos.append((origem, alvo))


def _apagar_vazias(pasta):
    for d, _, _ in sorted(os.walk(pasta), key=lambda x: -len(x[0])):
        try:
            os.rmdir(d)
        except OSError:
            pass


def fazer_mover_raiz(nova, log):
    """Leva tudo da pasta dos vídeos para a pasta nova. Em outro disco (ex.: SSD), copia tudo antes e só apaga
    a pasta antiga depois que a cópia deu certo: se faltar espaço no meio, nada se perde."""
    velha = movendo.get("de") or raiz()
    itens = _itens_da_raiz(velha)
    mesmo_disco = os.stat(velha).st_dev == os.stat(nova).st_dev
    feitos, juntados, renomes = [], [], {}
    try:
        for i, x in enumerate(itens, 1):
            log(f"Levando {x} para a pasta nova ({i} de {len(itens)})…")
            de, para = os.path.join(velha, x), os.path.join(nova, x)
            if os.path.isdir(de) and x.startswith("_") and os.path.isdir(para):
                _juntar(de, para, mesmo_disco, juntados)
                continue
            if os.path.exists(para):
                if not os.path.isdir(de):
                    continue
                k, base = 2, re.sub(r" \(\d+\)$", "", x)
                while os.path.exists(os.path.join(nova, f"{base} ({k})")):
                    k += 1
                renomes[x] = f"{base} ({k})"
                para = os.path.join(nova, renomes[x])
            if mesmo_disco:
                os.rename(de, para)
            elif os.path.isdir(de):
                shutil.copytree(de, para)
            else:
                shutil.copy2(de, para)
            feitos.append((de, para))
    except Exception as ex:
        if not mesmo_disco:
            log("Deu problema: desfazendo as cópias…")
            for _, para in feitos:
                shutil.rmtree(para, ignore_errors=True) if os.path.isdir(para) else os.remove(para)
            for _, alvo in juntados:
                try:
                    os.remove(alvo)
                except OSError:
                    pass
            movendo.clear()
            raise RuntimeError(f"Não consegui levar os vídeos ({getattr(ex, 'strerror', None) or ex}). "
                               "Continuo usando a pasta antiga e nada foi apagado.")
        movendo.clear()
        raise RuntimeError(f"Parei no meio ({ex}). Parte dos vídeos já está na pasta nova: {nova}")
    with trava:
        for a, b in renomes.items():
            if a in estado:
                estado[b] = estado.pop(a)
            for c in [os.path.join(nova, PRONTOS)] + [os.path.join(nova, PRONTOS, av["pasta"]) for av in avatares()]:
                if os.path.isfile(os.path.join(c, a + ".mp4")) and not os.path.exists(os.path.join(c, b + ".mp4")):
                    os.rename(os.path.join(c, a + ".mp4"), os.path.join(c, b + ".mp4"))
        cfg["raiz"] = nova
        gravar_json(ARQ_CFG, cfg)
        gravar_json(ARQ_EST, estado)
    if not mesmo_disco:
        log("Cópia conferida. Apagando a pasta antiga…")
        for de, _ in feitos:
            shutil.rmtree(de, ignore_errors=True) if os.path.isdir(de) else os.remove(de)
        for origem, _ in juntados:
            try:
                os.remove(origem)
            except OSError:
                pass
    for x in itens:
        if os.path.isdir(os.path.join(velha, x)):
            _apagar_vazias(os.path.join(velha, x))
    movendo.clear()
    log("Pronto: os vídeos agora ficam na pasta nova.")


# ------------------------------------------------------------------ levar as configurações para outro computador
TIPO_CONFIG = "gw-apps-edition-videos/config"


def exportar_config():
    av = []
    for a in avatares():
        x = {k: a[k] for k in ("id", "nome", "cor", "estilo") if k in a}
        f = foto_avatar(a["id"])
        if os.path.isfile(f):
            with open(f, "rb") as fh:
                x["foto_b64"] = base64.b64encode(fh.read()).decode()
        av.append(x)
    return {"tipo": TIPO_CONFIG, "versao": VERSAO, "criado": time.strftime("%Y-%m-%d %H:%M"), "avatares": av,
            "estilos": cfg["estilos"], "opcoes": cfg["opcoes"], "silencio": cfg["silencio"],
            "automacao": cfg["automacao"], "limpeza": cfg["limpeza"]}


def importar_config(j):
    """Junta as configurações de outro computador com as daqui (avatares pelo nome, estilos pelo id ou nome)."""
    if not isinstance(j, dict) or j.get("tipo") != TIPO_CONFIG:
        raise ValueError("Esse arquivo não é de configurações do GW Apps Edition Videos.")
    for k, padrao in (("opcoes", editor.PADRAO), ("silencio", editor.PADRAO_SILENCIO),
                      ("automacao", AUTOMACAO_PADRAO), ("limpeza", LIMPEZA_PADRAO)):
        cfg[k].update({x: v for x, v in (j.get(k) or {}).items() if x in padrao})
    ids_est = {}
    for e in j.get("estilos") or []:
        if not isinstance(e, dict) or not e.get("id") or not e.get("nome"):
            continue
        ops = {k: v for k, v in (e.get("opcoes") or {}).items() if k in editor.PADRAO}
        ja = estilo_por_id(e["id"]) or next((x for x in cfg["estilos"] if x["nome"].lower() == str(e["nome"]).lower()), None)
        if ja:
            ja.update(nome=str(e["nome"])[:40], opcoes=ops)
        else:
            ja = {"id": re.sub(r"\W", "", str(e["id"]))[:20] or "es" + "".join(random.choice("0123456789abcdef") for _ in range(8)),
                  "nome": str(e["nome"])[:40], "opcoes": ops}
            cfg["estilos"].append(ja)
        ids_est[e["id"]] = ja["id"]
    novos = atualizados = 0
    for a in j.get("avatares") or []:
        nome = " ".join(str((a or {}).get("nome", "")).split())[:40]
        if not nome_valido(nome):
            continue
        ja = next((x for x in avatares() if x["nome"].lower() == nome.lower()), None)
        if ja:
            atualizados += 1
        else:
            i = re.sub(r"\W", "", str(a.get("id", "")))[:20]
            if not i or avatar_por_id(i):
                i = "av" + "".join(random.choice("0123456789abcdef") for _ in range(8))
            ja = {"id": i, "nome": nome, "pasta": pasta_livre_avatar(nome), "cor": CORES_AVATAR[len(avatares()) % len(CORES_AVATAR)]}
            avatares().append(ja)
            novos += 1
        if re.fullmatch(r"#[0-9a-fA-F]{6}", str(a.get("cor", ""))):
            ja["cor"] = a["cor"]
        if a.get("estilo") in ids_est:
            ja["estilo"] = ids_est[a["estilo"]]
        if a.get("foto_b64"):
            try:
                foto = base64.b64decode(a["foto_b64"])
                if foto[:3] == b"\xff\xd8\xff" and len(foto) < 8_000_000:
                    os.makedirs(PASTA_AVATARES, exist_ok=True)
                    with open(foto_avatar(ja["id"]), "wb") as fh:
                        fh.write(foto)
            except Exception:
                pass
    gravar_json(ARQ_CFG, cfg)
    return {"novos": novos, "atualizados": atualizados, "estilos": len(ids_est)}


def pasta_downloads():
    for d in (os.path.join(os.path.expanduser("~"), "Downloads"), os.path.join(os.path.expanduser("~"), "Desktop")):
        if os.path.isdir(d):
            return d
    return os.path.expanduser("~")


def base_de(nome):
    """Vídeo editado sem CTA e sem setas (é por cima dele que eles entram)."""
    p = pasta_video(nome)
    return os.path.join(p, "_trabalho", "base.mp4") if p else None


def elementos_de(nome):
    e = estado.get(nome, {})
    return {"cta": e.get("cta", ""), "cta_ini": e.get("cta_ini"), "cta_fim": e.get("cta_fim"), "setas": e.get("setas", [])}


def _tempo(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return round(max(0.0, float(str(v).replace(",", "."))), 2)
    except ValueError:
        return None


def limpar_setas(lista):
    out = []
    for sx in (lista or [])[:30]:
        if not isinstance(sx, dict):
            continue
        n = lambda k, pad, lo, hi: max(lo, min(hi, float(sx.get(k, pad) if isinstance(sx.get(k, pad), (int, float)) else pad)))  # noqa: E731
        cor = str(sx.get("cor", "#FFD84D"))
        out.append({
            "estilo": sx.get("estilo") if sx.get("estilo") in editor.ESTILOS_SETA else "grossa",
            "dir": sx.get("dir") if sx.get("dir") in editor.DIRECOES else "baixo",
            "cor": cor if re.fullmatch(r"#[0-9a-fA-F]{6}", cor) else "#FFD84D",
            "tam": round(n("tam", 22, 4, 80), 1), "anim": sx.get("anim") if sx.get("anim") in ("parada", "balanca", "pisca") else "balanca",
            "x": round(n("x", 0.5, 0, 1), 4), "y": round(n("y", 0.5, 0, 1), 4),
            "ini": _tempo(sx.get("ini")) or 0.0, "fim": _tempo(sx.get("fim")),
        })
        if out[-1]["fim"] is not None and out[-1]["fim"] <= out[-1]["ini"] + 0.1:
            out[-1]["fim"] = None
    return out


def _ordem(d):
    m = re.search(r"\d+", d)
    return (0, int(m.group()), d.lower()) if m else (1, 0, d.lower())


def na_fila(tipo, chave):
    return fila["atual"] == (tipo, chave) or (tipo, chave) in fila["itens"]


def lista_videos():
    r = raiz()
    if not os.path.isdir(r):
        return []
    out = []
    nomes = [d for d in os.listdir(r) if os.path.isdir(os.path.join(r, d)) and not d.startswith(("_", "."))]
    for d in sorted(nomes, key=_ordem):
        p = os.path.join(r, d)
        cenas = editor.listar_cenas(p)
        if not cenas and not na_fila("editar", d) and not na_fila("silencio", d):
            continue
        fin = localizar_final(d)
        e = estado.get(d, {})
        st = e.get("status", "novo")
        if st in ("na fila", "editando") and not na_fila("editar", d):
            st = "novo"
        if st == "novo" and os.path.isfile(fin):
            st = "pronto"
        if st == "pronto" and not os.path.isfile(fin):
            st = "novo"
        nomes_cenas = [os.path.basename(a) for a in cenas]
        base = os.path.join(p, "_trabalho", "base.mp4")
        tem_base = os.path.isfile(base) and os.path.isfile(fin)
        cortadas = sum(1 for a in nomes_cenas if os.path.isfile(os.path.join(p, "raw", a)))
        editado_data = e.get("editado_data", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", editado_data):
            m_data = re.match(r"(\d{2})/(\d{2})", e.get("editado_em", ""))
            if m_data:
                ano = time.localtime(os.path.getmtime(fin)).tm_year if os.path.isfile(fin) else time.localtime().tm_year
                editado_data = f"{ano:04d}-{m_data.group(2)}-{m_data.group(1)}"
        out.append({
            "nome": d, "cenas": len(cenas), "cenas_cortadas": cortadas,
            "cenas_urls": [f"/midia/cena/{quote(d)}/{quote(a)}" for a in nomes_cenas],
            "final": (f"/midia/final/{quote(d)}.mp4?t={int(os.path.getmtime(fin))}") if os.path.isfile(fin) else None,
            "status": st, "etapa": fila["etapa"] if fila["atual"] == ("editar", d) else "",
            "avisos": e.get("avisos", []), "duracao": e.get("duracao"), "erro": e.get("erro", ""),
            "editado_em": e.get("editado_em", ""), "editado_data": editado_data,
            "postado_em": e.get("postado_em", ""), "postagens": e.get("postagens", {}),
            "apagar_em": max(1, round(e["apagar_em"] - time.time())) if e.get("apagar_em") else 0, "headline": e.get("headline", ""),
            "musica": e.get("musica", ""), "musica_usada": e.get("musica_usada", ""),
            "cta": e.get("cta", ""), "cta_ini": e.get("cta_ini"), "cta_fim": e.get("cta_fim"), "setas": e.get("setas", []),
            "base": (f"/midia/base/{quote(d)}.mp4?t={int(os.path.getmtime(base))}") if tem_base else None,
            "dur_base": round(editor.sondar(base if tem_base else fin)["dur"], 2) if os.path.isfile(fin) else None,
            "avatar": avatar_do_video(d) or "",
            "mini": f"/miniatura/{quote(d)}.jpg?t={int(os.path.getmtime(fin if os.path.isfile(fin) else cenas[0]))}" if (os.path.isfile(fin) or cenas) else None,
        })
    return out


_lidos = {}


def _ler_txt(f):
    with open(f, encoding="utf-8") as fh:
        return fh.read()


def _lido(f, ler):
    """Lê o arquivo só quando ele muda (o painel pergunta o estado várias vezes por minuto)."""
    try:
        k = (os.path.getmtime(f), os.path.getsize(f))
    except OSError:
        return ler(f)
    c = _lidos.get(f)
    if not c or c[0] != k:
        if len(_lidos) > 400:
            _lidos.clear()
        c = _lidos[f] = (k, ler(f))
    return c[1]


def lista_transcricoes():
    d = pasta_transc()
    out = []
    if os.path.isdir(d):
        js = [x for x in os.listdir(d) if x.endswith(".json")]
        js.sort(key=lambda x: os.path.getmtime(os.path.join(d, x)), reverse=True)
        for f in js[:100]:
            j = dict(_lido(os.path.join(d, f), lambda x: ler_json(x, {})))
            n = f[:-5]
            txt = os.path.join(d, n + ".txt")
            if os.path.isfile(txt):
                j["texto"] = _lido(txt, _ler_txt)
            j["id"] = n
            j.setdefault("status", "pronto")
            out.append(j)
    for k, j in list(transc_jobs.items()):
        if not any(o["id"] == j["nome"] for o in out):
            out.insert(0, {"id": j["nome"], "arquivo": j["arquivo"],
                           "status": "transcrevendo" if fila["atual"] == ("transcrever", k) else "na fila"})
    return out


def sortear_musica():
    """Uma faixa diferente por vídeo: passa por todas antes de repetir."""
    todas = list(trilhas.TRILHAS) + editor.musicas_do_usuario()
    rot = cfg.setdefault("rotacao", {})
    saco = [f for f in rot.get("saco", []) if f in todas]
    if not saco:
        saco = list(todas)
        random.shuffle(saco)
        if len(saco) > 1 and saco[0] == rot.get("ultima"):
            saco.append(saco.pop(0))
    f = saco.pop(0)
    rot["saco"], rot["ultima"] = saco, f
    gravar_json(ARQ_CFG, cfg)
    return f


def abrir_no_sistema(caminho, selecionar=False):
    try:
        if os.name == "nt":
            if selecionar and os.path.isfile(caminho):
                subprocess.Popen(f'explorer /select,"{caminho}"')
            else:
                os.makedirs(caminho, exist_ok=True) if not os.path.exists(caminho) else None
                os.startfile(caminho)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", caminho] if selecionar and os.path.isfile(caminho) else ["open", caminho])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(caminho) if os.path.isfile(caminho) else caminho])
    except Exception:
        traceback.print_exc()


# ------------------------------------------------------------------ fila de trabalho
def _log(rotulo):
    def log(msg):
        fila["etapa"] = msg
        fila["log"].append(f"{time.strftime('%H:%M:%S')} {rotulo}: {msg}")
        del fila["log"][:-200]
    return log


def fazer_edicao(nome, log):
    pasta = pasta_video(nome)
    if not pasta:
        raise RuntimeError("A pasta desse vídeo não existe mais.")
    o = opcoes_da_fila.pop(nome, None) or dict(cfg["opcoes"])
    cenas = editor.listar_cenas(pasta)
    so_elementos = o.pop("_so_elementos", False)
    if so_elementos and editor.base_atualizado(pasta):
        # só CTA e setas: usa o vídeo já montado (montado por versão antiga = monta tudo de novo)
        base, fin = base_de(nome), final_de(nome)
        if os.path.isfile(base):
            info = editor.aplicar_elementos(pasta, fin, o, elementos_de(nome), log)
            with trava:
                e = estado.setdefault(nome, {})
                if not e.get("postagens"):
                    e.pop("postado_em", None)
                e.update({"status": "pronto", "erro": "", "editado_em": time.strftime("%d/%m %H:%M"),
                          "editado_data": time.strftime("%Y-%m-%d")})
            log("Pronto.")
            return
    for i, arq in enumerate(cenas, 1):
        a = os.path.basename(arq)
        salvo = editor.cortes_salvos(pasta, a)
        if salvo and not salvo["auto"]:
            # cena revisada à mão em "Revisar cortes": fica exatamente como você deixou
            # (se foi cortada numa versão antiga, refaz os mesmos cortes com o som certo)
            editor.consertar_corte_antigo(pasta, a, log)
            continue
        if o.get("cortar_silencio"):
            # sempre a partir do original, com os ajustes de agora (refaz o corte automático anterior)
            log(f"Cortando as pausas da cena {i} de {len(cenas)}…")
            editor.cortar_automatico(pasta, a, cfg["silencio"])
        elif salvo:
            editor.restaurar_cena(pasta, a)   # corte desligado: volta a cena original
    os.makedirs(pasta_prontos(), exist_ok=True)
    info = editor.editar(pasta, o, log, saida=final_de(nome), elementos=elementos_de(nome))
    with trava:
        e = estado.setdefault(nome, {})
        if not e.get("postagens"):
            e.pop("postado_em", None)
        e.update({
            "status": "pronto", "avisos": info["avisos"], "duracao": info["duracao"], "erro": "",
            "editado_em": time.strftime("%d/%m %H:%M"), "editado_data": time.strftime("%Y-%m-%d"),
            "musica_usada": info.get("musica_usada", "")})
    log("Pronto.")


def fazer_silencio(nome, log):
    pasta = pasta_video(nome)
    if not pasta:
        raise RuntimeError("A pasta desse vídeo não existe mais.")
    total = 0.0
    for i, arq in enumerate(editor.listar_cenas(pasta), 1):
        a = os.path.basename(arq)
        log(f"Cortando as pausas da cena {i}…")
        total += editor.cortar_automatico(pasta, a, cfg["silencio"])
    log(f"Pronto: {total:.1f} s de pausa removidos.")


def fazer_transcricao(chave, log):
    job = transc_jobs.get(chave)
    if not job:
        return
    d = pasta_transc()
    t0 = time.time()
    try:
        r = editor.transcrever_arquivo(job["caminho"], d, job["nome"], log)
        gravar_json(os.path.join(d, r["nome"] + ".json"), {
            "arquivo": job["arquivo"], "duracao": r["duracao"], "palavras": r["palavras"],
            "feito_em": time.strftime("%d/%m %H:%M"), "segundos": round(time.time() - t0), "status": "pronto"})
        log("Pronto.")
    except Exception as ex:
        gravar_json(os.path.join(d, job["nome"] + ".json"), {"arquivo": job["arquivo"], "status": "erro", "erro": str(ex)[:300]})
        raise
    finally:
        transc_jobs.pop(chave, None)


# ------------------------------------------------------------------ pasta de entrada (vigiada)
LEIA_ENTRADA = """PASTA DE ENTRADA - GW Apps Edition Videos

Tudo o que você colocar aqui entra sozinho no programa (com ele aberto):

  - Um vídeo solto aqui            -> vira um vídeo novo, sem pasta de avatar.
  - Uma pasta com o nome do avatar -> os vídeos dentro dela entram na pasta desse avatar
    (ex.: _Entrada/Ana/video1.mp4). Se o avatar ainda não existe, ele é criado.
  - Uma pasta dentro da do avatar  -> vira UM vídeo feito de cenas, na ordem dos números
    (ex.: _Entrada/Ana/Receita de bolo/cena 1.mp4, cena 2.mp4...).

Os arquivos saem daqui quando entram no programa. Com a opção "Editar sozinho quando chegar"
ligada (em Automação), o vídeo já vai direto para a fila de edição.
"""
_vistos = {}


def pasta_entrada():
    return os.path.join(raiz(), ENTRADA)


def preparar_entrada():
    if not os.path.isdir(raiz()):
        return
    try:
        os.makedirs(pasta_entrada(), exist_ok=True)
        leia = os.path.join(pasta_entrada(), "LEIA-ME.txt")
        if not os.path.isfile(leia):
            with open(leia, "w", encoding="utf-8") as fh:
                fh.write(LEIA_ENTRADA)
    except OSError:
        pass


def _estavel(f):
    """True quando o arquivo parou de crescer há alguns segundos e ninguém está gravando nele."""
    try:
        st = os.stat(f)
    except OSError:
        return False
    marca, agora = (st.st_size, st.st_mtime), time.time()
    ant = _vistos.get(f)
    if not ant or ant[0] != marca:
        _vistos[f] = (marca, agora)
        return False
    if agora - ant[1] < 4 or not st.st_size:
        return False
    try:
        with open(f, "r+b"):
            pass
    except OSError:
        return False
    return True


def _importar(arquivos, titulo, nome_avatar):
    """Cria um vídeo com estes arquivos (na ordem) e tira eles da pasta de entrada."""
    with trava:
        av = None
        if nome_avatar:
            av = next((a for a in avatares() if nome_avatar.lower() in (a["nome"].lower(), a["pasta"].lower())), None)
            if not av and nome_valido(nome_avatar):
                av = criar_avatar(" ".join(nome_avatar.split())[:40])
        video = nome_novo_video(titulo)
        pasta = os.path.join(raiz(), video)
        os.makedirs(pasta)
        for i, f in enumerate(arquivos, 1):
            base, ext = os.path.splitext(os.path.basename(f))
            base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
            base = re.sub(r"^\d+\s*-\s*", "", base) or "cena"
            shutil.move(f, os.path.join(pasta, f"{i:02d} - {base}{ext.lower()}"))
            _vistos.pop(f, None)
        if av:
            estado.setdefault(video, {})["avatar"] = av["id"]
        gravar_json(ARQ_EST, estado)
        fila["log"].append(f"{time.strftime('%H:%M:%S')} Entrada: vídeo {video} chegou" + (f" em {av['nome']}" if av else ""))
        del fila["log"][:-200]
        if cfg["automacao"]["auto_editar"]:
            pedir_edicao([video])
    return video


def varrer_entrada():
    d = pasta_entrada()
    if not os.path.isdir(d):
        preparar_entrada()
        return
    eh_video = lambda n: n.lower().endswith(editor.EXT_VIDEO) and not n.startswith(("_", ".", "~"))  # noqa: E731
    for n in sorted(os.listdir(d), key=_ordem):
        f = os.path.join(d, n)
        if os.path.isfile(f):
            if eh_video(n) and _estavel(f):
                _importar([f], os.path.splitext(n)[0], None)
        elif os.path.isdir(f) and not n.startswith(("_", ".")):
            for m in sorted(os.listdir(f), key=_ordem):
                g = os.path.join(f, m)
                if os.path.isfile(g):
                    if eh_video(m) and _estavel(g):
                        _importar([g], os.path.splitext(m)[0], n)
                elif os.path.isdir(g) and not m.startswith(("_", ".")):
                    cenas = [os.path.join(g, x) for x in sorted(os.listdir(g), key=_ordem)
                             if eh_video(x) and os.path.isfile(os.path.join(g, x))]
                    if cenas and all([_estavel(c) for c in cenas]):
                        _importar(cenas, m, n)
                        try:
                            os.rmdir(g)
                        except OSError:
                            pass


def vigia():
    while True:
        time.sleep(2)
        try:
            limpar_postados()
            if cfg["automacao"]["vigiar"] and not movendo:
                varrer_entrada()
        except Exception:
            traceback.print_exc()
            time.sleep(10)


# ------------------------------------------------------------------ versão antiga (pasta com iniciador .bat/.command)
LEIA_ANTIGO = """ESTA VERSÃO FOI SUBSTITUÍDA PELO APLICATIVO

Os seus vídeos, vídeos prontos, avatares, transcrições e ajustes foram levados para o aplicativo
GW Apps Edition Videos (ícone na Área de Trabalho / Menu Iniciar / Aplicativos).

Onde ficam agora:
  Windows: pasta Vídeos > GW Apps Edition Videos > Meus videos
  Mac:     pasta Filmes > GW Apps Edition Videos > Meus videos

Os iniciadores desta pasta foram renomeados para "(versão antiga) ..." para não abrir a versão
velha por engano. Quando quiser, pode apagar esta pasta inteira.
"""
migracao = {}


def _pastas_antigas():
    """Pastas da versão antiga (a que abre por .bat/.command) nos lugares de costume."""
    casa = os.path.expanduser("~")
    bases = [casa] + [os.path.join(casa, x) for x in ("Downloads", "Desktop", "Documents", "Área de Trabalho", "Documentos")]
    bases += [os.path.join(casa, "OneDrive", x) for x in ("Desktop", "Documents", "Área de Trabalho", "Documentos")]
    eh_antiga = lambda d: (os.path.isfile(os.path.join(d, "programa", "app.py"))  # noqa: E731
                           and os.path.isfile(os.path.join(d, "programa", "editor.py")))
    achadas = []
    for b in bases:
        try:
            nomes = os.listdir(b)
        except OSError:
            continue
        for n in nomes:
            d = os.path.join(b, n)
            if not os.path.isdir(d):
                continue
            if eh_antiga(d):
                achadas.append(d)
            elif re.search(r"gw|edition|editor", n, re.I):
                try:
                    for m in os.listdir(d):
                        if eh_antiga(os.path.join(d, m)):
                            achadas.append(os.path.join(d, m))
                except OSError:
                    pass
    eu = os.path.normcase(os.path.abspath(os.path.join(BASE, "..")))
    return [d for d in dict.fromkeys(achadas) if os.path.normcase(os.path.abspath(d)) != eu]


def _pastas_de_video(r):
    try:
        return [d for d in sorted(os.listdir(r), key=_ordem) if os.path.isdir(os.path.join(r, d)) and not d.startswith(("_", "."))]
    except OSError:
        return []


def migrar_de(antiga):
    """Traz vídeos, prontos, avatares, transcrições e ajustes da versão antiga. Devolve (vídeos, avatares) ou None."""
    prog = os.path.join(antiga, "programa")
    c_old = ler_json(os.path.join(prog, "config.json"), {})
    e_old = ler_json(os.path.join(prog, "estado.json"), {})
    raiz_padrao_old = os.path.normpath(os.path.join(antiga, "Meus videos"))
    raiz_old = os.path.normpath(c_old.get("raiz") or raiz_padrao_old)
    vids = _pastas_de_video(raiz_old)
    if not vids and not c_old.get("avatares"):
        return None
    with trava:
        zerado = not _pastas_de_video(raiz())
        if zerado:
            for k, padrao in (("opcoes", editor.PADRAO), ("silencio", editor.PADRAO_SILENCIO)):
                cfg[k].update({x: v for x, v in (c_old.get(k) or {}).items() if x in padrao})
        mapa, pastas_old = {}, {}
        for a in c_old.get("avatares", []):
            if not a.get("id") or not a.get("nome"):
                continue
            pastas_old[a["id"]] = a.get("pasta", "")
            ja = next((x for x in avatares() if x["nome"].lower() == a["nome"].lower()), None)
            if ja:
                mapa[a["id"]] = ja["id"]
                continue
            novo = {"id": a["id"] if not avatar_por_id(a["id"]) else "av" + "".join(random.choice("0123456789abcdef") for _ in range(8)),
                    "nome": a["nome"], "pasta": pasta_livre_avatar(a["nome"]), "cor": a.get("cor") or CORES_AVATAR[0]}
            avatares().append(novo)
            mapa[a["id"]] = novo["id"]
            foto = os.path.join(prog, "avatares", re.sub(r"\W", "", a["id"]) + ".jpg")
            if os.path.isfile(foto):
                os.makedirs(PASTA_AVATARES, exist_ok=True)
                shutil.copy2(foto, foto_avatar(novo["id"]))
        trazidos = 0
        if vids and zerado and os.path.normcase(raiz_old) != os.path.normcase(raiz_padrao_old):
            # a versão antiga usava uma pasta escolhida pela pessoa: o aplicativo passa a usar a mesma pasta
            cfg["raiz"] = raiz_old
            for d in vids:
                ent = dict(e_old.get(d, {}))
                if ent.get("avatar") in mapa:
                    ent["avatar"] = mapa[ent["avatar"]]
                estado[d] = ent
            trazidos = len(vids)
        else:
            os.makedirs(raiz(), exist_ok=True)
            for d in vids:
                destino = d if not os.path.exists(os.path.join(raiz(), d)) else nome_novo_video("")
                shutil.move(os.path.join(raiz_old, d), os.path.join(raiz(), destino))
                ent = dict(e_old.get(d, {}))
                av_old = ent.get("avatar")
                ent["avatar"] = mapa.get(av_old, "") if av_old else ""
                if not ent["avatar"]:
                    ent.pop("avatar")
                if ent.get("status") in ("na fila", "editando"):
                    ent["status"] = "novo"
                estado[destino] = ent
                fin_old = os.path.join(raiz_old, PRONTOS, pastas_old.get(av_old, "") if av_old else "", d + ".mp4")
                if not os.path.isfile(fin_old):
                    fin_old = os.path.join(raiz_old, PRONTOS, d + ".mp4")
                if os.path.isfile(fin_old):
                    fin = final_de(destino)
                    os.makedirs(os.path.dirname(fin), exist_ok=True)
                    shutil.move(fin_old, fin)
                trazidos += 1
            t_old = os.path.join(raiz_old, TRANSC)
            if os.path.isdir(t_old):
                os.makedirs(pasta_transc(), exist_ok=True)
                for f in os.listdir(t_old):
                    if not os.path.exists(os.path.join(pasta_transc(), f)):
                        shutil.move(os.path.join(t_old, f), os.path.join(pasta_transc(), f))
        gravar_json(ARQ_CFG, cfg)
        gravar_json(ARQ_EST, estado)
    try:
        with open(os.path.join(antiga, "LEIA - VERSAO SUBSTITUIDA.txt"), "w", encoding="utf-8") as fh:
            fh.write(LEIA_ANTIGO)
        for n in os.listdir(antiga):
            if n.startswith("Abrir ") and n.endswith((".bat", ".command")):
                os.replace(os.path.join(antiga, n), os.path.join(antiga, "(versão antiga) " + n))
    except OSError:
        pass
    return trazidos, len(mapa)


def migrar_versao_antiga():
    """No aplicativo instalado: procura a versão antiga uma vez por pasta e traz tudo dela."""
    if os.path.normcase(DADOS) == os.path.normcase(BASE):
        return
    feitas = set(cfg.get("migradas", []))
    for antiga in _pastas_antigas():
        chave = os.path.normcase(os.path.abspath(antiga))
        if chave in feitas:
            continue
        try:
            r = migrar_de(antiga)
            if r:
                migracao.update(videos=migracao.get("videos", 0) + r[0], avatares=migracao.get("avatares", 0) + r[1],
                                quando=time.strftime("%Y-%m-%d %H:%M:%S"))
                fila["log"].append(f"{time.strftime('%H:%M:%S')} Versão antiga: {r[0]} vídeo(s) e {r[1]} avatar(es) trazidos de {antiga}")
        except Exception:
            traceback.print_exc()
        feitas.add(chave)
        with trava:
            cfg["migradas"] = sorted(feitas)
            gravar_json(ARQ_CFG, cfg)


def trabalhador():
    while True:
        with trava:
            if fila["cancelar"]:
                for t, k in fila["itens"]:
                    if t == "editar":
                        estado.setdefault(k, {})["status"] = "novo"
                    if t == "transcrever":
                        transc_jobs.pop(k, None)
                fila["itens"].clear()
                fila["cancelar"] = False
                gravar_json(ARQ_EST, estado)
            job = fila["itens"].pop(0) if fila["itens"] else None
            fila["atual"] = job
            if not job:
                fila["etapa"] = ""
            elif job[0] == "editar":
                estado.setdefault(job[1], {}).update(status="editando", erro="")
        if not job:
            time.sleep(0.4)
            continue
        tipo, chave = job
        rotulo = (transc_jobs.get(chave, {}).get("arquivo", chave) if tipo == "transcrever"
                  else "Pasta dos vídeos" if tipo == "mover_raiz" else chave)
        log = _log(rotulo)
        fila["etapa"] = "Começando…"
        try:
            if tipo == "editar":
                fazer_edicao(chave, log)
            elif tipo == "silencio":
                fazer_silencio(chave, log)
            elif tipo == "transcrever":
                fazer_transcricao(chave, log)
            elif tipo == "mover_raiz":
                fazer_mover_raiz(chave, log)
        except Exception as ex:
            traceback.print_exc()
            msg = str(ex)[:400]
            if tipo == "editar":
                with trava:
                    estado.setdefault(chave, {}).update(status="erro", erro=msg)
            log("Erro: " + msg[:200])
        finally:
            with trava:
                fila["feitos"] += 1
                fila["atual"] = None
                gravar_json(ARQ_EST, estado)


def enfileirar(tipo, chave):
    if not fila["atual"] and not fila["itens"]:
        fila["feitos"], fila["total"] = 0, 0
    fila["itens"].append((tipo, chave))
    fila["total"] += 1


# ------------------------------------------------------------------ servidor
HOSTS = {f"127.0.0.1:{PORTA}", f"localhost:{PORTA}"}
ORIGENS = {None, f"http://127.0.0.1:{PORTA}", f"http://localhost:{PORTA}"}


class Painel(BaseHTTPRequestHandler):
    server_version = "GWEditionVideos"

    def log_message(self, *a):
        pass

    # ---- respostas
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _bytes(self, b, tipo, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _arquivo(self, f, base):
        base = os.path.realpath(base)
        f = os.path.realpath(f)
        if not (f == base or f.startswith(base + os.sep)) or not os.path.isfile(f):
            return self.send_error(404)
        tam = os.path.getsize(f)
        a, b = 0, tam - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                a = int(m.group(1))
                if m.group(2):
                    b = min(int(m.group(2)), tam - 1)
            else:
                a = max(0, tam - int(m.group(2)))
            if a > b:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{tam}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{tam}")
        else:
            self.send_response(200)
        tipo = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if f.lower().endswith(".mp4"):
            tipo = "video/mp4"
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        pos, resta = a, b - a + 1
        try:
            while resta > 0:
                # abre e fecha a cada pedaço: assim o Windows consegue trocar o arquivo durante a edição
                with open(f, "rb") as fh:
                    fh.seek(pos)
                    pedaco = fh.read(min(1 << 20, resta))
                if not pedaco:
                    break
                self.wfile.write(pedaco)
                pos += len(pedaco)
                resta -= len(pedaco)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _host_ok(self):
        if self.headers.get("Host") not in HOSTS:
            self.send_error(403)
            return False
        return True

    def _receber(self, destino):
        n = int(self.headers.get("Content-Length") or 0)
        with open(destino, "wb") as fh:
            while n > 0:
                pedaco = self.rfile.read(min(1 << 20, n))
                if not pedaco:
                    break
                fh.write(pedaco)
                n -= len(pedaco)
        return n == 0

    # ---- GET
    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        p = unquote(u.path)
        q = parse_qs(u.query)
        if p in ("/", "/index.html"):
            with open(os.path.join(PASTA_PAINEL, "index.html"), "rb") as fh:
                return self._bytes(fh.read(), "text/html; charset=utf-8")
        if p == "/api/ping":
            return self._json({"app": NOME_APP, "versao": VERSAO})
        if p in ("/marca", "/favicon.ico"):
            for pasta in (PASTA_MARCA, os.path.join(BASE, "marca")):
                for nome, tipo in (("logo.png", "image/png"), ("logo.svg", "image/svg+xml")):
                    f = os.path.join(pasta, nome)
                    if os.path.isfile(f):
                        with open(f, "rb") as fh:
                            return self._bytes(fh.read(), tipo)
            return self.send_error(404)
        m = re.match(r"^/fontes/([\w.-]+\.(?:ttf|otf))$", p)
        if m:
            return self._arquivo(os.path.join(editor.PASTA_FONTES, m.group(1)), editor.PASTA_FONTES)
        if p == "/api/estado":
            with trava:
                atual = fila["atual"]
                rot_atual = None
                if atual:
                    rot_atual = transc_jobs.get(atual[1], {}).get("arquivo", atual[1]) if atual[0] == "transcrever" else atual[1]
                r = {
                    "app": NOME_APP, "versao": VERSAO, "raiz": raiz(), "raiz_existe": os.path.isdir(raiz()), "prontos": pasta_prontos(),
                    "opcoes": cfg["opcoes"], "silencio": cfg["silencio"], "videos": lista_videos(), "avatares": avatares_para_painel(),
                    "musicas": editor.musicas_do_usuario(),
                    "trilhas": [{"id": k, "nome": v[0]} for k, v in trilhas.TRILHAS.items()],
                    "fontes_legenda": list(editor.FONTES_LEGENDA), "fontes_headline": list(editor.FONTES_HEADLINE),
                    "transcricoes": lista_transcricoes(),
                    "silencio_fila": [k for t, k in fila["itens"] if t == "silencio"] + ([atual[1]] if atual and atual[0] == "silencio" else []),
                    "fila": {"atual": rot_atual, "tipo": atual[0] if atual else None, "etapa": fila["etapa"],
                             "restantes": len(fila["itens"]), "feitos": fila["feitos"], "total": fila["total"],
                             "log": fila["log"][-14:]},
                    "estilos": cfg["estilos"], "automacao": cfg["automacao"], "entrada": pasta_entrada(),
                    "migracao": migracao, "limpeza": cfg["limpeza"], "historico": resumo_historico(),
                    "movendo": bool(movendo), "atualizacao": atualizador.resumo(),
                }
            # o painel manda a marca da última resposta: se nada mudou, a resposta é só "igual" (bem mais leve)
            b = json.dumps(r, ensure_ascii=False).encode("utf-8")
            marca = hashlib.md5(b).hexdigest()[:16]
            if (q.get("h") or [""])[0] == marca:
                return self._json({"igual": True, "h": marca})
            return self._bytes(b[:-1] + f', "h": "{marca}"}}'.encode(), "application/json; charset=utf-8")
        if p == "/api/espaco":
            r = {"raiz": raiz(), "existe": raiz_ok(), "padrao": os.path.normcase(os.path.normpath(raiz())) == os.path.normcase(RAIZ_PADRAO)}
            if r["existe"]:
                u = shutil.disk_usage(raiz())
                r.update(livre=u.free, total=u.total, videos=len(lista_videos()))
            return self._json(r)
        if p == "/api/historico":
            return self._json({"itens": historico[-500:][::-1]})
        if p == "/api/silencio/lista":
            nome = (q.get("video") or [""])[0]
            pasta = pasta_video(nome)
            if not pasta:
                return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
            cs = []
            for i, arq in enumerate(editor.listar_cenas(pasta), 1):
                a = os.path.basename(arq)
                raw = os.path.join(pasta, "raw", a)
                tem_raw = os.path.isfile(raw)
                cs.append({"rotulo": f"Cena {i}", "arquivo": a, "dur": round(editor.sondar(arq)["dur"], 2),
                           "cortada": tem_raw, "dur_original": round(editor.sondar(raw)["dur"], 2) if tem_raw else None})
            return self._json({"ok": True, "video": nome, "cenas": cs})
        if p == "/api/legenda":
            pasta = pasta_video((q.get("video") or [""])[0])
            if not pasta:
                return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
            try:
                cs = []
                for i, arq in enumerate(editor.listar_cenas(pasta), 1):
                    a = os.path.basename(arq)
                    if not editor.sondar(arq)["audio"]:
                        cs.append({"arquivo": a, "rotulo": f"Cena {i}", "texto": "", "sem_audio": True})
                        continue
                    ent = editor.transcricao_da_cena(pasta, a)
                    cs.append({"arquivo": a, "rotulo": f"Cena {i}", "texto": " ".join(w["w"] for w in ent["palavras"]),
                               "ouvido": " ".join(w["w"] for w in ent["asr"]), "corrigida": bool(ent.get("corrigida"))})
                return self._json({"ok": True, "video": os.path.basename(pasta), "cenas": cs,
                                   "na_fila": na_fila("editar", os.path.basename(pasta))})
            except Exception as ex:
                traceback.print_exc()
                return self._json({"ok": False, "erro": str(ex)[:300]}, 500)
        m = re.match(r"^/midia/cena/([^/]+)/(raw/)?([^/]+)$", p)
        if m:
            pasta = pasta_video(m.group(1))
            if not pasta:
                return self.send_error(404)
            return self._arquivo(os.path.join(pasta, (m.group(2) or "").rstrip("/"), m.group(3)), pasta)
        m = re.match(r"^/midia/base/([^/]+)\.mp4$", p)
        if m:
            pasta = pasta_video(os.path.basename(m.group(1)))
            if not pasta:
                return self.send_error(404)
            return self._arquivo(os.path.join(pasta, "_trabalho", "base.mp4"), pasta)
        if p == "/seta.png":
            from io import BytesIO
            g = lambda k, pad: (q.get(k) or [pad])[0]  # noqa: E731
            cor = g("cor", "#FFD84D")
            cor = cor if re.fullmatch(r"#[0-9a-fA-F]{6}", cor) else "#FFD84D"
            est = g("estilo", "grossa")
            est = est if est in editor.ESTILOS_SETA else "grossa"
            dr = g("dir", "baixo")
            dr = dr if dr in editor.DIRECOES else "baixo"
            buf = BytesIO()
            editor.desenhar_seta(est, dr, cor, 240).save(buf, "PNG")
            return self._bytes(buf.getvalue(), "image/png", {"Cache-Control": "max-age=86400"})
        m = re.match(r"^/midia/final/([^/]+)\.mp4$", p)
        if m:
            return self._arquivo(localizar_final(os.path.basename(m.group(1))), pasta_prontos())
        m = re.match(r"^/avatar/(\w+)\.jpg$", p)
        if m:
            return self._arquivo(foto_avatar(m.group(1)), PASTA_AVATARES)
        m = re.match(r"^/miniatura/([^/]+)\.jpg$", p)
        if m:
            n = os.path.basename(m.group(1))
            pasta = pasta_video(n)
            if not pasta:
                return self.send_error(404)
            fin = localizar_final(n)
            cenas = editor.listar_cenas(pasta)
            fonte = fin if os.path.isfile(fin) else (cenas[0] if cenas else None)
            if not fonte:
                return self.send_error(404)
            os.makedirs(PASTA_PREVIAS, exist_ok=True)
            f = os.path.join(PASTA_PREVIAS, "mini_" + re.sub(r"\W", "_", n) + f"_{int(os.path.getmtime(fonte))}.jpg")
            if not os.path.isfile(f):
                with _sem_mini:
                    if not os.path.isfile(f):
                        for velho in os.listdir(PASTA_PREVIAS):
                            if velho.startswith("mini_" + re.sub(r"\W", "_", n) + "_"):
                                try:
                                    os.remove(os.path.join(PASTA_PREVIAS, velho))
                                except OSError:
                                    pass
                        try:
                            t = min(1.0, editor.sondar(fonte)["dur"] / 3)
                            editor.ff("-ss", f"{t:.2f}", "-i", fonte, "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "4", f)
                        except Exception:
                            return self.send_error(404)    # arquivo com defeito: o painel só não mostra a miniatura
            return self._arquivo(f, PASTA_PREVIAS)
        m = re.match(r"^/ouvir/(.+)$", p)
        if m:
            esc = m.group(1)
            if esc in trilhas.TRILHAS:
                os.makedirs(PASTA_PREVIAS, exist_ok=True)
                f = os.path.join(PASTA_PREVIAS, "trilha_" + esc[1:] + ".wav")
                if not os.path.isfile(f):
                    trilhas.gerar(esc, 24, f)
                return self._arquivo(f, PASTA_PREVIAS)
            return self._arquivo(os.path.join(editor.PASTA_MUSICAS, os.path.basename(esc)), editor.PASTA_MUSICAS)
        m = re.match(r"^/previa/([\w-]+\.(?:jpg|png))$", p)
        if m:
            return self._arquivo(os.path.join(PASTA_PREVIAS, m.group(1)), PASTA_PREVIAS)
        m = re.match(r"^/transcricao/(.+)\.(txt|srt)$", p)
        if m:
            f = os.path.join(pasta_transc(), os.path.basename(m.group(1)) + "." + m.group(2))
            if not os.path.isfile(f):
                return self.send_error(404)
            with open(f, "rb") as fh:
                return self._bytes(fh.read(), "text/plain; charset=utf-8",
                                   {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(os.path.basename(f))})
        self.send_error(404)

    # ---- POST
    def do_POST(self):
        if not self._host_ok():
            return
        if self.headers.get("Origin") not in ORIGENS:
            return self._json({"ok": False, "erro": "origem não permitida"}, 403)
        p = urlparse(self.path).path
        try:
            return self._post(p)
        except Exception as ex:
            traceback.print_exc()
            return self._json({"ok": False, "erro": str(ex)[:300]}, 500)

    def _post(self, p):
        h = lambda k: unquote(self.headers.get(k, "") or "")  # noqa: E731
        if p == "/api/janela/focar":
            # o aplicativo foi aberto de novo: em vez de abrir outro, traz a janela que já está aberta
            if ao_pedir_foco:
                threading.Thread(target=ao_pedir_foco, daemon=True).start()
                return self._json({"ok": True})
            return self._json({"ok": False})
        if p == "/api/atualizacao/verificar":
            atualizador.verificar(VERSAO, forcar=True)
            return self._json({"ok": True, "atualizacao": atualizador.resumo()})
        if p == "/api/atualizacao/instalar":
            if ocupado() or movendo:
                return self._json({"ok": False, "erro": "Espere a fila de edição terminar para atualizar."}, 409)
            ok = atualizador.instalar(ao_sair or (lambda: os._exit(0)))
            return self._json({"ok": ok, "erro": "" if ok else "Não há atualização para instalar agora."})
        if p == "/api/config/exportar":
            nome = f"GW-configuracoes-{time.strftime('%Y-%m-%d')}.gwconfig"
            arq = os.path.join(pasta_downloads(), nome)
            with trava:
                dados = exportar_config()
            with open(arq, "w", encoding="utf-8") as fh:
                json.dump(dados, fh, ensure_ascii=False)
            abrir_no_sistema(arq, selecionar=True)
            return self._json({"ok": True, "arquivo": arq, "avatares": len(dados["avatares"]), "estilos": len(dados["estilos"])})
        if p == "/api/config/importar":
            d = self._corpo()
            try:
                with trava:
                    r = importar_config(d.get("config"))
            except ValueError as ex:
                return self._json({"ok": False, "erro": str(ex)}, 400)
            return self._json({"ok": True, **r})
        if p == "/api/escolher-pasta":
            if not ao_escolher_pasta:
                return self._json({"ok": False, "sem_dialogo": True})
            c = ao_escolher_pasta(raiz() if raiz_ok() else os.path.expanduser("~"))
            return self._json({"ok": bool(c), "caminho": c or "", "cancelado": not c})
        if p == "/api/enviar-cena":
            nome_arq = os.path.basename(h("X-Nome") or "cena.mp4")
            base, ext = os.path.splitext(nome_arq)
            if ext.lower() not in editor.EXT_VIDEO:
                return self._json({"ok": False, "erro": "Só aceito vídeos MP4, MOV, WEBM ou M4V."}, 400)
            if movendo:
                return self._json({"ok": False, "erro": "Estou levando os vídeos para a pasta nova. Espere terminar."}, 409)
            if not garantir_raiz():
                return self._json({"ok": False, "erro": ERRO_RAIZ}, 409)
            with trava:
                video = os.path.basename(h("X-Video"))
                if video:
                    if not pasta_video(video):
                        return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
                else:
                    video = nome_novo_video(h("X-Titulo"))
                    os.makedirs(os.path.join(raiz(), video))
                    av = h("X-Avatar")
                    if avatar_por_id(av):
                        estado.setdefault(video, {})["avatar"] = av
                        gravar_json(ARQ_EST, estado)
            pasta = os.path.join(raiz(), video)
            i = max(1, int(h("X-Indice") or 1))
            base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .") or "cena"
            base = re.sub(r"^\d+\s*-\s*", "", base) or "cena"
            destino = os.path.join(pasta, f"{i:02d} - {base}{ext.lower()}")
            k = 2
            while os.path.exists(destino):
                destino = os.path.join(pasta, f"{i:02d} - {base} ({k}){ext.lower()}")
                k += 1
            tmp = os.path.join(pasta, "_envio_" + os.path.basename(destino))
            if not self._receber(tmp):
                os.remove(tmp)
                return self._json({"ok": False, "erro": "O envio foi interrompido."}, 400)
            os.replace(tmp, destino)
            return self._json({"ok": True, "video": video})

        if p == "/api/avatar/foto":
            a = avatar_por_id(h("X-Avatar"))
            if not a:
                return self._json({"ok": False, "erro": "Pasta não encontrada."}, 404)
            if int(self.headers.get("Content-Length") or 0) > 25 * 1024 * 1024:
                return self._json({"ok": False, "erro": "Imagem grande demais (máximo 25 MB)."}, 400)
            os.makedirs(PASTA_AVATARES, exist_ok=True)
            tmp = foto_avatar(a["id"]) + ".envio"
            if not self._receber(tmp):
                os.remove(tmp)
                return self._json({"ok": False, "erro": "O envio foi interrompido."}, 400)
            try:
                from PIL import Image, ImageOps
                im = ImageOps.exif_transpose(Image.open(tmp)).convert("RGB")
                ImageOps.fit(im, (480, 600), Image.LANCZOS, centering=(0.5, 0.35)).save(foto_avatar(a["id"]), quality=88)
            except Exception:
                return self._json({"ok": False, "erro": "Não consegui abrir essa imagem. Use JPG, PNG ou WEBP."}, 400)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return self._json({"ok": True})

        if p == "/api/transcrever/enviar":
            nome_arq = os.path.basename(h("X-Nome") or "arquivo")
            base, ext = os.path.splitext(nome_arq)
            if ext.lower() not in EXT_TRANSC:
                return self._json({"ok": False, "erro": "Formato não suportado."}, 400)
            base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .") or "arquivo"
            if movendo or not garantir_raiz():
                return self._json({"ok": False, "erro": ERRO_RAIZ}, 409)
            d = pasta_transc()
            entrada = os.path.join(d, "_arquivos")
            os.makedirs(entrada, exist_ok=True)
            with trava:
                final = base
                k = 2
                while os.path.exists(os.path.join(d, final + ".json")) or any(j["nome"] == final for j in transc_jobs.values()):
                    final = f"{base} ({k})"
                    k += 1
                chave = "t" + str(int(time.time() * 1000)) + str(random.randint(100, 999))
                transc_jobs[chave] = {"nome": final, "arquivo": nome_arq, "caminho": os.path.join(entrada, final + ext.lower())}
            if not self._receber(transc_jobs[chave]["caminho"]):
                transc_jobs.pop(chave, None)
                return self._json({"ok": False, "erro": "O envio foi interrompido."}, 400)
            with trava:
                enfileirar("transcrever", chave)
            return self._json({"ok": True, "id": final})

        d = self._corpo()

        if p == "/api/headline/previa":
            pasta = pasta_video(d.get("video"))
            if not pasta:
                return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
            o = dict(cfg["opcoes"])
            o.update({k: v for k, v in (d.get("opcoes") or {}).items() if k in editor.PADRAO})
            os.makedirs(PASTA_PREVIAS, exist_ok=True)
            pid = ("cta_" if d.get("cta") else "hl_") + re.sub(r"\W", "_", os.path.basename(pasta))
            editor.previa_headline(pasta, d.get("texto", ""), o, os.path.join(PASTA_PREVIAS, pid + ".jpg"), cta=bool(d.get("cta")))
            return self._json({"ok": True, "url": f"/previa/{pid}.jpg?t={time.time():.0f}{random.randint(0, 999)}"})

        if p == "/api/cta/camada":
            pasta = pasta_video(d.get("video"))
            if not pasta:
                return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
            o = dict(cfg["opcoes"])
            o.update({k: v for k, v in (d.get("opcoes") or {}).items() if k in editor.PADRAO})
            os.makedirs(PASTA_PREVIAS, exist_ok=True)
            pid = "ctac_" + re.sub(r"\W", "_", os.path.basename(pasta))
            editor.camada_cta_previa(pasta, d.get("texto", ""), o, os.path.join(PASTA_PREVIAS, pid + ".png"))
            return self._json({"ok": True, "url": f"/previa/{pid}.png?t={time.time():.0f}{random.randint(0, 999)}"})

        if p == "/api/legenda":
            pasta = pasta_video(d.get("video"))
            if not pasta:
                return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
            for c in d.get("cenas") or []:
                a = os.path.basename(c.get("arquivo", ""))
                if not os.path.isfile(os.path.join(pasta, a)):
                    continue
                if c.get("restaurar"):
                    editor.restaurar_legenda(pasta, a)
                elif isinstance(c.get("texto"), str):
                    editor.salvar_correcao(pasta, a, c["texto"])
            return self._json({"ok": True})

        if p in ("/api/silencio/analisar", "/api/silencio/cortar", "/api/silencio/restaurar"):
            pasta = pasta_video(d.get("video"))
            a = os.path.basename(d.get("arquivo", ""))
            if not pasta or not (os.path.isfile(os.path.join(pasta, a)) or os.path.isfile(os.path.join(pasta, "raw", a))):
                return self._json({"ok": False, "erro": "Cena não encontrada."}, 404)
            if na_fila("editar", os.path.basename(pasta)) or na_fila("silencio", os.path.basename(pasta)):
                if p != "/api/silencio/analisar":
                    return self._json({"ok": False, "erro": "Esse vídeo está na fila de edição. Espere terminar."}, 409)
            if p == "/api/silencio/analisar":
                o = dict(cfg["silencio"])
                o.update({k: v for k, v in (d.get("opcoes") or {}).items() if k in editor.PADRAO_SILENCIO})
                r = editor.analisar_silencios(editor.fonte_do_corte(pasta, a), o)
                tem_raw = os.path.isfile(os.path.join(pasta, "raw", a))
                r.update(ok=True, aplicados=(editor.cortes_salvos(pasta, a) or {}).get("cortes") if tem_raw else None,
                         url=f"/midia/cena/{quote(os.path.basename(pasta))}/" + ("raw/" if tem_raw else "") + quote(a))
                return self._json(r)
            if p == "/api/silencio/cortar":
                r = editor.cortar_silencios(pasta, a, d.get("cortes") or [])
                r["ok"] = True
                return self._json(r)
            editor.restaurar_cena(pasta, a)
            return self._json({"ok": True})

        with trava:
            if p == "/api/opcoes":
                for k, v in (d.get("opcoes") or {}).items():
                    if k in editor.PADRAO:
                        cfg["opcoes"][k] = v
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True})
            if p == "/api/postado":
                marcar = bool(d.get("postado"))
                alterados = []
                for n in d.get("videos") or []:
                    n = os.path.basename(str(n))
                    if not pasta_video(n):
                        continue
                    e = estado.setdefault(n, {})
                    if marcar:
                        e["postado_em"] = time.strftime("%Y-%m-%d %H:%M")
                    else:
                        e.pop("postado_em", None)
                        e.pop("postagens", None)
                    alterados.append(n)
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "videos": alterados, "postado": marcar})
            if p == "/api/postagem":
                # onde o vídeo já foi postado: {"instagram": "2026-11-22 10:30", "facebook": ...}
                redes = [r for r in (d.get("redes") or [d.get("rede")]) if r in REDES]
                if not redes:
                    return self._json({"ok": False, "erro": "Rede desconhecida."}, 400)
                marcar, agora, alterados = bool(d.get("postado")), time.strftime("%Y-%m-%d %H:%M"), []
                for n in d.get("videos") or []:
                    n = os.path.basename(str(n))
                    if not pasta_video(n):
                        continue
                    e = estado.setdefault(n, {})
                    pg = e.setdefault("postagens", {})
                    for r in redes:
                        if marcar:
                            pg.setdefault(r, agora)
                        else:
                            pg.pop(r, None)
                    if pg:
                        e.setdefault("postado_em", max(pg.values()))
                    else:
                        e.pop("postagens", None)
                        e.pop("postado_em", None)
                    alterados.append(n)
                apagar = [n for n in alterados if agendar_limpeza(n)]
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "videos": alterados, "apagar": apagar, "espera": ESPERA_APAGAR})
            if p == "/api/limpeza":
                for k, padrao in LIMPEZA_PADRAO.items():
                    if k in d:
                        v = d[k]
                        if k == "ligada":
                            cfg["limpeza"][k] = bool(v)
                        elif (k == "quando" and v in ("todas", "qualquer")) or (k == "o_que" and v in ("tudo", "trabalho")):
                            cfg["limpeza"][k] = v
                if not cfg["limpeza"]["ligada"]:
                    for e in estado.values():
                        e.pop("apagar_em", None)
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True, "limpeza": cfg["limpeza"]})
            if p == "/api/limpeza/cancelar":
                for n in d.get("videos") or []:
                    estado.get(os.path.basename(str(n)), {}).pop("apagar_em", None)
                return self._json({"ok": True})
            if p == "/api/limpeza/agora":
                if not cfg["limpeza"]["ligada"]:
                    return self._json({"ok": False, "erro": "Ligue a opção de apagar depois de postar primeiro."}, 400)
                nomes = [n for n, e in estado.items() if pasta_video(n) and postado_completo(e) and not na_fila("editar", n)]
                for n in nomes:
                    estado[n]["apagar_em"] = time.time()
                return self._json({"ok": True, "videos": nomes})
            if p == "/api/silencio/opcoes":
                for k, v in (d.get("opcoes") or {}).items():
                    if k in editor.PADRAO_SILENCIO:
                        cfg["silencio"][k] = v
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True})
            if p == "/api/raiz":
                txt = (d.get("raiz") or "").strip().strip('"')
                if not txt:
                    return self._json({"ok": False, "erro": "Escolha uma pasta."}, 400)
                nova, velha = os.path.normpath(os.path.abspath(os.path.expanduser(txt))), os.path.normpath(raiz())
                if movendo:
                    return self._json({"ok": False, "erro": "Já estou levando os vídeos para a pasta nova. Espere terminar."}, 409)
                if os.path.normcase(nova) == os.path.normcase(velha):
                    return self._json({"ok": True, "existe": raiz_ok()})
                nc, vc = os.path.normcase(nova) + os.sep, os.path.normcase(velha) + os.sep
                if nc.startswith(vc) or vc.startswith(nc):
                    return self._json({"ok": False, "erro": "Escolha uma pasta que não fique dentro da pasta atual (nem o contrário)."}, 400)
                try:
                    os.makedirs(nova, exist_ok=True)
                    teste = os.path.join(nova, ".gw_teste")
                    with open(teste, "w") as fh:
                        fh.write("ok")
                    os.remove(teste)
                except OSError:
                    return self._json({"ok": False, "erro": "Não consigo gravar nessa pasta. Se for um SSD externo, confira se ele está conectado e não está protegido contra gravação."}, 400)
                if d.get("mover") and os.path.isdir(velha) and _itens_da_raiz(velha):
                    if fila["atual"] or fila["itens"]:
                        return self._json({"ok": False, "erro": "Espere a fila de edição terminar para levar os vídeos."}, 409)
                    precisa = _tamanho(velha)
                    livre = shutil.disk_usage(nova).free
                    if os.stat(velha).st_dev != os.stat(nova).st_dev and precisa > livre:
                        return self._json({"ok": False, "erro": f"Não cabe: os vídeos ocupam {precisa / 1e9:.1f} GB e a pasta nova tem {livre / 1e9:.1f} GB livres."}, 400)
                    movendo.update(de=velha, para=nova)
                    enfileirar("mover_raiz", nova)
                    return self._json({"ok": True, "movendo": True})
                cfg["raiz"] = nova
                gravar_json(ARQ_CFG, cfg)
                preparar_entrada()
                return self._json({"ok": True, "existe": True})
            if p == "/api/editar":
                if movendo:
                    return self._json({"ok": False, "erro": "Estou levando os vídeos para a pasta nova. Espere terminar."}, 409)
                return self._json({"ok": True, "adicionados": pedir_edicao(d.get("videos", []))})
            if p == "/api/estilo/salvar":
                nome = " ".join(str(d.get("nome", "")).split())[:40]
                if not nome:
                    return self._json({"ok": False, "erro": "Dê um nome para o estilo."}, 400)
                ops = {k: v for k, v in (d.get("opcoes") or cfg["opcoes"]).items() if k in editor.PADRAO}
                es = estilo_por_id(d.get("id")) or next((x for x in cfg["estilos"] if x["nome"].lower() == nome.lower()), None)
                if es:
                    es.update(nome=nome, opcoes=ops)
                else:
                    es = {"id": "es" + "".join(random.choice("0123456789abcdef") for _ in range(8)), "nome": nome, "opcoes": ops}
                    cfg["estilos"].append(es)
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True, "estilo": es})
            if p == "/api/estilo/apagar":
                cfg["estilos"] = [x for x in cfg["estilos"] if x["id"] != d.get("id")]
                for a in avatares():
                    if a.get("estilo") == d.get("id"):
                        a.pop("estilo", None)
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True})
            if p == "/api/automacao":
                for k in AUTOMACAO_PADRAO:
                    if k in d:
                        cfg["automacao"][k] = bool(d[k])
                gravar_json(ARQ_CFG, cfg)
                if cfg["automacao"]["vigiar"]:
                    preparar_entrada()
                return self._json({"ok": True, "automacao": cfg["automacao"]})
            if p == "/api/silencio/auto":
                nomes = [n for n in d.get("videos", []) if pasta_video(n) and not na_fila("silencio", n) and not na_fila("editar", n)]
                for n in nomes:
                    enfileirar("silencio", n)
                return self._json({"ok": True, "videos": nomes})
            if p == "/api/cancelar":
                fila["cancelar"] = True
                return self._json({"ok": True})
            if p == "/api/renomear":
                n = os.path.basename(d.get("video", ""))
                pasta = pasta_video(n)
                novo = nome_valido(d.get("novo", ""))
                if not pasta:
                    return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
                if not novo:
                    return self._json({"ok": False, "erro": 'Esse nome não serve. Evite / \\ : * ? " < > | e não comece com _ ou ponto.'}, 400)
                if novo == n:
                    return self._json({"ok": True, "nome": n})
                if na_fila("editar", n) or na_fila("silencio", n):
                    return self._json({"ok": False, "erro": "Esse vídeo está na fila. Espere terminar."}, 409)
                destino = os.path.join(raiz(), novo)
                if os.path.exists(destino) and novo.lower() != n.lower():
                    return self._json({"ok": False, "erro": f"Já existe um vídeo chamado {novo}."}, 409)
                editor.trocar(pasta, destino)
                av = avatar_do_video(n)
                if os.path.isfile(final_de(n, av)):
                    editor.trocar(final_de(n, av), final_de(novo, av))
                if n in estado:
                    estado[novo] = estado.pop(n)
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "nome": novo})
            if p == "/api/remover":
                n = os.path.basename(d.get("video", ""))
                pasta = pasta_video(n)
                if not pasta:
                    return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
                if na_fila("editar", n) or na_fila("silencio", n):
                    return self._json({"ok": False, "erro": "Esse vídeo está na fila. Espere terminar."}, 409)
                lix = os.path.join(raiz(), LIXEIRA)
                os.makedirs(lix, exist_ok=True)
                destino = os.path.join(lix, n)
                if os.path.exists(destino):
                    destino = os.path.join(lix, f"{n} ({time.strftime('%Y-%m-%d %H-%M-%S')})")
                editor.trocar(pasta, destino, mover=True)
                if os.path.isfile(final_de(n)):
                    os.makedirs(os.path.join(destino, "final"), exist_ok=True)
                    editor.trocar(final_de(n), os.path.join(destino, "final", n + ".mp4"), mover=True)
                estado.pop(n, None)
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True})
            if p == "/api/headline":
                n = os.path.basename(d.get("video", ""))
                if pasta_video(n):
                    estado.setdefault(n, {})["headline"] = str(d.get("texto", ""))[:400]
                    gravar_json(ARQ_EST, estado)
                return self._json({"ok": True})
            if p == "/api/elementos":
                n = os.path.basename(d.get("video", ""))
                if not pasta_video(n):
                    return self._json({"ok": False, "erro": "Vídeo não encontrado."}, 404)
                e = estado.setdefault(n, {})
                if "cta" in d:
                    e["cta"] = str(d.get("cta") or "")[:400]
                if "cta_ini" in d:
                    e["cta_ini"] = _tempo(d.get("cta_ini"))
                if "cta_fim" in d:
                    e["cta_fim"] = _tempo(d.get("cta_fim"))
                if "setas" in d:
                    e["setas"] = limpar_setas(d.get("setas"))
                aplicado = False
                if d.get("aplicar"):
                    if na_fila("editar", n) or na_fila("silencio", n):
                        gravar_json(ARQ_EST, estado)
                        return self._json({"ok": False, "erro": "Esse vídeo já está na fila. Salvei; quando terminar, clique em aplicar de novo."}, 409)
                    opcoes_da_fila[n] = opcoes_para_fila(n, so_elementos=os.path.isfile(final_de(n)))
                    e["status"], e["erro"] = "na fila", ""
                    enfileirar("editar", n)
                    aplicado = True
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "aplicado": aplicado, "setas": e.get("setas", [])})
            if p == "/api/avatar/novo":
                nome = " ".join(str(d.get("nome", "")).split())[:40]
                if not nome_valido(nome):
                    return self._json({"ok": False, "erro": 'Escreva um nome (sem / \\ : * ? " < > | e sem começar com _ ou ponto).'}, 400)
                if any(a["nome"].lower() == nome.lower() for a in avatares()):
                    return self._json({"ok": False, "erro": f"Já existe uma pasta chamada {nome}."}, 409)
                return self._json({"ok": True, "avatar": criar_avatar(nome)})
            if p == "/api/avatar/editar":
                a = avatar_por_id(d.get("id"))
                if not a:
                    return self._json({"ok": False, "erro": "Pasta não encontrada."}, 404)
                if "nome" in d:
                    nome = " ".join(str(d.get("nome", "")).split())[:40]
                    if not nome_valido(nome):
                        return self._json({"ok": False, "erro": "Esse nome não serve."}, 400)
                    if any(x["nome"].lower() == nome.lower() and x["id"] != a["id"] for x in avatares()):
                        return self._json({"ok": False, "erro": f"Já existe uma pasta chamada {nome}."}, 409)
                    if any(na_fila("editar", n) for n, e in estado.items() if e.get("avatar") == a["id"]):
                        return self._json({"ok": False, "erro": "Tem vídeo dessa pasta na fila. Espere terminar para renomear."}, 409)
                    nova = pasta_livre_avatar(nome, ignorar=a["id"]) if nome_valido(nome) != a["pasta"] else a["pasta"]
                    velha = os.path.join(pasta_prontos(), a["pasta"])
                    if nova != a["pasta"] and os.path.isdir(velha):
                        editor.trocar(velha, os.path.join(pasta_prontos(), nova))
                    a["nome"], a["pasta"] = nome, nova
                if re.fullmatch(r"#[0-9a-fA-F]{6}", str(d.get("cor", ""))):
                    a["cor"] = d["cor"]
                if "estilo" in d:
                    if estilo_por_id(d.get("estilo")):
                        a["estilo"] = d["estilo"]
                    else:
                        a.pop("estilo", None)
                gravar_json(ARQ_CFG, cfg)
                return self._json({"ok": True, "avatar": a})
            if p == "/api/avatar/apagar":
                a = avatar_por_id(d.get("id"))
                if not a:
                    return self._json({"ok": False, "erro": "Pasta não encontrada."}, 404)
                deles = [n for n, e in estado.items() if e.get("avatar") == a["id"]]
                if any(na_fila("editar", n) for n in deles):
                    return self._json({"ok": False, "erro": "Tem vídeo dessa pasta na fila. Espere terminar."}, 409)
                for n in deles:      # os vídeos ficam: só saem do avatar
                    try:
                        mover_final(n, a["id"], None)
                    except Exception:
                        traceback.print_exc()
                    estado[n].pop("avatar", None)
                velha = os.path.join(pasta_prontos(), a["pasta"])
                try:
                    if os.path.isdir(velha) and not os.listdir(velha):
                        os.rmdir(velha)
                except OSError:
                    pass
                if os.path.isfile(foto_avatar(a["id"])):
                    os.remove(foto_avatar(a["id"]))
                avatares().remove(a)
                gravar_json(ARQ_CFG, cfg)
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "videos": len(deles)})
            if p == "/api/avatar/foto/remover":
                a = avatar_por_id(d.get("id"))
                if a and os.path.isfile(foto_avatar(a["id"])):
                    os.remove(foto_avatar(a["id"]))
                return self._json({"ok": True})
            if p == "/api/video-avatar":
                para = d.get("avatar") or None
                if para and not avatar_por_id(para):
                    return self._json({"ok": False, "erro": "Pasta não encontrada."}, 404)
                movidos, presos = [], []
                for n in d.get("videos") or []:
                    n = os.path.basename(str(n))
                    if not pasta_video(n):
                        continue
                    if na_fila("editar", n) or na_fila("silencio", n):
                        presos.append(n)
                        continue
                    de = avatar_do_video(n)
                    if de != para:
                        try:
                            mover_final(n, de, para)
                        except Exception as ex:
                            return self._json({"ok": False, "erro": f"{n}: {ex}"}, 409)
                        e = estado.setdefault(n, {})
                        if para:
                            e["avatar"] = para
                        else:
                            e.pop("avatar", None)
                    movidos.append(n)
                gravar_json(ARQ_EST, estado)
                return self._json({"ok": True, "movidos": movidos, "presos": presos})
            if p == "/api/musica-video":
                n = os.path.basename(d.get("video", ""))
                if pasta_video(n):
                    estado.setdefault(n, {})["musica"] = str(d.get("musica", ""))
                    gravar_json(ARQ_EST, estado)
                return self._json({"ok": True})
            if p == "/api/transcrever/apagar":
                n = os.path.basename(d.get("id", ""))
                for ext in (".txt", ".srt", ".json"):
                    f = os.path.join(pasta_transc(), n + ext)
                    if os.path.isfile(f):
                        os.remove(f)
                for f in os.listdir(os.path.join(pasta_transc(), "_arquivos")) if os.path.isdir(os.path.join(pasta_transc(), "_arquivos")) else []:
                    if os.path.splitext(f)[0] == n:
                        try:
                            os.remove(os.path.join(pasta_transc(), "_arquivos", f))
                        except OSError:
                            pass
                return self._json({"ok": True})
            if p == "/api/abrir":
                alvo = d.get("alvo", "raiz")
                if alvo not in ("musicas", "marca") and not garantir_raiz():
                    return self._json({"ok": False, "erro": ERRO_RAIZ}, 409)
                if alvo == "prontos":
                    n = os.path.basename(d.get("video", "") or "")
                    if n and os.path.isfile(localizar_final(n)):
                        abrir_no_sistema(localizar_final(n), selecionar=True)
                    else:
                        alvo_p = pasta_prontos_de(d.get("avatar"))
                        os.makedirs(alvo_p, exist_ok=True)
                        abrir_no_sistema(alvo_p)
                elif alvo == "musicas":
                    os.makedirs(editor.PASTA_MUSICAS, exist_ok=True)
                    abrir_no_sistema(editor.PASTA_MUSICAS)
                elif alvo == "entrada":
                    preparar_entrada()
                    abrir_no_sistema(pasta_entrada())
                elif alvo == "transcricoes":
                    os.makedirs(pasta_transc(), exist_ok=True)
                    abrir_no_sistema(pasta_transc())
                elif alvo == "marca":
                    abrir_no_sistema(PASTA_MARCA)
                elif alvo == "video" and pasta_video(d.get("video")):
                    abrir_no_sistema(pasta_video(d.get("video")))
                elif os.path.isdir(raiz()):
                    abrir_no_sistema(raiz())
                return self._json({"ok": True})
        self.send_error(404)


ao_escolher_pasta = None   # o aplicativo de janela troca por uma função que abre "Escolher pasta" do sistema
ao_sair = None         # o aplicativo de janela troca por uma função que fecha a janela (para atualizar)
ao_pedir_foco = None   # o aplicativo de janela troca por uma função que traz a janela para a frente


class Servidor(ThreadingHTTPServer):
    # no Windows, SO_REUSEADDR deixaria duas cópias do programa ouvirem a mesma porta
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def iniciar_servidor():
    """Abre o servidor do painel e a fila de trabalho. Dá OSError se a porta já estiver em uso."""
    srv = Servidor(("127.0.0.1", PORTA), Painel)
    threading.Thread(target=trabalhador, daemon=True).start()
    threading.Thread(target=vigia, daemon=True).start()
    threading.Thread(target=migrar_versao_antiga, daemon=True).start()
    threading.Thread(target=atualizador.vigiar, args=(VERSAO,), daemon=True).start()
    return srv


def ocupado():
    """True se tem vídeo sendo editado/transcrito ou esperando na fila."""
    return bool(fila["atual"] or fila["itens"])


def main():
    url = f"http://127.0.0.1:{PORTA}"
    try:
        srv = iniciar_servidor()
    except OSError:
        print(f"O {NOME_APP} já está aberto. Abrindo o navegador…")
        if not os.environ.get("GW_SEM_NAVEGADOR"):
            webbrowser.open(url)
        time.sleep(2)
        sys.exit(0)
    print()
    print(f"  {NOME_APP} {VERSAO} aberto no navegador: {url}")
    print("  Deixe esta janela aberta enquanto usa. Para fechar o programa, feche esta janela.")
    print()
    if not os.environ.get("GW_SEM_NAVEGADOR"):
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
