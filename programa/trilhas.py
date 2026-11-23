"""GW Apps Edition Videos — trilhas de fundo geradas por código.

Todas as trilhas são sintetizadas na hora (nenhum sample, nenhuma gravação),
então não têm direitos autorais de terceiros e podem ser usadas em anúncios.
Cada trilha é uma função f(D) -> array estéreo float (n, 2) com D segundos.
"""
import wave
import numpy as np

SR = 48000


# ----------------------------------------------------------------- utilidades
def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(n):
    return np.arange(n) / SR


def _filtro(x, grave=None, agudo=None):
    """Passa-baixa / passa-alta suaves no domínio da frequência (funciona em mono ou estéreo)."""
    eixo = 0
    X = np.fft.rfft(x, axis=eixo)
    f = np.fft.rfftfreq(x.shape[eixo], 1 / SR)
    g = np.ones_like(f)
    if agudo:
        g *= 1 / np.sqrt(1 + (f / agudo) ** 4)
    if grave:
        g *= 1 / np.sqrt(1 + (grave / np.maximum(f, 1e-3)) ** 4)
    if x.ndim == 2:
        g = g[:, None]
    return np.fft.irfft(X * g, x.shape[eixo], axis=eixo)


def _convolver(x, ir):
    n = len(x) + len(ir) - 1
    N = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(ir, N), N)[: len(x)]


def _reverb(L, tempo=1.8, mistura=0.25, escuro=5000, semente=7):
    """Reverb de sala: resposta ao impulso sintética (ruído com decaimento exponencial)."""
    n = int(SR * tempo)
    rng = np.random.default_rng(semente)
    t = _t(n)
    ir = rng.standard_normal((n, 2)) * np.exp(-t * 6.9 / tempo)[:, None]
    ir[: int(0.015 * SR)] = 0
    ir = _filtro(ir, grave=180, agudo=escuro)
    ir /= np.sqrt((ir ** 2).sum(0)) + 1e-9
    mol = np.stack([_convolver(L[:, 0], ir[:, 0]), _convolver(L[:, 1], ir[:, 1])], 1)
    return L * (1 - mistura) + mol * mistura * 2.2


class _Mesa:
    """Linha do tempo estéreo onde os sons são colocados."""

    def __init__(self, D):
        self.n = int(SR * D) + 1
        self.L = np.zeros((self.n, 2))

    def por(self, y, t0, ganho=1.0, pan=0.5):
        s = int(t0 * SR)
        if s >= self.n or s < 0:
            return
        e = min(self.n, s + len(y))
        seg = y[: e - s] * ganho
        self.L[s:e, 0] += seg * np.sqrt(1 - pan)
        self.L[s:e, 1] += seg * np.sqrt(pan)


_cache = {}


def _memo(chave, fn):
    if chave not in _cache:
        _cache[chave] = fn()
    return _cache[chave]


# --------------------------------------------------------------- instrumentos
def _pluck(f, dur=0.7, brilho=0.55):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        y = np.zeros(n)
        for k in range(1, 10):
            if f * k > 14000:
                break
            y += (brilho ** (k - 1)) / k ** 0.4 * np.sin(2 * np.pi * f * k * t) * np.exp(-t * (4 + 2.6 * k))
        return y * np.minimum(1, t / 0.003)
    return _memo(("pluck", round(f, 2), dur, brilho), faz)


def _epiano(f, dur=1.8):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        mod = 0.9 * np.exp(-t * 5) * np.sin(2 * np.pi * f * t)
        y = np.sin(2 * np.pi * f * t + mod)
        y += 0.22 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 4)
        y *= np.exp(-t * 1.4) * np.minimum(1, t / 0.005) * (1 + 0.08 * np.sin(2 * np.pi * 4.8 * t))
        return y
    return _memo(("ep", round(f, 2), dur), faz)


def _pad(f, dur, ataque=0.9, harm=6):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        y = np.zeros(n)
        for d in (-0.09, 0.0, 0.1):
            ff = f * 2 ** (d / 12)
            for k in range(1, harm + 1):
                y += np.sin(2 * np.pi * ff * k * t + 1.7 * k * d) / k ** 1.25
        a = min(ataque, dur / 2.5)
        env = np.minimum(1, t / a) * np.clip((dur - t) / a, 0, 1)
        return y * env * 0.22
    return _memo(("pad", round(f, 2), round(dur, 3), ataque, harm), faz)


def _marimba(f, dur=0.9):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        y = np.sin(2 * np.pi * f * t) * np.exp(-t * 6.5)
        y += 0.32 * np.sin(2 * np.pi * 3.93 * f * t) * np.exp(-t * 20)
        y += 0.08 * np.sin(2 * np.pi * 9.8 * f * t) * np.exp(-t * 45)
        return y * np.minimum(1, t / 0.002)
    return _memo(("mar", round(f, 2), dur), faz)


def _sino(f, dur=2.5):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        y = np.sin(2 * np.pi * f * t) * np.exp(-t * 1.8)
        y += 0.4 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t * 3.2)
        y += 0.15 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t * 6)
        return y * np.minimum(1, t / 0.003)
    return _memo(("sino", round(f, 2), dur), faz)


def _baixo(f, dur):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        y = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(2 * np.pi * 2 * f * t) + 0.08 * np.sin(2 * np.pi * 3 * f * t)
        env = np.minimum(1, t / 0.008) * np.clip((dur - t) / 0.04, 0, 1) * (0.7 + 0.3 * np.exp(-t * 6))
        return y * env
    return _memo(("bx", round(f, 2), round(dur, 3)), faz)


def _bumbo(dur=0.5, corpo=48):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        freq = corpo + 95 * np.exp(-t * 32)
        fase = 2 * np.pi * np.cumsum(freq) / SR
        y = np.sin(fase) * np.exp(-t * 7)
        y += np.random.default_rng(1).standard_normal(n) * np.exp(-t * 350) * 0.12
        return y
    return _memo(("bumbo", dur, corpo), faz)


def _caixa(dur=0.32):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        r = np.random.default_rng(2).standard_normal(n)
        r = _filtro(r, grave=900, agudo=7000)
        y = r / (np.abs(r).max() + 1e-9) * np.exp(-t * 17) + 0.45 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 24)
        return y
    return _memo(("caixa", dur), faz)


def _palma(dur=0.35):
    def faz():
        n = int(SR * dur)
        t = _t(n)
        r = _filtro(np.random.default_rng(3).standard_normal(n), grave=1100, agudo=6000)
        r /= np.abs(r).max() + 1e-9
        env = np.zeros(n)
        for k, d in enumerate((0, 0.011, 0.023)):
            s = int(d * SR)
            env[s:] = np.maximum(env[s:], np.exp(-(t[: n - s]) * (90 if k < 2 else 16)))
        return r * env
    return _memo(("palma", dur), faz)


def _chimbal(aberto=False, semente=4):
    def faz():
        dur = 0.3 if aberto else 0.07
        n = int(SR * dur)
        t = _t(n)
        r = _filtro(np.random.default_rng(semente).standard_normal(n), grave=7000)
        r /= np.abs(r).max() + 1e-9
        return r * np.exp(-t * (14 if aberto else 70))
    return _memo(("hh", aberto, semente), faz)


def _bombeia(L, batida, D, fundo=0.42, rec=0.11):
    """Efeito 'sidechain': o som abaixa a cada batida do bumbo e volta."""
    t = _t(len(L))
    fase = np.mod(t, batida)
    g = 1 - fundo * np.exp(-fase / rec)
    return L * g[:, None]


# ----------------------------------------------------------------- acordes
# notas MIDI (C4 = 60)
def _acorde(raiz, tipo):
    forma = {"M": (0, 4, 7), "m": (0, 3, 7), "M7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
             "7": (0, 4, 7, 10), "m9": (0, 3, 7, 10, 14), "M9": (0, 4, 7, 11, 14), "sus2": (0, 2, 7), "6": (0, 4, 7, 9)}[tipo]
    return [raiz + i for i in forma]


# ----------------------------------------------------------------- trilhas
def pulso(D):
    """Pulso tech: animada, bumbo reto, arpejo — boa para app, tecnologia, antes/depois."""
    bpm = 112
    b = 60 / bpm
    compasso = 4 * b
    seq = [_acorde(57, "m"), _acorde(53, "M"), _acorde(60, "M"), _acorde(55, "M")]  # Am F C G
    m = _Mesa(D)
    musical = _Mesa(D)
    k = 0
    t0 = 0.0
    while t0 < D:
        ac = seq[k % 4]
        notas = [ac[0] + 12, ac[1] + 12, ac[2] + 12, ac[1] + 24, ac[2] + 12, ac[0] + 24, ac[1] + 12, ac[2] + 12]
        for j in range(16):
            nt = notas[j % 8]
            musical.por(_pluck(hz(nt), 0.5, 0.5), t0 + j * b / 4, 0.16, 0.3 if j % 2 else 0.7)
        musical.por(_pad(hz(ac[0]), compasso + 0.6, 0.4), t0, 0.32)
        for j in range(8):
            musical.por(_baixo(hz(ac[0] - 24), b / 2 * 0.85), t0 + j * b / 2, 0.42)
        if k >= 1:
            for j in range(4):
                m.por(_bumbo(), t0 + j * b, 0.95)
                m.por(_chimbal(False, 4 + j), t0 + j * b + b / 2, 0.22, 0.62)
        if k >= 2:
            for j in (1, 3):
                m.por(_palma(), t0 + j * b, 0.32, 0.45)
        k += 1
        t0 += compasso
    L = _bombeia(musical.L, b, D) + m.L
    return _reverb(L, 1.4, 0.16)


def lofi(D):
    """Lo-fi chill: batida lenta, piano elétrico quente, chiado de vinil — conversa, rotina, bastidores."""
    bpm = 80
    b = 60 / bpm
    compasso = 4 * b
    seq = [_acorde(50, "m9"), _acorde(55, "7"), _acorde(48, "M9"), _acorde(57, "m7")]  # Dm9 G7 Cmaj9 Am7
    rng = np.random.default_rng(11)
    m = _Mesa(D)
    k = 0
    t0 = 0.0
    swing = 0.62
    pent = [62, 64, 67, 69, 72, 74, 76]
    while t0 < D:
        ac = seq[k % 4]
        for i, nt in enumerate(ac):
            m.por(_epiano(hz(nt), compasso + 0.4), t0 + i * 0.018, 0.17, 0.35 + 0.08 * i)
        m.por(_baixo(hz(ac[0] - 12), 1.4 * b), t0, 0.38)
        m.por(_baixo(hz(ac[0] - 12), 0.9 * b), t0 + 2.5 * b, 0.3)
        if k >= 1:
            for pos, tipo in ((0, "b"), (1, "c"), (1.75 * 1, None), (2.5, "b"), (3, "c")):
                if tipo == "b":
                    m.por(_bumbo(0.45, 52), t0 + pos * b, 0.85)
                elif tipo == "c":
                    m.por(_caixa(), t0 + pos * b, 0.42, 0.52)
            for j in range(8):
                desl = (swing - 0.5) * b if j % 2 else 0
                m.por(_chimbal(False, 20 + j), t0 + j * b / 2 + desl, 0.13 if j % 2 else 0.18, 0.62)
        if k >= 2 and k % 2 == 0:
            for j in range(3):
                m.por(_epiano(hz(rng.choice(pent) + 12), 0.9), t0 + (1 + j * 0.75 + rng.uniform(0, 0.2)) * b, 0.1, 0.65)
        k += 1
        t0 += compasso
    n = m.n
    vinil = np.random.default_rng(5).standard_normal(n) * 0.006
    estalos = (np.random.default_rng(6).random(n) > 0.99985) * np.random.default_rng(7).uniform(0.05, 0.25, n)
    L = m.L + (vinil + estalos)[:, None]
    L = _filtro(L, grave=40, agudo=4200)
    return _reverb(L, 1.2, 0.14)


def ambiente(D):
    """Ambiente suave: sem batida, só camadas — depoimento, emoção, bem-estar."""
    seq = [_acorde(48, "M7"), _acorde(57, "m7"), _acorde(53, "M7"), _acorde(55, "6")]  # Cmaj7 Am7 Fmaj7 G6
    m = _Mesa(D)
    rng = np.random.default_rng(21)
    dur_ac = 5.2
    k = 0
    t0 = 0.0
    while t0 < D:
        ac = seq[k % 4]
        for i, nt in enumerate(ac):
            m.por(_pad(hz(nt), dur_ac + 2.6, 1.6, 4), t0, 0.26, 0.3 + 0.13 * i)
        m.por(_pad(hz(ac[0] - 12), dur_ac + 2.6, 1.6, 3), t0, 0.3)
        for _ in range(2):
            nt = int(rng.choice(ac)) + 24
            m.por(_sino(hz(nt)), t0 + rng.uniform(0.5, dur_ac - 0.5), 0.07, rng.uniform(0.2, 0.8))
        k += 1
        t0 += dur_ac
    t = _t(m.n)
    L = m.L * (0.88 + 0.12 * np.sin(2 * np.pi * t / 9))[:, None]
    return _reverb(L, 3.0, 0.35, 4500)


def motivacional(D):
    """Motivacional: arpejo de piano que cresce — transformação, resultado, conquista."""
    bpm = 96
    b = 60 / bpm
    compasso = 4 * b
    seq = [_acorde(48, "M"), _acorde(55, "M"), _acorde(57, "m"), _acorde(53, "M")]  # C G Am F
    m = _Mesa(D)
    k = 0
    t0 = 0.0
    while t0 < D:
        ac = seq[k % 4]
        arp = [ac[0], ac[1], ac[2], ac[0] + 12, ac[2], ac[1], ac[0] + 12, ac[1] + 12]
        for j in range(8):
            m.por(_epiano(hz(arp[j] + 12), 1.2), t0 + j * b / 2, 0.15, 0.35 if j % 2 else 0.65)
        m.por(_pad(hz(ac[0]), compasso + 0.8, 0.7), t0, 0.22 if k >= 2 else 0.12)
        m.por(_pad(hz(ac[2]), compasso + 0.8, 0.7), t0, 0.16 if k >= 2 else 0.08)
        m.por(_baixo(hz(ac[0] - 24), 2 * b * 0.9), t0, 0.45)
        m.por(_baixo(hz(ac[0] - 24), 2 * b * 0.9), t0 + 2 * b, 0.4)
        if k >= 2:
            m.por(_bumbo(0.5, 46), t0, 0.8)
            m.por(_bumbo(0.5, 46), t0 + 2 * b, 0.7)
            m.por(_palma(), t0 + b, 0.22, 0.5)
            m.por(_palma(), t0 + 3 * b, 0.22, 0.5)
        if k >= 4:
            for j in range(8):
                m.por(_chimbal(False, 40 + j), t0 + j * b / 2, 0.12, 0.6)
        k += 1
        t0 += compasso
    return _reverb(m.L, 2.0, 0.24)


def leve(D):
    """Leve e alegre: marimba e palmas — dicas rápidas, lifestyle, comida."""
    bpm = 122
    b = 60 / bpm
    compasso = 4 * b
    seq = [_acorde(62, "M"), _acorde(67, "M"), _acorde(69, "M"), _acorde(59, "m")]  # D G A Bm
    m = _Mesa(D)
    k = 0
    t0 = 0.0
    padrao = [0, 2, 1, 2, 0, 2, 1, 2]
    while t0 < D:
        ac = seq[k % 4]
        for j in range(8):
            nt = ac[padrao[j]] + (12 if j in (3, 7) else 0)
            m.por(_marimba(hz(nt)), t0 + j * b / 2, 0.24, 0.3 if j % 2 else 0.7)
        m.por(_baixo(hz(ac[0] - 24), b * 0.8), t0, 0.42)
        m.por(_baixo(hz(ac[0] - 24), b * 0.8), t0 + 2 * b, 0.36)
        m.por(_baixo(hz(ac[0] - 17), b * 0.6), t0 + 3.5 * b, 0.28)
        if k >= 1:
            m.por(_bumbo(0.4, 50), t0, 0.7)
            m.por(_bumbo(0.4, 50), t0 + 2 * b, 0.6)
            for j in (1, 3):
                m.por(_palma(), t0 + j * b, 0.34, 0.5)
            for j in range(16):
                m.por(_chimbal(False, 60 + j % 5), t0 + j * b / 4, 0.07 if j % 2 else 0.11, 0.66)
        k += 1
        t0 += compasso
    return _reverb(m.L, 1.3, 0.15)


def suspense(D):
    """Suspense / curiosidade: grave contínuo, tique-taque — gancho, 'você sabia?', revelação."""
    bpm = 100
    b = 60 / bpm
    compasso = 4 * b
    m = _Mesa(D)
    rng = np.random.default_rng(31)
    escala = [69, 71, 72, 76, 77, 81]
    k = 0
    t0 = 0.0
    while t0 < D:
        base = 45 if (k // 2) % 2 == 0 else 41  # A / F
        m.por(_pad(hz(base - 12), compasso + 1.2, 1.2, 5), t0, 0.5)
        m.por(_pad(hz(base + 7 - 12), compasso + 1.2, 1.2, 4), t0, 0.22)
        m.por(_bumbo(0.9, 38), t0, 0.75)
        for j in range(8):
            m.por(_chimbal(False, 80 + j % 3), t0 + j * b / 2, 0.16 if j % 2 == 0 else 0.1, 0.3 if j % 2 else 0.7)
        if k >= 1:
            for _ in range(2):
                m.por(_sino(hz(int(rng.choice(escala))), 2.2), t0 + rng.uniform(0.2, compasso - 0.3), 0.06, rng.uniform(0.2, 0.8))
        k += 1
        t0 += compasso
    t = _t(m.n)
    L = m.L * (0.8 + 0.2 * np.sin(2 * np.pi * t / (compasso * 2)))[:, None]
    return _reverb(L, 2.6, 0.3, 3800)


TRILHAS = {
    "@pulso": ("Pulso tech (animada)", pulso),
    "@lofi": ("Lo-fi chill", lofi),
    "@leve": ("Leve e alegre", leve),
    "@motiva": ("Motivacional", motivacional),
    "@suspense": ("Suspense / curiosidade", suspense),
    "@ambiente": ("Ambiente suave (sem batida)", ambiente),
}
PADRAO = "@pulso"


def gerar(tid, D, caminho):
    """Gera a trilha `tid` com D segundos e grava um WAV estéreo 48 kHz."""
    _cache.clear()
    _, fn = TRILHAS.get(tid, TRILHAS[PADRAO])
    D = max(1.0, float(D))
    L = fn(D + 0.05)[: int(SR * D)]
    n = len(L)
    t = _t(n)
    L *= (np.minimum(1, t / 0.4) * np.clip((D - t) / 0.6, 0, 1))[:, None]
    rms = np.sqrt((L ** 2).mean()) + 1e-9
    L *= 0.2 / rms
    L = 0.95 * np.tanh(L * 1.1)
    with wave.open(caminho, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(L, -1, 1) * 32767).astype(np.int16).tobytes())
    _cache.clear()
    return caminho
