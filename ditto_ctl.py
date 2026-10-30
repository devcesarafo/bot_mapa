# -*- coding: utf-8 -*-
"""
DITTO CTL (fase 4): move (`o`) e para (`p`) o Ditto no ritmo medido na volta do usuário (analise_ditto.json de 24/10).

PADRÃO IMPLEMENTADO (medido, 5 lures; a regra do teleporte foi explicada pelo usuário):
  1) Depois do revive de FIM DE LURE (<= 2 monstros vivos quando o Ditto volta): P em ~0,8 s, assim que o personagem
     começa a andar. O Ditto fica parado no ponto do revive.
  2) Quando o personagem PASSA de 9 tiles na horizontal ou 4 na vertical do ponto do P (|dx| >= 10 ou |dy| >= 5), o Ditto
     sai da visão e é TELEPORTADO para o personagem. O bot ESPERA o teleporte (Ditto visto a até 2,6 tiles do personagem;
     se não for visto, assume após 1,2 s): mandar o O antes faria o Ditto andar e atrasaria/impediria o teleporte.
     Só então começa a rajada de O (~4,5 Hz, intervalo 0,21 s), mirando o ALVO escolhido por alvo_ditto.py (tile aberto
     do mapa de obstáculos, à frente na rota ou perto da parada), de modo que o Ditto passe à frente do personagem.
  3) O personagem para (coordenada parada >= 0,35 s): continua o O por 0,4 s e aperta P com 1,6 s de parada
     (o usuário usou 0,7 a 2,5 s, mediana 1,1 s; 1,6 s evita P em pausas curtas de 1 a 1,5 s no meio da rota).
Roda em thread própria (20 Hz) lendo só a coordenada: o O precisa de 4,5 Hz, mais rápido que a leitura de combate.
Sem confirmação visual das teclas (não há sinal confiável): as mensagens do log são o que o bot ENVIOU.
"""
import math
import random
from collections import deque

TILE = 72.3
CENTRO_JOGO = (960.0, 490.0)
AREA_MOUSE = (215, 108, 1705, 872)          # o cursor nunca sai daqui
DIST_MOUSE_TILES = 3.5
O_INTERVALO_S = 0.21
P_APOS_REVIVE_S = 0.8
ESPERA_P_MAX_S = 5.0
SAI_X, SAI_Y = 10, 5                      # |dx| >= 10 ou |dy| >= 5 = passou de 9 / 4 tiles: Ditto fora da visão
TELEPORTE_MIN_S = 0.15
TELEPORTE_MAX_S = 1.2
RAIO_PERTO_TILES = 2.6
CROP_BOX = (700, 230, 1220, 750)         # região em volta do personagem (para ver o Ditto aparecer)
LOG_ALVO_S = 3.0
REPOS_MAX_CICLO = 4                    # ajustes do Ditto por lure (movimento DELICADO: o Ditto entra em monstros e eles saem do tile)
REPOS_VEL_TILES_S = 4.0                # velocidade assumida do Ditto (com o buff G ele anda mais que o personagem)
REPOS_ENTRE_S = 2.5                    # mínimo entre dois ajustes (para ver o efeito)
REPOS_O_INTERVALO_S = 0.25
SETTLE_P_S = 0.5                       # depois de um P o Ditto precisa de um tempo parado antes do R
CRUZ_ENTRE_S = 1.6                     # passo em cruz (1 tile): mínimo entre dois passos
CRUZ_MAX_CICLO = 8                     # passos em cruz por lure
CRUZ_DUR_S = 0.6                       # duração do passo de 1 tile (2 O + P)
VER_MAX_S = 2.0                        # depois de um ajuste: espera ver o Ditto de novo (no máx.) antes de liberar o R
PARADO_S = 0.35
O_APOS_PARAR_S = 0.4
P_APOS_PARAR_S = 1.6
CICLO_MAX_MON = 2


class DittoCtl:
    def __init__(self, tecla, mouse, log, humanizar=True, alvo=None, ver_perto=None):
        self.tecla, self.mouse, self.log, self.hum = tecla, mouse, log, humanizar
        self.alvo, self.ver_perto = alvo, ver_perto     # AlvoDitto (mapa+rota) e função () -> bool (Ditto perto do personagem)
        self.t_cross, self.t_log_alvo, self.hp, self.n_mon = 0.0, -99.0, None, 0
        self.estado = "idle"
        self.hist = deque()                 # (t, x, y)
        self.ult_mudanca = 0.0
        self.ult_coord = None
        self.t_rev = 0.0
        self.ponto_P = None
        self.prox_O = 0.0
        self.t_parou = None
        self.rumo = None                    # último rumo conhecido (vetor unitário)
        self.ok_ate = -1.0                  # a thread principal renova: leitura de combate válida
        self.n_O = 0
        self.pedido, self.repos_count, self.t_repos, self.t_fim_repos = None, 0, 0.0, -99.0
        self.t_ult_P, self.t_visto, self.precisa_ver, self.esquecer = -99.0, -99.0, False, False
        self.pausado = False                                # fuga: o personagem foge e o Ditto NÃO recebe O/P
        self.verif_cruz, self.seg_R_ate = False, -1.0     # passo em cruz: conferir monstro na área depois / segurar o R 1 s

    # ---- chamado pela thread principal
    def renovar(self, t, ok, hp=None, n_mon=None):
        if ok:
            self.ok_ate = t + 1.5
        self.hp = hp
        self.n_mon = n_mon

    def revive_voltou(self, t, n_mon):
        if t - self.t_rev < 3.0 and self.estado in ("espera_P", "seguro"):
            return                                  # o Ditto piscou na Battle list: não é outro revive
        if n_mon <= CICLO_MAX_MON:
            self.estado, self.t_rev, self.ponto_P = "espera_P", t, None
            self.repos_count, self.pedido = 0, None
            self.verif_cruz, self.seg_R_ate = False, -1.0
            if self.alvo is not None and self.ult_coord is not None:
                self.alvo.iniciar_lure(self.ult_coord)
            self.log(f"[ditto] Ditto voltou do revive ({n_mon} monstro(s)): P assim que o personagem andar.")
        else:
            self.estado = "idle"
            self.log(f"[ditto] revive com {n_mon} monstros vivos (luta): não mexo no Ditto.")

    def pode_reposicionar(self, t, cruz=False):
        """Ajuste possível no 'seguro' ou JÁ no 'parando' (0,6 s depois de parar): o ajuste substitui o P final, ganhando tempo."""
        ok_estado = self.estado == "seguro" or (self.estado == "parando" and t - self.t_parou >= 0.6)
        max_c, entre = (CRUZ_MAX_CICLO, CRUZ_ENTRE_S) if cruz else (REPOS_MAX_CICLO, REPOS_ENTRE_S)
        return (ok_estado and self.pedido is None and self.repos_count < max_c
                and t - self.t_fim_repos > entre and self.ult_coord is not None)

    def pedir_reposicionar(self, t, tx, ty, dist_tiles, info, cruz=False):
        """Ajuste DELICADO: 2 apertos de O (3 se > 2 tiles), 0,3 s entre eles, e P assim que o Ditto deve ter chegado.
        cruz=True: passo de 1 tile (2 O, P em 0,6 s) e, quando terminar, o bot confere se há monstro dentro da área."""
        n_o = 2 if dist_tiles <= 2 else (3 if dist_tiles <= 5 else 5)     # distâncias longas (retirada): mais apertos
        dur = CRUZ_DUR_S if cruz else max(0.7, dist_tiles / REPOS_VEL_TILES_S + 0.4)
        self.pedido = {"tx": tx, "ty": ty, "dur": dur, "n_o": n_o, "t": t, "cruz": cruz}
        self.esquecer = True
        self.log(f"[ditto] {'PASSO' if cruz else 'AJUSTE'} ({self.repos_count + 1}/{CRUZ_MAX_CICLO if cruz else REPOS_MAX_CICLO}): {info}")

    def isca(self, tx, ty, n=3, intervalo=0.25):
        """FUGA: manda o Ditto para o tile (tx, ty) relativo ao personagem (lado OPOSTO ao da fuga): os monstros correm para ele.
        Roda numa thread própria (n apertos de O com o mouse no tile); funciona mesmo com o ctl pausado."""
        import threading as _th
        import time as _tm

        def _go():
            px = min(max(CENTRO_JOGO[0] + tx * TILE, AREA_MOUSE[0]), AREA_MOUSE[2])
            py = min(max(CENTRO_JOGO[1] + ty * TILE, AREA_MOUSE[1]), AREA_MOUSE[3])
            self.mouse((int(px), int(py)))
            for _ in range(n):
                self.tecla("o")
                _tm.sleep(intervalo)
            self.log(f"[ditto] ISCA: Ditto mandado para ({tx:+d},{ty:+d}) do personagem (lado oposto da fuga).")
        _th.Thread(target=_go, daemon=True).start()

    def segurar_R(self, t, s):
        self.seg_R_ate = max(self.seg_R_ate, t + s)

    def parado_ha(self, t):
        """Há quantos segundos a coordenada do personagem não muda."""
        return t - self.ult_mudanca if self.ult_coord is not None else 0.0

    def visto_ditto(self, t):
        self.t_visto = t

    def consumir_esquecer(self):
        e, self.esquecer = self.esquecer, False
        return e

    def bloqueia_R(self, t):
        """O R NÃO pode sair com o Ditto andando, recém-parado (a posição dele ainda não vale) ou sem ter sido visto depois de um ajuste."""
        if self.estado in ("mover", "parando", "repos"):
            return "Ditto em movimento"
        if t - self.t_ult_P < SETTLE_P_S:
            return "Ditto acabou de parar"
        if self.precisa_ver:
            if self.t_visto > self.t_ult_P + 0.3:
                self.precisa_ver = False
            elif t - self.t_ult_P <= VER_MAX_S:
                return "esperando ver o Ditto na posição nova"
            else:
                self.precisa_ver = False
        if t < self.seg_R_ate:                          # NÃO é "em movimento": o R de emergência passa por cima desta espera
            return "aguardando 1 s depois do passo (monstro dentro da área)"
        return None

    def desligar(self):
        self.estado = "idle"

    # ---- helpers
    def _rumo(self, t):
        """Vetor unitário do deslocamento na última 1 s; se parado/sem dado, o último rumo."""
        ref = next(((tt, x, y) for tt, x, y in self.hist if t - tt <= 1.0), None)
        if ref and self.ult_coord:
            dx, dy = self.ult_coord[0] - ref[1], self.ult_coord[1] - ref[2]
            n = math.hypot(dx, dy)
            if n >= 1.0:
                self.rumo = (dx / n, dy / n)
        return self.rumo

    def _enviar_O(self, t):
        tx = ty = None
        if self.alvo is not None and self.ult_coord is not None:
            try:
                tx, ty, info = self.alvo.alvo(self.ult_coord, n_mon=self.n_mon or 0)
                if t - self.t_log_alvo >= LOG_ALVO_S:
                    self.t_log_alvo = t
                    self.log(f"[ditto] alvo do O: {info}")
            except Exception as e:
                self.log(f"[ditto] alvo por mapa falhou ({e}); usando o rumo do personagem")
        if tx is None:                                      # sem mapa/rota: N tiles à frente do rumo
            r = self._rumo(t)
            if r is None:
                return False
            tx, ty = round(r[0] * DIST_MOUSE_TILES), round(r[1] * DIST_MOUSE_TILES)
        px, py = CENTRO_JOGO[0] + tx * TILE, CENTRO_JOGO[1] + ty * TILE
        if self.hum:
            px += random.uniform(-0.25, 0.25) * TILE
            py += random.uniform(-0.25, 0.25) * TILE
        px = min(max(px, AREA_MOUSE[0]), AREA_MOUSE[2])
        py = min(max(py, AREA_MOUSE[1]), AREA_MOUSE[3])
        self.mouse((int(px), int(py)))
        self.tecla("o")
        self.n_O += 1
        self.prox_O = t + O_INTERVALO_S * (random.uniform(0.85, 1.35) if self.hum else 1.0)
        return True

    def _P(self, t, motivo):
        self.tecla("p")
        self.t_ult_P = t
        self.ponto_P = self.ult_coord
        self.log(f"[ditto] P ('p'): {motivo}")

    # ---- thread do Ditto (20 Hz)
    def passo(self, t, coord):
        if coord is None:
            return
        c = (coord[0], coord[1])
        if c != self.ult_coord:
            self.ult_mudanca = t
        self.ult_coord = c
        self.hist.append((t, c[0], c[1]))
        while self.hist and t - self.hist[0][0] > 2.0:
            self.hist.popleft()
        if self.pausado or self.estado == "idle" or t > self.ok_ate:
            return
        parado = t - self.ult_mudanca >= PARADO_S
        if self.estado == "espera_P":
            if t - self.t_rev > ESPERA_P_MAX_S:
                self.estado = "idle"
                self.log("[ditto] personagem não saiu depois do revive: ciclo cancelado (sem P).")
                return
            ref = self.hist[0]
            if t - self.t_rev >= P_APOS_REVIVE_S and not parado and math.hypot(c[0] - ref[1], c[1] - ref[2]) >= 1:
                self._P(t, f"{t - self.t_rev:.1f} s depois do revive, personagem saindo (Ditto fica no ponto do revive)")
                self.estado = "seguro"
        elif self.estado == "repos":
            p = self.pedido
            if not parado or (self.n_O >= p["n_o"] and t - self.t_repos >= p["dur"]):
                self._P(t, f"Ditto posicionado ({self.n_O} O em {t - self.t_repos:.1f} s)")
                self.estado, self.pedido, self.t_fim_repos = "seguro", None, t
                self.repos_count += 1
                self.precisa_ver, self.esquecer = True, True
                self.verif_cruz = bool(p.get("cruz"))
            elif t >= self.prox_O and self.n_O < p["n_o"]:
                px, py = CENTRO_JOGO[0] + p["tx"] * TILE, CENTRO_JOGO[1] + p["ty"] * TILE
                self.mouse((int(min(max(px, AREA_MOUSE[0]), AREA_MOUSE[2])), int(min(max(py, AREA_MOUSE[1]), AREA_MOUSE[3]))))
                self.tecla("o")
                self.n_O += 1
                self.prox_O = t + REPOS_O_INTERVALO_S
        elif self.estado == "seguro":
            if self.pedido and parado:
                self.estado, self.t_repos, self.prox_O, self.n_O = "repos", t, t, 0
                return
            if self.pedido and t - self.pedido["t"] > 4.0:
                self.pedido = None                    # o personagem não parou a tempo: pedido vencido
            if self.ponto_P and (abs(c[0] - self.ponto_P[0]) >= SAI_X or abs(c[1] - self.ponto_P[1]) >= SAI_Y):
                self.estado, self.t_cross = "teleporte", t
                self.log(f"[ditto] personagem passou do limite (dx={c[0] - self.ponto_P[0]}, dy={c[1] - self.ponto_P[1]}): "
                         "Ditto fora da visão; aguardando o TELEPORTE (sem O até lá)")
        elif self.estado == "teleporte":
            visto = t - self.t_cross >= TELEPORTE_MIN_S and self.ver_perto is not None and self.ver_perto()
            if visto or t - self.t_cross >= TELEPORTE_MAX_S:
                self.estado, self.prox_O, self.n_O = "mover", t, 0
                self.log(f"[ditto] teleporte {'visto' if visto else 'NÃO visto (assumido)'} {t - self.t_cross:.2f} s depois: "
                         "rajada de O (4,5 Hz)")
        elif self.estado == "mover":
            if parado:
                self.estado, self.t_parou = "parando", self.ult_mudanca
            elif t >= self.prox_O:
                self._enviar_O(t)
        elif self.estado == "parando":
            if self.pedido and parado and t - self.t_parou >= 0.6:
                self.estado, self.t_repos, self.prox_O, self.n_O = "repos", t, t, 0
                return
            if not parado:
                self.estado = "mover"                       # só uma pausa: segue
            elif t - self.t_parou < O_APOS_PARAR_S:
                if t >= self.prox_O:
                    self._enviar_O(t)
            elif t - self.t_parou >= P_APOS_PARAR_S:
                self.log(f"[ditto] rajada de O terminou ({self.n_O} apertos).")
                self._P(t, f"personagem parado há {t - self.t_parou:.1f} s")
                self.estado = "seguro"


def ditto_perto(crop, hp=None, raio_tiles=RAIO_PERTO_TILES):
    """True se há uma barra de vida SEM caveira (o Ditto) a até raio_tiles do personagem (fora da barra dele mesmo).
    crop = frame[CROP_BOX]. Um Ditto exatamente em cima do personagem não é visto (barras sobrepostas)."""
    from detector_stun import achar_barras, _mapa_caveira, _tem_caveira_acima, BARRA_LARGURA
    h, w = crop.shape[:2]
    area = (0, 0, w, h)
    cx, cy = 959 - CROP_BOX[0], 452 - CROP_BOX[1]
    mapa = None
    for bx, by, bw in achar_barras(crop, area):
        if bw < 6 or (hp is not None and abs(bw - BARRA_LARGURA * hp) > 4.5):
            continue
        d = math.hypot(bx - cx, by - cy)
        if d <= 22 or d > raio_tiles * TILE:
            continue
        if mapa is None:
            mapa = _mapa_caveira(crop, area)
        if not _tem_caveira_acima(mapa, bx, by):
            return True
    return False
