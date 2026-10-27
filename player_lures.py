# -*- coding: utf-8 -*-
"""
Player de lures: percorre o laço gravado pelo gravador_lures.py.

MODOS
  Simulação : um personagem virtual anda no seu mapa de obstáculos. Não lê a tela
              e não aperta tecla nenhuma. Serve para testar a lógica inteira
              (pode acelerar o tempo e simular monstros bloqueando o caminho).
  Dry-run   : lê a coordenada REAL do jogo e mostra o que pressionaria,
              mas NÃO aperta nada. Você anda à mão e vê o bot "acompanhando".
  Real      : lê a coordenada e aperta WASD de verdade (o jogo precisa estar em foco).

COMO ANDA
  - Planeja o caminho entre pontos de passagem com A* no mapa de obstáculos
    (diagonal custa o mesmo que dois passos retos, como medido).
  - Segura as teclas e acompanha a coordenada; quando chega no tile alvo, solta
    (o passo seguinte só começa ~0,3 s depois, então não passa do ponto).
  - Se a posição não muda (monstro bloqueando), destrava em escada:
    diagonal de um lado, diagonal do outro, bloqueia o tile e replaneja,
    recua e replaneja, e por fim declara PERDIDO e solta tudo.
  - Em cada ponto de parada ele PARA (aqui é só uma espera: o combate virá depois).
  - Humanização opcional (0, 1 ou 2): ruído na escolha do caminho, pontos de
    passagem deslocados, micro-pausas e um desvio sorteado a cada 1 a 4 min.

PARADA DE EMERGÊNCIA: Ctrl+Alt+X (global, precisa do pynput) ou o botão Parar.
Em qualquer falha, todas as teclas são soltas.

Dependências:
    pip install opencv-python numpy pillow mss pynput pydirectinput
Arquivos na mesma pasta: leitor_coord.py, editor_mapa.py (e o mapa_obstaculos.json)
"""
import os
import sys
import json
import time
import heapq
import queue
import random
import threading
import subprocess

import numpy as np
from PIL import Image, ImageTk, ImageDraw
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from leitor_coord import ler_coord, REGIAO_PADRAO
from editor_mapa import ObstacleMap

if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    from pynput import keyboard as pk
except Exception:
    pk = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_GRAVADOR = os.path.join(APP_DIR, "config_gravador.json")
CFG_PLAYER = os.path.join(APP_DIR, "config_player.json")
MAPA_OBST_PADRAO = os.path.join(APP_DIR, "mapa_obstaculos.json")

PASSO_RETO_S = 0.316        # medido no jogo real
PASSO_DIAG_S = 0.617
PARADA_ALERTA_S = 30.0      # fase 5: parado esperando o portão há mais que isso = avisa (e segue esperando)
LIMIAR_TRAVADO_S = 0.85     # sem mudar de tile por mais que isso = travado (diagonal leva ~0,62 s)

DIRS8 = [(0, -1), (0, 1), (1, 0), (-1, 0), (1, -1), (-1, -1), (1, 1), (-1, 1)]
TECLAS_DIR = {(0, -1): {"W"}, (0, 1): {"S"}, (1, 0): {"D"}, (-1, 0): {"A"},
              (1, -1): {"W", "D"}, (-1, -1): {"W", "A"},
              (1, 1): {"S", "D"}, (-1, 1): {"S", "A"}}
NOME_DIR = {(0, -1): "N", (0, 1): "S", (1, 0): "L", (-1, 0): "O",
            (1, -1): "NE", (-1, -1): "NO", (1, 1): "SE", (-1, 1): "SO"}


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def rdp(pts, eps):
    """Simplifica a polilinha (Ramer-Douglas-Peucker)."""
    if len(pts) < 3:
        return list(pts)
    a = np.array(pts[0], float)
    ab = np.array(pts[-1], float) - a
    n = float(np.hypot(*ab))
    best, bi = -1.0, 0
    for i in range(1, len(pts) - 1):
        v = np.array(pts[i], float) - a
        d = float(np.hypot(*v)) if n == 0 else abs(ab[0] * v[1] - ab[1] * v[0]) / n
        if d > best:
            best, bi = d, i
    if best > eps:
        return rdp(pts[:bi + 1], eps)[:-1] + rdp(pts[bi:], eps)
    return [pts[0], pts[-1]]


def montar_rota(lures_json, eps=1.0):
    """lures.json -> [{nome, z, wps:[(x,y)...], pontos:[(x,y)...]}]"""
    rota = []
    for lu in lures_json["lures"]:
        pts = []
        for x, y in lu["pontos"]:
            if not pts or pts[-1] != (x, y):
                pts.append((x, y))
        wps = rdp(pts, eps)
        rota.append({"nome": lu["nome"], "z": lu["inicio"][2], "wps": wps, "pontos": pts, "ditto_fim": lu.get("ditto_fim")})
    return rota


# ----------------------------------------------------------------------------
# Tempo (permite acelerar a simulação)
# ----------------------------------------------------------------------------
class Relogio:
    def __init__(self, fator=1.0):
        self.f = float(fator)
        self.t0 = time.perf_counter()

    def agora(self):
        return (time.perf_counter() - self.t0) * self.f

    def dormir(self, s):
        time.sleep(max(0.0, s) / self.f)


# ----------------------------------------------------------------------------
# Teclas
# ----------------------------------------------------------------------------
class Teclas:
    def __init__(self):
        self.down = set()

    def set(self, want):
        want = set(want)
        for k in self.down - want:
            self._solta(k)
        for k in want - self.down:
            self._aperta(k)
        self.down = want

    def soltar_tudo(self):
        self.set(set())

    def _aperta(self, k):
        pass

    def _solta(self, k):
        pass


class TeclasSeco(Teclas):
    """Dry-run: não aperta nada."""


class TeclasSim(Teclas):
    def __init__(self, mundo):
        super().__init__()
        self.mundo = mundo

    def set(self, want):
        super().set(want)
        self.mundo.set_keys(self.down)


class TeclasPynput(Teclas):
    def __init__(self):
        super().__init__()
        from pynput.keyboard import Controller
        self.c = Controller()

    def _aperta(self, k):
        self.c.press(k.lower())

    def _solta(self, k):
        self.c.release(k.lower())


class TeclasDirect(Teclas):
    """pydirectinput envia scancodes (alguns jogos ignoram o pynput)."""

    def __init__(self):
        super().__init__()
        import pydirectinput
        pydirectinput.PAUSE = 0.0
        self.p = pydirectinput

    def _aperta(self, k):
        self.p.keyDown(k.lower())

    def _solta(self, k):
        self.p.keyUp(k.lower())


# ----------------------------------------------------------------------------
# Mundo: de onde vem a posição
# ----------------------------------------------------------------------------
class MundoReal:
    """Lê a coordenada do jogo na tela em laço."""

    def __init__(self, regiao):
        self.regiao = regiao
        self.p = None
        self.t_ok = 0.0
        self.cond = threading.Condition()
        self.vivo = False

    def iniciar(self):
        self.vivo = True
        threading.Thread(target=self._loop, daemon=True).start()

    def parar(self):
        self.vivo = False

    def _loop(self):
        import mss
        x, y, w, h = self.regiao
        mon = {"left": x, "top": y, "width": w, "height": h}
        with mss.mss() as sct:
            while self.vivo:
                c = ler_coord(np.array(sct.grab(mon)))
                if c:
                    with self.cond:
                        self.t_ok = time.perf_counter()
                        if c != self.p:
                            self.p = c
                            self.cond.notify_all()
                time.sleep(0.002)

    def pos(self):
        with self.cond:
            if time.perf_counter() - self.t_ok > 0.8:
                return None
            return self.p

    def aguardar_mudanca(self, p_ref, timeout):
        fim = time.perf_counter() + timeout
        with self.cond:
            while self.p == p_ref:
                resto = fim - time.perf_counter()
                if resto <= 0:
                    return None
                self.cond.wait(resto)
            return self.p


class MundoSim:
    """Personagem virtual: anda no mapa de obstáculos obedecendo às teclas."""

    def __init__(self, obst, inicio, rel):
        self.obst, self.rel = obst, rel
        self.p = tuple(inicio)
        self.keys = set()
        self.livre_em = 0.0
        self.bloqueios = []          # (x, y, t_fim)
        self.cond = threading.Condition()
        self.vivo = False
        self.rng = random.Random(7)

    def iniciar(self):
        self.vivo = True
        threading.Thread(target=self._loop, daemon=True).start()

    def parar(self):
        self.vivo = False

    def set_keys(self, keys):
        with self.cond:
            self.keys = set(keys)

    def bloquear(self, x, y, dur):
        with self.cond:
            self.bloqueios.append((x, y, self.rel.agora() + dur))

    def _livre(self, x, y, agora):
        if not self.obst.walkable_game(x, y):
            return False
        return not any(bx == x and by == y and agora < tf for bx, by, tf in self.bloqueios)

    def _loop(self):
        while self.vivo:
            agora = self.rel.agora()
            with self.cond:
                keys = set(self.keys)
                if keys and agora >= self.livre_em:
                    dx = ("D" in keys) - ("A" in keys)
                    dy = ("S" in keys) - ("W" in keys)
                    if (dx, dy) != (0, 0):
                        nx, ny = self.p[0] + dx, self.p[1] + dy
                        if self._livre(nx, ny, agora):
                            self.p = (nx, ny, self.p[2])
                            base = PASSO_DIAG_S if dx and dy else PASSO_RETO_S
                            self.livre_em = agora + base * self.rng.uniform(0.97, 1.05)
                            self.cond.notify_all()
            time.sleep(0.0005)

    def pos(self):
        return self.p

    def aguardar_mudanca(self, p_ref, timeout):
        fim = time.perf_counter() + timeout / self.rel.f
        with self.cond:
            while self.p == p_ref:
                resto = fim - time.perf_counter()
                if resto <= 0:
                    return None
                self.cond.wait(resto)
            return self.p


# ----------------------------------------------------------------------------
# Humanização
# ----------------------------------------------------------------------------
class Humano:
    PARAMS = {
        0: dict(ruido=0.0, jit=0, p_jit=0.0, p_pausa=0.0, pausa=(0, 0), var=None),
        1: dict(ruido=0.25, jit=1, p_jit=0.5, p_pausa=0.03, pausa=(0.15, 0.6), var=(60, 240)),
        2: dict(ruido=0.6, jit=2, p_jit=0.7, p_pausa=0.06, pausa=(0.2, 1.2), var=(45, 180)),
    }

    def __init__(self, nivel, rel, rng):
        self.n, self.rel, self.rng = nivel, rel, rng
        self.P = self.PARAMS[nivel]
        self.prox = None
        self.agendar()

    def agendar(self):
        v = self.P["var"]
        self.prox = self.rel.agora() + self.rng.uniform(*v) if v else None

    def ruido(self):
        return self.P["ruido"]

    def variacao_pendente(self):
        return self.prox is not None and self.rel.agora() >= self.prox

    def pausa(self):
        if self.rng.random() < self.P["p_pausa"]:
            return self.rng.uniform(*self.P["pausa"])
        return 0.0

    def jitter(self, wp, andavel):
        j = self.P["jit"]
        if not j or self.rng.random() > self.P["p_jit"]:
            return wp
        for _ in range(8):
            c = (wp[0] + self.rng.randint(-j, j), wp[1] + self.rng.randint(-j, j))
            if c != wp and andavel(*c):
                return c
        return wp


# ----------------------------------------------------------------------------
# Player
# ----------------------------------------------------------------------------
class Player:
    def __init__(self, obst, rota, mundo, teclas, rel, nivel_hum=1, parada_s=6.0,
                 seco=False, log=print, verbose=False, seed=None, portao=None, tol_parada=0):
        self.tol_parada = tol_parada   # tiles de folga no ÚLTIMO ponto do lure (0 = exato; seco/acompanhar usa 2)
        self._t_status = -99.0
        self.portao = portao      # fase 5: callable(agora) -> (liberado, motivo); None = espera fixa (parada_s) como antes
        self.obst, self.rota = obst, rota
        self.mundo, self.teclas, self.rel = mundo, teclas, rel
        self.parada_s, self.seco, self.log, self.verbose = parada_s, seco, log, verbose
        self.rng = random.Random(seed)
        self.hum = Humano(nivel_hum, rel, self.rng)
        self.parar = threading.Event()
        self.temp_bloq = {}
        self.caminho = None
        self.estado = {"lure": 0, "teclas": "", "info": ""}
        self.stats = {"voltas": 0, "travadas": 0, "replanos": 0}

    # -- utilidades -----------------------------------------------------------
    def andavel(self, x, y):
        if self.temp_bloq.get((x, y), -1) > self.rel.agora():
            return False
        return self.obst.walkable_game(x, y)

    def _teclas(self, want):
        if set(want) != self.teclas.down:
            self.teclas.set(want)
            self.estado["teclas"] = "+".join(sorted(want))
            if self.verbose:
                self.log(f"   teclas: {self.estado['teclas'] or '(soltas)'}")

    def _esperar(self, s):
        """dorme em pedaços, soltando as teclas antes."""
        fim = self.rel.agora() + s
        while self.rel.agora() < fim and not self.parar.is_set():
            self.rel.dormir(min(0.05, fim - self.rel.agora()))

    # -- planejamento (A*) -------------------------------------------------------
    def planejar(self, a, b, ruido=0.0):
        if a == b:
            return [a]
        marg = 20
        x0, x1 = min(a[0], b[0]) - marg, max(a[0], b[0]) + marg
        y0, y1 = min(a[1], b[1]) - marg, max(a[1], b[1]) + marg
        seed = self.rng.getrandbits(30)
        heap = [(abs(a[0] - b[0]) + abs(a[1] - b[1]), 0.0, a, -1)]
        best = {(a, -1): 0.0}
        veio = {}
        n = 0
        while heap:
            f, g, cur, pd = heapq.heappop(heap)
            if g > best.get((cur, pd), 1e18) + 1e-9:
                continue
            if cur == b:
                path, key = [cur], (cur, pd)
                while key in veio:
                    key = veio[key]
                    path.append(key[0])
                return path[::-1]
            n += 1
            if n > 40000:
                break
            for di, (dx, dy) in enumerate(DIRS8):
                nx, ny = cur[0] + dx, cur[1] + dy
                if not (x0 <= nx <= x1 and y0 <= ny <= y1) or not self.andavel(nx, ny):
                    continue
                c = 2.0 if dx and dy else 1.0
                if pd != -1 and di != pd:
                    c += 0.15
                if ruido:
                    c += ruido * (((nx * 73856093) ^ (ny * 19349663) ^ seed ^ (di * 83492791)) & 255) / 255.0
                ng = g + c
                key = ((nx, ny), di)
                if ng < best.get(key, 1e18):
                    best[key] = ng
                    veio[key] = (cur, pd)
                    heapq.heappush(heap, (ng + abs(nx - b[0]) + abs(ny - b[1]), ng, (nx, ny), di))
        return None

    def _mais_proximo_andavel(self, p):
        if self.andavel(*p):
            return p
        for r in range(1, 4):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    c = (p[0] + dx, p[1] + dy)
                    if self.andavel(*c):
                        return c
        return p

    # -- andar até um ponto -----------------------------------------------------------
    def seguir(self, alvo, tol, z):
        alvo = self._mais_proximo_andavel(alvo)
        self.caminho, idx = None, {}
        sem_mover = 0
        sem_leitura_desde = None
        while True:
            if self.parar.is_set():
                return "PARADO"
            p = self.mundo.pos()
            if p is None:
                self._teclas(set())
                sem_leitura_desde = sem_leitura_desde or self.rel.agora()
                if self.rel.agora() - sem_leitura_desde > 12:
                    return "SEM_LEITURA"
                self.rel.dormir(0.05)
                continue
            sem_leitura_desde = None
            if p[2] != z:
                return "PERDIDO"
            pxy = (p[0], p[1])
            self.estado["info"] = f"pos {pxy} -> alvo {alvo}"
            if self.seco and self.rel.agora() - self._t_status >= 5.0:
                self._t_status = self.rel.agora()
                self.log(f"acompanhando lure {self.estado['lure']}: você em {pxy}, alvo {alvo} ({cheb(pxy, alvo)} tiles)")
            if cheb(pxy, alvo) <= tol:
                self._teclas(set())
                return "OK"
            if self.caminho is None or pxy not in idx:
                self.caminho = self.planejar(pxy, alvo, self.hum.ruido())
                self.stats["replanos"] += 1
                if not self.caminho:
                    self.temp_bloq.clear()
                    self.caminho = self.planejar(pxy, alvo, 0.0)
                    if not self.caminho:
                        self._teclas(set())
                        self.log(f"sem caminho de {pxy} até {alvo} no mapa")
                        return "SEM_CAMINHO"
                idx = {t: i for i, t in enumerate(self.caminho)}
            prox = self.caminho[idx[pxy] + 1]
            d = (prox[0] - pxy[0], prox[1] - pxy[1])
            self._teclas(TECLAS_DIR[d])
            novo = self.mundo.aguardar_mudanca(p, LIMIAR_TRAVADO_S)
            if novo is not None:
                sem_mover = 0
                continue
            if self.seco:
                continue
            sem_mover += 1
            r = self.destravar(p, d, sem_mover)
            if r == "MOVEU":
                sem_mover = 0
            elif r:
                return r

    def destravar(self, p, d, nivel):
        pxy = (p[0], p[1])
        self.stats["travadas"] += 1
        self.log(f"travado em {pxy} indo {NOME_DIR[d]} (tentativa {nivel})")
        if d[0] and d[1]:
            alts = [(d[0], 0), (0, d[1])]
        elif d[0]:
            alts = [(d[0], 1), (d[0], -1)]
        else:
            alts = [(1, d[1]), (-1, d[1])]
        self.rng.shuffle(alts)
        ahead = (pxy[0] + d[0], pxy[1] + d[1])
        if nivel in (1, 2):
            a = alts[nivel - 1]
            self._teclas(TECLAS_DIR[a])
            return "MOVEU" if self.mundo.aguardar_mudanca(p, 0.7) is not None else None
        if nivel == 3:
            self.temp_bloq[ahead] = self.rel.agora() + 12
            self.caminho = None
            self.log(f"   bloqueando {ahead} por 12 s e replanejando")
            return None
        if nivel == 4:
            self._teclas(TECLAS_DIR[(-d[0], -d[1])])
            moveu = self.mundo.aguardar_mudanca(p, 0.8) is not None
            self.temp_bloq[ahead] = self.rel.agora() + 12
            self.caminho = None
            self.log("   recuando e replanejando")
            return "MOVEU" if moveu else None
        self._teclas(set())
        self.log("PERDIDO: não consegui destravar")
        return "PERDIDO"

    # -- lures e paradas -----------------------------------------------------------------
    def _mais_proximo_da_rota(self, p):
        best = (1e9, 0, 0)
        for li, lu in enumerate(self.rota):
            for k, w in enumerate(lu["wps"]):
                d = float(np.hypot(w[0] - p[0], w[1] - p[1]))
                if d < best[0]:
                    best = (d, li, k)
        return best

    def percorrer_lure(self, li, alvos):
        lu = self.rota[li]
        z = lu["z"]
        n = len(alvos)
        for i, wp in enumerate(alvos):
            ultimo = i == n - 1
            if not ultimo and i < n - 2:
                wp = self.hum.jitter(wp, self.andavel)
            if not ultimo:
                if self.hum.variacao_pendente():
                    p = self.mundo.pos()
                    if p:
                        d = self.hum.jitter((wp[0], wp[1]), self.andavel)
                        r = max(2, self.hum.P["jit"] + 1)
                        for _ in range(10):
                            c = (wp[0] + self.rng.randint(-r, r), wp[1] + self.rng.randint(-r, r))
                            if c != wp and self.andavel(*c):
                                d = c
                                break
                        self.log(f"variação: desvio por {d}")
                        res = self.seguir(d, 1, z)
                        if res != "OK":
                            return res
                    self.hum.agendar()
                pausa = self.hum.pausa()
                if pausa:
                    self.log(f"micro-pausa {pausa:.2f} s")
                    self._teclas(set())
                    self._esperar(pausa)
            res = self.seguir(wp, self.tol_parada if ultimo else 1, z)
            if res == "PERDIDO" and ultimo:
                p = self.mundo.pos()
                if p and cheb((p[0], p[1]), wp) <= 2:
                    return "OK"
            if res != "OK":
                return res
        return "OK"

    def parada(self, li):
        lu = self.rota[li]
        self._teclas(set())
        if self.portao is None:                      # modo antigo (GUI/simulação): só espera
            dur = self.parada_s * self.rng.uniform(0.9, 1.15)
            self.log(f"PARADA {li + 1} em {lu['wps'][-1]} (combate aqui; aguardando {dur:.1f} s)")
            self._esperar(dur)
            return
        # fase 5: fica PARADO até o combate acabar. Nunca anda por tempo: só o portão libera.
        self.log(f"PARADA {li + 1} em {lu['wps'][-1]}: aguardando o fim do combate (portão)")
        self._aguardar_portao(f"indo para o lure {(li + 1) % len(self.rota) + 1}")

    def _aguardar_portao(self, destino):
        """Solta as teclas e espera o portão abrir. True = abriu; False = pediram para parar."""
        t0 = self.rel.agora()
        ult_log, ult_alerta, motivo_ant = t0, t0, None
        while not self.parar.is_set():
            self._teclas(set())
            ok, motivo = self.portao(self.rel.agora())
            agora = self.rel.agora()
            if ok:
                self.log(f"portão ABERTO após {agora - t0:.1f} s: combate acabou, {destino}")
                return True
            if motivo != motivo_ant and agora - ult_log >= 2.0 or agora - ult_log >= 10.0:
                ult_log, motivo_ant = agora, motivo
                self.log(f"   portão fechado ({agora - t0:.0f} s): {motivo}")
            if agora - t0 >= PARADA_ALERTA_S and agora - ult_alerta >= 10.0:
                ult_alerta = agora
                self.log(f"[ALERTA] parado há {agora - t0:.0f} s e o portão não abriu ({motivo}). NÃO ando sozinho: resolva à mão.")
                try:
                    import winsound
                    winsound.Beep(1000, 300)
                except Exception:
                    pass
            self.rel.dormir(0.1)
        return False

    def rodar(self):
        try:
            for _ in range(100):
                if self.mundo.pos() or self.parar.is_set():
                    break
                self.rel.dormir(0.05)
            p = self.mundo.pos()
            if p is None:
                self.log("Não consegui ler a posição do jogo. Parando.")
                return "SEM_LEITURA"
            d, li, k = self._mais_proximo_da_rota(p)
            self.log(f"Início em {p[:2]}; ponto mais próximo da rota: lure {li + 1}, passagem {k} ({d:.1f} tiles)")
            if d > 25:
                self.log("Longe demais da rota (mais de 25 tiles). Chegue perto do laço e tente de novo.")
                return "LONGE"
            if self.portao is not None and not self.seco:       # real: só começa a andar com o combate acabado
                self.log("Início: aguardando o portão abrir (sem monstros, Ditto presente) antes de andar")
                if not self._aguardar_portao("começando a andar"):
                    return "PARADO"
            ini = k
            t_volta = self.rel.agora()
            while not self.parar.is_set():
                self.estado["lure"] = li + 1
                t0 = self.rel.agora()
                res = self.percorrer_lure(li, self.rota[li]["wps"][ini:])
                ini = 1
                if res != "OK" and self.seco and res in ("SEM_CAMINHO", "PERDIDO"):
                    pq = self.mundo.pos()                      # seco: você andou por onde o laço não previa; reacompanha daí
                    if pq:
                        d, li, k = self._mais_proximo_da_rota(pq)
                        self.log(f"seco: você saiu do caminho esperado ({res}); reacompanhando a partir do lure {li + 1}, passagem {k} ({d:.1f} tiles)")
                        ini = k
                        continue
                if res != "OK":
                    self.log(f"Interrompido: {res}")
                    return res
                self.log(f"lure {li + 1} concluído em {self.rel.agora() - t0:.1f} s")
                self.parada(li)
                li = (li + 1) % len(self.rota)
                if li == 0:
                    self.stats["voltas"] += 1
                    self.log(f"=== volta {self.stats['voltas']} completa em {self.rel.agora() - t_volta:.0f} s "
                             f"(travadas: {self.stats['travadas']}) ===")
                    t_volta = self.rel.agora()
            return "PARADO"
        except Exception as e:
            self.log(f"ERRO: {e!r}")
            return "ERRO"
        finally:
            self.teclas.soltar_tudo()


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class App:
    ESCALA = 0.8

    def __init__(self, root):
        self.root = root
        root.title("Player de Lures")
        root.geometry("1180x800")
        self.fila_log = queue.Queue()
        self.rodando = False
        self.player = None
        self.mundo = None
        self.thread = None
        self.obst = None
        self.rota = None
        self.hk = None
        self.photo = None
        self.marker = None
        self.regiao = tuple(REGIAO_PADRAO)

        self.v_lures = tk.StringVar()
        self.v_obst = tk.StringVar(value=MAPA_OBST_PADRAO)
        self.v_modo = tk.StringVar(value="sim")
        self.v_vel = tk.StringVar(value="10")
        self.v_hum = tk.StringVar(value="1")
        self.v_fid = tk.StringVar(value="1.0")
        self.v_parada = tk.StringVar(value="6")
        self.v_backend = tk.StringVar(value="pydirectinput")
        self.v_monstros = tk.BooleanVar(value=True)
        self.v_verbose = tk.BooleanVar(value=False)
        self.v_status = tk.StringVar(value="Parado.")
        self._cfg_load()

        left = ttk.Frame(root, width=430)
        left.pack(side="left", fill="y", padx=6, pady=6)
        left.pack_propagate(False)
        f = ttk.LabelFrame(left, text="Arquivos")
        f.pack(fill="x", pady=3)
        for lab, var, cmd in (("lures.json", self.v_lures, self.pick_lures), ("Mapa obst.", self.v_obst, self.pick_obst)):
            r = ttk.Frame(f); r.pack(fill="x", padx=4, pady=2)
            ttk.Label(r, text=lab, width=10).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(r, text="...", width=3, command=cmd).pack(side="left", padx=2)

        f = ttk.LabelFrame(left, text="Modo")
        f.pack(fill="x", pady=3)
        ttk.Radiobutton(f, text="Simulação (personagem virtual, não toca no jogo)", variable=self.v_modo, value="sim").pack(anchor="w", padx=6)
        ttk.Radiobutton(f, text="Dry-run (lê o jogo, NÃO aperta tecla)", variable=self.v_modo, value="seco").pack(anchor="w", padx=6)
        ttk.Radiobutton(f, text="REAL (aperta WASD no jogo)", variable=self.v_modo, value="real").pack(anchor="w", padx=6)

        f = ttk.LabelFrame(left, text="Opções")
        f.pack(fill="x", pady=3)
        g = ttk.Frame(f); g.pack(fill="x", padx=4, pady=2)
        ttk.Label(g, text="Humanização").grid(row=0, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.v_hum, values=["0", "1", "2"], width=4, state="readonly").grid(row=0, column=1, padx=4)
        ttk.Label(g, text="Parada (s)").grid(row=0, column=2, sticky="e")
        ttk.Entry(g, textvariable=self.v_parada, width=5).grid(row=0, column=3, padx=4)
        ttk.Label(g, text="Fidelidade").grid(row=1, column=0, sticky="w")
        cb = ttk.Combobox(g, textvariable=self.v_fid, values=["0.6", "1.0", "2.0"], width=4, state="readonly")
        cb.grid(row=1, column=1, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.carregar())
        ttk.Label(g, text="(menor = mais fiel ao gravado)", foreground="#666").grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Label(g, text="Velocidade da sim.").grid(row=2, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.v_vel, values=["1", "5", "10", "20"], width=4).grid(row=2, column=1, padx=4)
        ttk.Label(g, text="Teclas (real)").grid(row=3, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.v_backend, values=["pydirectinput", "pynput"], width=14, state="readonly").grid(row=3, column=1, columnspan=3, padx=4)
        ttk.Checkbutton(f, text="Simular monstros bloqueando o caminho (só simulação)", variable=self.v_monstros).pack(anchor="w", padx=6)
        ttk.Checkbutton(f, text="Detalhar teclas no log", variable=self.v_verbose).pack(anchor="w", padx=6)
        ttk.Label(f, text="Região da coordenada vem do config_gravador.json", foreground="#666").pack(anchor="w", padx=6)

        b = ttk.Frame(left); b.pack(fill="x", pady=4)
        self.btn_ini = ttk.Button(b, text="Iniciar", command=self.iniciar); self.btn_ini.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(b, text="PARAR (Ctrl+Alt+X)", command=self.parar, state="disabled"); self.btn_stop.pack(side="left", padx=3)
        ttk.Label(left, textvariable=self.v_status, wraplength=410, justify="left").pack(anchor="w", pady=2)
        self.txt = tk.Text(left, font=("Consolas", 9), height=20)
        self.txt.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(root, bg="#202020", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)

        root.protocol("WM_DELETE_WINDOW", self.fechar)
        if pk:
            try:
                self.hk = pk.GlobalHotKeys({"<ctrl>+<alt>+x": lambda: self.fila_log.put("\0parar")})
                self.hk.start()
            except Exception:
                pass
        if not self.v_lures.get():
            self._auto_lures()
        self.carregar()
        root.after(150, self.tick)

    # -- config -----------------------------------------------------------------
    def _cfg_load(self):
        try:
            with open(CFG_GRAVADOR, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            self.regiao = tuple(int(float(c[k])) for k in "xywh")
        except Exception:
            pass
        try:
            with open(CFG_PLAYER, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            for k in ("lures", "obst", "modo", "vel", "hum", "parada", "backend", "fid"):
                if k in c:
                    getattr(self, "v_" + k).set(c[k])
        except Exception:
            pass

    def _cfg_save(self):
        try:
            with open(CFG_PLAYER, "w", encoding="utf-8") as fh:
                json.dump({k: getattr(self, "v_" + k).get() for k in
                           ("lures", "obst", "modo", "vel", "hum", "parada", "backend", "fid")}, fh)
        except Exception:
            pass

    def _auto_lures(self):
        base = os.path.join(APP_DIR, "gravacoes")
        if os.path.isdir(base):
            subs = sorted(d for d in os.listdir(base) if os.path.exists(os.path.join(base, d, "lures.json")))
            if subs:
                self.v_lures.set(os.path.join(base, subs[-1], "lures.json"))

    def pick_lures(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], initialdir=os.path.join(APP_DIR, "gravacoes"))
        if p:
            self.v_lures.set(p)
            self.carregar()

    def pick_obst(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            self.v_obst.set(p)
            self.carregar()

    def log(self, s):
        """Pode ser chamado de qualquer thread: só enfileira; o tick() escreve na tela."""
        self.fila_log.put(s)

    # -- carregar rota e desenhar o mapa ---------------------------------------------------
    def carregar(self):
        self.rota = self.obst = None
        try:
            with open(self.v_lures.get(), "r", encoding="utf-8") as fh:
                lj = json.load(fh)
            self.rota = montar_rota(lj, float(self.v_fid.get()))
            op = self.v_obst.get()
            if not os.path.exists(op):
                op = lj.get("mapa_obstaculos", "")
            if not os.path.exists(op):
                raise ValueError("mapa de obstáculos não encontrado")
            self.obst = ObstacleMap.load(op)
            if not self.obst.anchor:
                raise ValueError("o mapa de obstáculos não tem âncora")
            with open(op, "r", encoding="utf-8") as fh:
                mf = json.load(fh).get("map_file", "")
            img = None
            for c in (mf, os.path.join(os.path.dirname(op), mf), os.path.join(os.path.dirname(op), os.path.basename(mf))):
                if c and os.path.exists(c):
                    img = c
                    break
            self.desenhar_base(img)
            self.v_status.set(f"Rota carregada: {len(self.rota)} lures, "
                              f"{sum(len(r['wps']) for r in self.rota)} pontos de passagem.")
        except Exception as e:
            self.v_status.set(f"Carregue o lures.json e o mapa de obstáculos ({e})")

    def px(self, x, y):
        c, r = self.obst.tile_of_game(x, y)
        t, s = self.obst.tile, self.ESCALA
        return (self.obst.ox + (c + 0.5) * t) * s, (self.obst.oy + (r + 0.5) * t) * s

    def desenhar_base(self, img_path):
        s = self.ESCALA
        if img_path:
            im = Image.open(img_path).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im).convert("RGB")
            im = im.resize((int(im.width * s), int(im.height * s)), Image.NEAREST)
        else:
            im = Image.new("RGB", (600, 600), (40, 40, 40))
        d = ImageDraw.Draw(im)
        cores = [(255, 0, 0), (255, 0, 255), (255, 160, 0), (0, 120, 255), (255, 255, 0)]
        for i, lu in enumerate(self.rota):
            cor = cores[i % len(cores)]
            pts = [self.px(*p) for p in lu["wps"]]
            d.line(pts, fill=cor, width=2)
            for q in pts:
                d.ellipse((q[0] - 2, q[1] - 2, q[0] + 2, q[1] + 2), fill=cor)
            e = pts[-1]
            d.rectangle((e[0] - 5, e[1] - 5, e[0] + 5, e[1] + 5), fill=(0, 0, 0), outline=cor)
            d.text((e[0] + 8, e[1] - 12), str(i + 1), fill=cor)
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.marker = None

    # -- execução -----------------------------------------------------------------------------
    def iniciar(self):
        if not self.rota or not self.obst:
            self.carregar()
            if not self.rota or not self.obst:
                messagebox.showerror("Erro", "Carregue o lures.json e o mapa de obstáculos.")
                return
        try:
            hum = int(self.v_hum.get())
            parada = float(self.v_parada.get().replace(",", "."))
            vel = float(self.v_vel.get().replace(",", ".")) if self.v_modo.get() == "sim" else 1.0
        except ValueError:
            messagebox.showerror("Erro", "Valor inválido nas opções.")
            return
        self._cfg_save()
        modo = self.v_modo.get()
        rel = Relogio(vel)
        if modo == "sim":
            inicio = tuple(self.rota[0]["wps"][0]) + (self.rota[0]["z"],)
            mundo = MundoSim(self.obst, inicio, rel)
            teclas = TeclasSim(mundo)
        else:
            mundo = MundoReal(self.regiao)
            if modo == "seco":
                teclas = TeclasSeco()
            else:
                try:
                    teclas = TeclasDirect() if self.v_backend.get() == "pydirectinput" else TeclasPynput()
                except Exception as e:
                    messagebox.showerror("Erro", f"Não consegui iniciar o envio de teclas ({e}).\n"
                                                  "Instale: pip install pydirectinput pynput")
                    return
                if not messagebox.askokcancel(
                        "Modo REAL",
                        "O bot vai apertar WASD no jogo.\n\n"
                        "Depois de OK você tem 5 segundos para clicar na janela do jogo.\n"
                        "Para parar: Ctrl+Alt+X ou o botão PARAR.\n\nContinuar?"):
                    return
        self.mundo = mundo
        self.player = Player(self.obst, self.rota, mundo, teclas, rel, nivel_hum=hum, parada_s=parada,
                             seco=(modo == "seco"), log=self.log, verbose=self.v_verbose.get())
        self.txt.delete("1.0", "end")
        self.rodando = True
        self.btn_ini.config(state="disabled")
        self.btn_stop.config(state="normal")
        mundo.iniciar()
        if modo == "sim" and self.v_monstros.get():
            threading.Thread(target=self._monstros, args=(mundo, rel), daemon=True).start()
        self.thread = threading.Thread(target=self._run, args=(modo, rel), daemon=True)
        self.thread.start()

    def _monstros(self, mundo, rel):
        rng = random.Random(3)
        pts = [p for lu in self.rota for p in lu["pontos"][3:-3]]
        while not self.player.parar.is_set():
            rel.dormir(rng.uniform(12, 25))
            p = mundo.pos()
            cand = [q for q in pts if 3 <= max(abs(q[0] - p[0]), abs(q[1] - p[1])) <= 5]
            if cand:
                q = rng.choice(cand)
                mundo.bloquear(q[0], q[1], rng.uniform(4, 8))
                self.log(f"[sim] monstro parado em {q} por alguns segundos")

    def _run(self, modo, rel):
        if modo == "real":
            self.log("Clique na janela do jogo. Começando em 5 s...")
            for i in range(5, 0, -1):
                if self.player.parar.is_set():
                    break
                time.sleep(1)
        res = "PARADO" if self.player.parar.is_set() else self.player.rodar()
        self.mundo.parar()
        self.player.teclas.soltar_tudo()
        self.log(f"Fim: {res}")
        self.fila_log.put("\0fim")

    def _fim(self):
        self.rodando = False
        self.btn_ini.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.v_status.set("Parado.")

    def parar(self):
        if self.player:
            self.player.parar.set()
            try:
                self.player.teclas.soltar_tudo()
            except Exception:
                pass

    def tick(self):
        try:
            while True:
                msg = self.fila_log.get_nowait()
                if msg == "\0fim":
                    self._fim()
                    continue
                if msg == "\0parar":
                    self.parar()
                    continue
                self.txt.insert("end", msg + "\n")
                self.txt.see("end")
        except queue.Empty:
            pass
        pl, m = self.player, self.mundo
        if pl and m and self.rodando:
            p = m.pos()
            if p:
                x, y = self.px(p[0], p[1])
                if self.marker:
                    self.canvas.coords(self.marker, x - 5, y - 5, x + 5, y + 5)
                else:
                    self.marker = self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#00ff00", outline="white", width=2)
                self.canvas.tag_raise(self.marker)
            e = pl.estado
            self.v_status.set(f"lure {e['lure']} | teclas: {e['teclas'] or '-'} | {e['info']} | "
                              f"voltas {pl.stats['voltas']} | travadas {pl.stats['travadas']}")
        self.root.after(120, self.tick)

    def fechar(self):
        self.parar()
        if self.mundo:
            self.mundo.parar()
        if self.hk:
            try:
                self.hk.stop()
            except Exception:
                pass
        self._cfg_save()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
