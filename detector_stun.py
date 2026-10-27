# -*- coding: utf-8 -*-
"""
Detector do anel de stun (PROTÓTIPO).

Um monstro atordoado pelo Whirlwind aparece com um anel branco ao redor.
Este módulo procura esses anéis na área do jogo com um filtro em forma de anel
(correlação com uma coroa circular sobre o "quanto é branco").

    from detector_stun import achar_aneis
    aneis = achar_aneis(frame)       # [(x, y, raio, pontuacao), ...]

Medido nas gravações do usuário: com anel a pontuação fica em 0,30 a 0,50 e sem
anel em ~0,12 (limite padrão 0,25). Em duas gravações o anel apareceu no shiny
stunado, não apareceu nos lures sem shiny e sumiu depois da morte dele, sem falsos
positivos. Velocidade: ~260 ms por quadro (configurar() deixa mais rápido).

O ANEL PISCA (~5 vezes por segundo) durante todo o stun (~6 s). Com 10 quadros por
segundo ele aparece em quadros alternados; por isso um quadro SEM anel não quer
dizer nada. Use a JanelaAneis, que junta os últimos ~0,5 s:

    j = JanelaAneis()
    j.adicionar(t, achar_aneis(frame))         # a cada quadro
    j.max_aneis(t)                             # maior nº de anéis nos últimos 0,5 s
    j.fracao_stunada(t, n_monstros)            # max_aneis / monstros na Battle list

Na gravação do usuário, logo após o lançamento: 10 anéis para 10 monstros.

LIMITES CONHECIDOS (por isso é protótipo):
  - Monstros empilhados escondem parte do anel; a contagem pode ficar abaixo do
    número de monstros. Pode haver contagem a mais (22 para 10) quando um efeito
    de ataque passa na frente: use min(contagem, monstros).
  - Calibrado em quadros JPEG de uma única hunt (Gogoat) e com o stun do Whirlwind.
  - Precisa de ~10 quadros/s para pegar a piscada.
"""
import base64
import numpy as np
import cv2

AREA_JOGO = (200, 100, 1720, 880)       # x0, y0, x1, y1 (sem as bordas da janela)
ESCALA = 0.5                            # trabalha em metade da resolução (bem mais rápido)
RAIOS = range(30, 46, 4)                # em pixels da tela
LIMITE = 0.25
DIST_MIN = 18                           # na escala reduzida (~36 px na tela)


def _kernel(r, esp=2):
    s = 2 * (r + esp) + 1
    k = np.zeros((s, s), np.float32)
    c = r + esp
    yy, xx = np.mgrid[:s, :s]
    d = np.hypot(yy - c, xx - c)
    k[(d >= r - esp) & (d <= r + esp)] = 1
    return k / k.sum()


_KS = {r: _kernel(max(2, int(round(r * ESCALA))), 1) for r in RAIOS}


def configurar(escala=0.5, raios=range(30, 46, 4)):
    """Troca velocidade por precisão. Medido (nesta máquina de teste), na gravação do usuário:
       escala 0.5 + 4 raios = 258 ms, 10/10 anéis, 0 falsos;
       escala 0.25 + 4 raios = 108 ms, 9/10 anéis, 0 falsos."""
    global ESCALA, RAIOS, _KS, DIST_MIN
    ESCALA, RAIOS = escala, raios
    _KS = {r: _kernel(max(2, int(round(r * escala))), 1) for r in raios}
    DIST_MIN = max(6, int(36 * escala))


def mapa_branco(frame, area=AREA_JOGO):
    x0, y0, x1, y1 = area
    g = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(g, cv2.COLOR_BGR2HSV)
    mn = g.min(axis=2).astype(np.float32)
    w = np.clip((mn - 150) / 105, 0, 1) * (hsv[..., 1] < 80)
    return cv2.resize(w.astype(np.float32), None, fx=ESCALA, fy=ESCALA, interpolation=cv2.INTER_AREA)


def achar_aneis(frame, limite=LIMITE, area=AREA_JOGO):
    """Lista de (x, y, raio, pontuacao) em coordenadas da tela, do mais forte ao mais fraco."""
    W = mapa_branco(frame, area)
    melhor = np.zeros(W.shape, np.float32)
    raio = np.zeros(W.shape, np.int16)
    for r, k in _KS.items():
        R = cv2.filter2D(W, -1, k)
        m = R > melhor
        melhor[m] = R[m]
        raio[m] = r
    cand = []
    ys, xs = np.nonzero(melhor >= limite)
    ordem = np.argsort(-melhor[ys, xs])
    for i in ordem:
        y, x = ys[i], xs[i]
        if all(max(abs(x - cx), abs(y - cy)) >= DIST_MIN for cx, cy, _, _ in cand):
            cand.append((int(x), int(y), int(raio[y, x]), float(melhor[y, x])))
        if len(cand) >= 40:
            break
    ox, oy = area[0], area[1]
    return [(int(x / ESCALA) + ox, int(y / ESCALA) + oy, r, round(s, 2)) for x, y, r, s in cand]


def forca_maxima(frame, area=AREA_JOGO):
    """Maior pontuação de anel no quadro (0 a 1): útil como série temporal."""
    W = mapa_branco(frame, area)
    return float(max(cv2.filter2D(W, -1, k).max() for k in _KS.values()))


class JanelaAneis:
    """Junta as detecções dos últimos 'janela_s' segundos (o anel pisca)."""

    def __init__(self, janela_s=0.5):
        self.janela_s = janela_s
        self.hist = []                       # [(t, n_aneis)]

    def adicionar(self, t, aneis):
        self.hist.append((t, len(aneis), [(a[0], a[1]) for a in aneis]))
        while self.hist and t - self.hist[0][0] > max(2.0, self.janela_s * 4):
            self.hist.pop(0)

    def posicoes(self, t):
        """Posições dos anéis vistos nos últimos 'janela_s' s (o anel pisca, então junta vários quadros),
        sem repetir o mesmo anel visto em quadros diferentes."""
        out = []
        for tt, _, pos in self.hist:
            if t - tt <= self.janela_s:
                for p in pos:
                    if all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) > 18 for q in out):
                        out.append(p)
        return out

    def max_aneis(self, t):
        v = [n for tt, n, _ in self.hist if t - tt <= self.janela_s]
        return max(v) if v else 0

    def fracao_stunada(self, t, n_monstros):
        """0 a 1: anéis vistos na janela / monstros vivos na Battle list."""
        if n_monstros <= 0:
            return 1.0
        return min(1.0, self.max_aneis(t) / n_monstros)


# ---------------------------------------------------------------------------
# Verificação MONSTRO A MONSTRO (caveira + anel)
# ---------------------------------------------------------------------------
# Todo monstro vivo tem uma caveira branca acima do nome. O anel de stun fica numa
# posição fixa em relação à caveira. Contar anéis não basta: 8 anéis para 9 monstros
# deixa passar o monstro sem stun. Aqui cada caveira procura o seu anel.
_SKULL_B64 = "iVBORw0KGgoAAAANSUhEUgAAABEAAAASCAIAAAAym6IDAAADqUlEQVQoFW3BfVCSdxwA8O8PeHxBhNBAOVtmZq6LuvJwoN1SFNvqXNz5cp1jWtnSbJ5hdJbTTW1nWtk2u1nq2DpsNtSr7lbXdUb5Up22mdPLsYHjlgqPPGigoMjLA+yuu/23zwclxG2ih4ZOav8EgKxM8Tr2OniLMFtm5+ZIlyOKgV4arABwpXjXG9vqotWJtsbH6w2Gpqutr3W6jrZrZ87I4a25WeO0fnp+3jxPEAdzsjMkYvlJRVPhDqPZgQBAXn2uuKREr9flfbAf/k/3vV928PnDd5Xlpy+cyIxFl9q+E0skyO83W4jngwOMMEbs5i1MFstLksY545Cm3+lyl8tPRUdFA5Xy69DDk8dPoc6en5N3J9lsNsPMDJVC25a4bX1kZEhICEDA5XJbrVatTkuSnvhNcewI9uTkxJG8QwgAvrzYHMHhcLhRSfxdDCYDwzAajQYAPp/P4/GsOlbGpybNptnAEq6o+qpUzEOZGemPnww2X21Nz8jicjh0Oh3DMIQQAAQCAa/X63Q6LQuLmvu9tefqzkq3rq45Udoe4dDzFz/29AkEAh6Ptz40GACWST8AsGgUAHjj8szj+Pj42OH8/AbZ7pWVFSRK4o+OT/2g7s3KyrItL92/fYdCpeTLZBRE6bvV7fV6P8rJjWSznww8LsrNbSlNczjsKGFj9PSs+fK163vTxRUln7549gwAbqj7aDRqYV4OAAhEojbljcnfR0sKj3Z+XuCwL6GIMAwLYxMWS8ct9aWaahzH19zuB0PDQRgmSU0JDQ6O4vGqG5tKZQX3lI2ry9aVZSti06lhEdFGo+nB8NORwQG1SvW+RNL6zbcIIblCMfjoYcEnRcJ0cXbaXlWznAJ+CPgQm06zOUkAUPb0vhMTg1Epe0SisrITPp+vo/P7kZFRjz+g+3u64shhAFDWH2eyWAgAjpV/JhAKmSwWsWjlcrnbE9/VaqcCfuBv5/+h05kJnMOJtBO40/JPVW3LzQtlqP0nlUiYanfY11xr2okJL+nj8GJiN2wEQDOzr58O9Ad77NLicjqdHh4ern81dijnY9TWpXovOTkkKPgvw3T+vg8BoEvdvSU+kfSRGs2j87VfAMAdTX/C5ni3x/3q5chR2TEEAF93tEul0tGx32TZB2tqzjY2XoT/RDGDCLvn5t3bKSmpfV3t1VUN5Qd2ovq6uvqGhsvX2yhej6KisrKyAjfhuAm3EMTC4kIouE1L7tNF+xhxO883tBSmbvCS6F/SEqceVmkYWgAAAABJRU5ErkJggg=="
_SKULL = cv2.imdecode(np.frombuffer(base64.b64decode(_SKULL_B64), np.uint8), cv2.IMREAD_COLOR)
OFFSET_ANEL = (-10, 52)        # centro do anel = centro da caveira + isto (medido nos quadros)
TOL_ANEL = 30                  # distância máxima (px) entre o anel visto e o esperado


def achar_caveiras(frame, limite=0.80, area=AREA_JOGO):
    """Posições (x, y, pontuacao) do centro da caveira de cada monstro VIVO na tela."""
    res, x0, y0 = _mapa_caveira(frame, area)
    return caveiras_do_mapa((res, x0, y0), limite)


def caveiras_do_mapa(mapa, limite=0.80):
    """Mesmo resultado de achar_caveiras, a partir do mapa já calculado (_mapa_caveira): o bot calcula 1 vez por quadro."""
    res, x0, y0 = mapa
    ys, xs = np.where(res >= limite)
    h, w = _SKULL.shape[:2]
    pts = []
    for y, x in sorted(zip(ys, xs), key=lambda p: -res[p[0], p[1]]):
        if all(abs(x - px) > 12 or abs(y - py) > 12 for px, py, _ in pts):
            pts.append((int(x), int(y), float(res[y, x])))
    return [(x + w // 2 + x0, y + h // 2 + y0, round(s, 2)) for x, y, s in pts]


def status_por_monstro(caveiras, aneis, n_lista=None):
    """caveiras: [(x,y,s)], aneis: [(x,y,...)] (já acumulados numa janela de ~0,5 s).
    Devolve (lista de {x,y,stunado}, n_sem_stun).
    n_sem_stun inclui monstros da Battle list SEM caveira visível (rótulo cortado no topo
    da tela, por exemplo) que não tenham um anel sobrando: na dúvida, conta como sem stun."""
    usados = set()
    saida = []
    for sx, sy, _ in caveiras:
        ex, ey = sx + OFFSET_ANEL[0], sy + OFFSET_ANEL[1]
        melhor, dmin = None, TOL_ANEL + 1
        for i, a in enumerate(aneis):
            d = max(abs(a[0] - ex), abs(a[1] - ey))
            if i not in usados and d < dmin:
                melhor, dmin = i, d
        if melhor is not None:
            usados.add(melhor)
        saida.append({"x": sx, "y": sy, "stunado": melhor is not None})
    sem = sum(1 for m in saida if not m["stunado"])
    if n_lista is not None and n_lista > len(caveiras):
        sobrando = len(aneis) - len(usados)
        sem += max(0, (n_lista - len(caveiras)) - sobrando)
    return saida, sem


# ---------------------------------------------------------------------------
# Onde está o Ditto? (o rótulo SEM caveira)
# ---------------------------------------------------------------------------
# Todo rótulo (nome + barra de vida pequena com contorno preto) pertence a um monstro (tem caveira
# acima) ou ao Ditto/treinador (não tem). A cor do nome muda com a vida, então a busca é pela BARRA:
# retângulo de 2 a 4 px de altura, ~26 px de largura (parte colorida + resto preto), contorno preto.
CENTRO_TREINADOR = (959, 452)        # a barra do treinador fica aqui (personagem sempre no centro)
BARRA_DY = 28                        # barra do monstro = centro da caveira + (0, 28)
BARRA_LARGURA = 26


def achar_barras(frame, area=AREA_JOGO, sat_min=90, val_min=85):
    """Barras de vida de rótulos: retângulos de 2 a 4 px de altura com contorno preto.
    Os rótulos às vezes ficam esmaecidos (semitransparentes), por isso o limite de cor é baixo;
    o contorno preto e a largura da barra evitam confundir com o cenário."""
    x0, y0, x1, y1 = area
    g = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(g, cv2.COLOR_BGR2HSV)
    col = ((hsv[..., 1] >= sat_min) & (hsv[..., 2] >= val_min)).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(col, connectivity=8)
    escuro = g.max(axis=2) < 75
    saida = []
    H, W = escuro.shape
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (2 <= h <= 4 and 3 <= w <= 28 and a >= w * h * 0.8):
            continue
        # contorno preto acima e abaixo da barra INTEIRA (26 px seguidos): folhagem não tem isso
        xa, xb = max(0, x - 1), min(W, x + BARRA_LARGURA + 1)
        cima, baixo = escuro[max(0, y - 1), xa:xb].mean(), escuro[min(H - 1, y + h), xa:xb].mean()
        if cima < 0.85 or baixo < 0.85:
            continue
        # a barra inteira (parte colorida + resto preto) tem ~26 px
        faixa = g[y + h // 2, x:x + BARRA_LARGURA]
        if faixa.shape[0] < BARRA_LARGURA - 2:
            continue
        v = cv2.cvtColor(faixa.reshape(1, -1, 3), cv2.COLOR_BGR2HSV)[0]
        ok = ((v[:, 1] >= sat_min) & (v[:, 2] >= val_min)) | (v[:, 2] < 75)
        if ok.mean() < 0.85:
            continue
        saida.append((int(x + x0 + w / 2), int(y + y0 + h / 2), int(w)))
    return saida


def _mapa_caveira(frame, area=AREA_JOGO):
    x0, y0, x1, y1 = area
    return cv2.matchTemplate(frame[y0:y1, x0:x1], _SKULL, cv2.TM_CCOEFF_NORMED), x0, y0


def _tem_caveira_acima(mapa, bx, by, tol=7, minimo=0.45):
    """Há uma caveira (mesmo esmaecida) logo acima da barra em (bx, by)?"""
    res, x0, y0 = mapa
    h, w = _SKULL.shape[:2]
    cx, cy = bx - x0 - w // 2, by - BARRA_DY - y0 - h // 2          # canto da janela da caveira esperada
    xa, xb = max(0, cx - tol), min(res.shape[1], cx + tol + 1)
    ya, yb = max(0, cy - tol), min(res.shape[0], cy + tol + 1)
    if xb <= xa or yb <= ya:
        return False
    return float(res[ya:yb, xa:xb].max()) >= minimo


def achar_ditto(frame, caveiras=None, hp=None, mapa=None):
    """(x, y) da barra de vida do Ditto na tela, ou None. É a barra de rótulo que NÃO tem caveira
    acima (monstros têm) e que não é a do treinador (no centro da tela).
    hp (0 a 1, da Battle list) filtra: a parte colorida da barra mede ~26 px x vida (+-4,5 px).
    Chame só quando a Battle list diz que o Ditto está em campo."""
    mapa = mapa if mapa is not None else _mapa_caveira(frame)
    sobra = []
    for bx, by, w in achar_barras(frame):
        if w < 6:                    # a barra do Ditto (mesmo com pouca vida) é larga; 3 a 5 px é cenário
            continue
        if hp is not None and abs(w - BARRA_LARGURA * hp) > 4.5:
            continue
        if max(abs(bx - CENTRO_TREINADOR[0]), abs(by - CENTRO_TREINADOR[1])) <= 22:
            continue
        if _tem_caveira_acima(mapa, bx, by):
            continue
        sobra.append((bx, by, w))
    if not sobra:
        return None
    sobra.sort(key=lambda b: -b[2])           # várias: a mais larga (o Ditto sempre tem vida)
    return sobra[0][:2]


def distancia_tiles(ditto, caveira, tile=72.3):
    """Distância (dx, dy em tiles) do Ditto até um monstro (centro da caveira)."""
    return (caveira[0] - ditto[0]) / tile, (caveira[1] + BARRA_DY - ditto[1]) / tile


def na_area_do_stun(dx, dy, meio_lado=3.0, folga=0.6):
    """A área do Whirlwind é um quadrado 7x7 (3 tiles para cada lado) sem os 4 cantos."""
    ax, ay = abs(dx), abs(dy)
    if ax > meio_lado + folga or ay > meio_lado + folga:
        return False
    return not (ax > meio_lado - 0.5 and ay > meio_lado - 0.5)
