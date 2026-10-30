# -*- coding: utf-8 -*-
"""
ALVO DO DITTO: para onde apontar o mouse nos apertos de O.

Em vez de 'N tiles à frente na direção em que o personagem anda' (que manda o Ditto para dentro de uma floresta
quando a rota vai por ali), usa o MAPA DE OBSTÁCULOS e a ROTA do laço (lures.json):
  1) acha em que ponto do lure o personagem está e o SENTIDO da rota (a ordem dos pontos gravados);
  2) se a PARADA do lure está dentro da tela, o alvo é o tile mais aberto (longe de obstáculos) perto dela;
  3) senão, o alvo é o tile mais aberto à frente, perto da rota, dentro da tela (|dx| <= 9, |dy| <= 4 tiles).
Tile desconhecido ou bloqueado no mapa NUNCA é alvo. Se nada servir, cai no ponto da rota ~3 tiles à frente.
"""
import math
import random

import numpy as np
import cv2

JANELA_X, JANELA_Y = 10, 5          # o cursor cai dentro da tela: 10 tiles de cada lado, 5 acima/abaixo (como o usuário faz: o tile MAIS LONGE)
FRENTE_MIN, FRENTE_IDEAL, FRENTE_MAX = 2.0, 10.0, 14.0   # o mais longe possível na direção do laço (o usuário mira o tile a 10)
OLHAR_PONTOS = 12                   # quantos pontos da rota à frente definem o sentido
RAIO_PARADA = 3
CLEARANCE_MAX = 3.0
ETA_S_TILE = 0.5                    # Gogoat: 2 tiles/s
ETA_MAX_S = 6.0                     # mais que isso para entrar na área (ou sem caminho) = 'preso'


JITTER_P = 0.4                      # fração dos lures em que o tile gravado do Ditto varia (humanização)
JITTER_TILES = 1                    # variação máxima (tiles) em x e y
DITTO_FIM_RAIO = 2                  # se o tile gravado não for livre, procura um livre até esse raio
DITTO_FIM_MIN_TR = 3                # nunca mais perto que isso do personagem (cheb)
BOX_OBSTRUIDOS = 8                  # Ditto 'boxeado' = pelo menos tantos dos 8 tiles em volta obstruídos (monstro, obstáculo do mapa ou personagem)
CRUZ_MIN_TR = 3                     # o passo em cruz nunca leva o Ditto a menos que isso do personagem (cheb)
CRUZ = (("norte", 0, -1), ("leste", 1, 0), ("sul", 0, 1), ("oeste", -1, 0))


class AlvoDitto:
    def __init__(self, obst, rota, rng=None):
        """obst: ObstacleMap (editor_mapa). rota: saída de player_lures.montar_rota (lista de lures com 'pontos')."""
        self.obst, self.rota = obst, rota
        walk = (obst.grid == 1).astype(np.uint8)
        self.clear = cv2.distanceTransform(walk, cv2.DIST_L2, 3)      # tiles até o obstáculo/desconhecido mais perto
        self.li, self.idx = None, 0
        self.rng = rng or random.Random()
        self._jit = (None, (0, 0))          # (lure, (jx, jy)) sorteado UMA vez por visita ao lure

    # ---- mapa
    def livre(self, x, y):
        try:
            return bool(self.obst.walkable_game(x, y))
        except Exception:
            return False

    def abertura(self, x, y):
        try:
            c, r = self.obst.tile_of_game(x, y)
        except Exception:
            return 0.0
        if 0 <= r < self.clear.shape[0] and 0 <= c < self.clear.shape[1]:
            return float(self.clear[r, c])
        return 0.0

    # ---- onde estou na rota
    def localizar(self, pos, lure=None):
        """Atualiza (lure, índice do ponto) com continuidade: procura à frente do último índice; se perdeu, global."""
        def melhor(li, ini, fim):
            pts = self.rota[li]["pontos"]
            b = (1e9, ini)
            for k in range(max(0, ini), min(len(pts), fim)):
                d = math.hypot(pts[k][0] - pos[0], pts[k][1] - pos[1])
                if d < b[0]:
                    b = (d, k)
            return b
        if lure is not None:
            self.li = lure
            self.idx = melhor(lure, 0, len(self.rota[lure]["pontos"]))[1]
            return self.li, self.idx
        if self.li is not None:
            d, k = melhor(self.li, self.idx - 2, self.idx + 30)
            if d <= 4:
                self.idx = k
                return self.li, self.idx
        best = (1e9, 0, 0)
        for li in range(len(self.rota)):
            d, k = melhor(li, 0, len(self.rota[li]["pontos"]))
            if d < best[0]:
                best = (d, li, k)
        self.li, self.idx = best[1], best[2]
        return self.li, self.idx

    # ---- alcance dos monstros ao Ditto (movimento simples: se existe caminho por tile livre, eles se aproximam sempre)
    def _bfs(self, ini, raio):
        """Distância (passos, 4 direções) por tiles andáveis a partir de ini; o tile inicial entra sempre."""
        dist, fila = {ini: 0}, [ini]
        for p in fila:
            d = dist[p]
            if d >= raio:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (p[0] + dx, p[1] + dy)
                if q not in dist and self.livre(q[0], q[1]):
                    dist[q] = d + 1
                    fila.append(q)
        return dist

    @staticmethod
    def na_area(m, t):
        dx, dy = abs(m[0] - t[0]), abs(m[1] - t[1])
        return dx <= 3 and dy <= 3 and not (dx == 3 and dy == 3)

    def alcance(self, pos, mons, D):
        """Para cada monstro (tile): (tile, ETA em s, dentro_da_area). ETA = passos até o tile LIVRE mais próximo da área do
        Ditto em D (livre = andável, sem outro monstro, que não seja o do Ditto nem o do personagem) x 0,5 s/tile (Gogoat: 2 tiles/s,
        4 direções). inf = não existe tile para ele chegar. Monstros não atravessam uns aos outros."""
        ocup = set(mons)
        area = [(D[0] + i, D[1] + j) for i in range(-3, 4) for j in range(-3, 4) if not (abs(i) == 3 and abs(j) == 3)]
        livres = {q for q in area if self.livre(q[0], q[1]) and q not in ocup and q != D and q != (pos[0], pos[1])}
        out = []
        for m in mons:
            if self.na_area(m, D):
                out.append((m, 0.0, True))
                continue
            dist, fila, eta = {m: 0}, [m], float("inf")
            for p in fila:
                d = dist[p]
                if p in livres:
                    eta = d * ETA_S_TILE
                    break
                if d >= 12:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (p[0] + dx, p[1] + dy)
                    if q not in dist and q not in ocup and self.livre(q[0], q[1]):
                        dist[q] = d + 1
                        fila.append(q)
            out.append((m, eta, False))
        return out

    def tiles(self, pos, mon_off, d_off):
        M = [(pos[0] + round(dx), pos[1] + round(dy)) for dx, dy in mon_off]
        D = (pos[0] + round(d_off[0]), pos[1] + round(d_off[1])) if d_off else None
        return M, D

    def problemas(self, pos, mon_off, d_off, eta_max=ETA_MAX_S):
        """Tiles dos monstros fora da área do Ditto que NÃO chegam (sem tile livre) ou demoram mais que eta_max."""
        M, D = self.tiles(pos, mon_off, d_off)
        if D is None:
            return None
        return frozenset(m for m, eta, dentro in self.alcance(pos, M, D) if not dentro and eta > eta_max)

    # ---- lógica de lure: o Ditto 'come tiles' andando 1 tile NO SENTIDO DO LURE (monstros chegam por trás e fecham de perto para longe)
    ANEL = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))

    @staticmethod
    def area_de(T):
        return [(T[0] + i, T[1] + j) for i in range(-3, 4) for j in range(-3, 4) if not (abs(i) == 3 and abs(j) == 3)]

    def capacidade(self, pos, T):
        """Quantos monstros cabem na área do Ditto em T: tiles andáveis da área (sem contar o tile do Ditto nem o do personagem)."""
        return sum(1 for q in self.area_de(T) if q != T and q != (pos[0], pos[1]) and self.livre(q[0], q[1]))

    def direcao_lure(self):
        """Passo unitário (sx, sy) do sentido em que o lure está indo no FIM da rota (os últimos ~12 pontos gravados)."""
        if self.li is None:
            return None
        pts = self.rota[self.li]["pontos"]
        a = pts[max(0, len(pts) - 13)]
        b = pts[-1]
        vx, vy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(vx, vy)
        if n < 2:
            return None
        ux, uy = vx / n, vy / n
        sx = 0 if abs(ux) < 0.38 else (1 if ux > 0 else -1)
        sy = 0 if abs(uy) < 0.38 else (1 if uy > 0 else -1)
        return (sx, sy) if (sx or sy) else None

    def cobertura(self, M, T):
        return sum(1 for m in M if self.na_area(m, T))

    # ---- PASSO EM CRUZ (1 tile): só quando o Ditto está boxeado e há monstro fora da área
    def obstruidos(self, pos, M, D):
        """Quantos dos 8 tiles em volta do Ditto estão obstruídos: monstro, obstáculo/parede do mapa ou o personagem."""
        ocup = set(M)
        ocup.add((pos[0], pos[1]))
        return sum(1 for dx, dy in self.ANEL if (D[0] + dx, D[1] + dy) in ocup or not self.livre(D[0] + dx, D[1] + dy))

    def passo_cruz(self, pos, mon_off, d_off, n_total):
        """Anda o Ditto 1 TILE (norte/leste/sul/oeste a partir do centro dele). Só se estiver boxeado (>= BOX_OBSTRUIDOS de 8 em volta
        obstruídos) e houver monstro fora da área. O tile de destino precisa ser andável no mapa (monstro nele pode: o Ditto o empurra).
        Escolhe o sentido que mantém/aumenta a cobertura, libera mais dos 3 tiles de trás (o antigo e os 2 laterais) e fica mais perto
        dos que estão fora. -> (tx, ty relativos ao personagem, info) ou (None, None, motivo)."""
        M, D = self.tiles(pos, mon_off, d_off)
        if D is None or len(M) < 2:
            return None, None, "Ditto não localizado"
        obs = self.obstruidos(pos, M, D)
        if obs < BOX_OBSTRUIDOS:
            return None, None, f"Ditto não está boxeado ({obs}/8 obstruídos)"
        fora = [m for m in M if not self.na_area(m, D)]
        if not fora:
            return None, None, "todos os localizados já estão na área"
        cov0 = self.cobertura(M, D)
        d_tr0 = max(abs(D[0] - pos[0]), abs(D[1] - pos[1]))
        dist_fora0 = sum(max(abs(m[0] - D[0]), abs(m[1] - D[1])) for m in fora) / len(fora)
        melhor = None
        for nome, dx, dy in CRUZ:
            N = (D[0] + dx, D[1] + dy)
            if not self.livre(N[0], N[1]) or N == (pos[0], pos[1]):
                continue                                     # parede/obstáculo/personagem: não anda para lá
            tx, ty = N[0] - pos[0], N[1] - pos[1]
            if abs(tx) > JANELA_X or abs(ty) > JANELA_Y:
                continue
            if max(abs(tx), abs(ty)) < min(d_tr0, CRUZ_MIN_TR):
                continue                                     # não chega perto do personagem
            cov1 = self.cobertura(M, N)
            if cov1 < cov0:
                continue                                     # não pode perder quem já está na área
            px, py = abs(dy), abs(dx)                        # lateral do sentido
            atras = [D, (D[0] + px, D[1] + py), (D[0] - px, D[1] - py)]
            livres_atras = sum(1 for q in atras if self.livre(q[0], q[1]))
            dist_fora = sum(max(abs(m[0] - N[0]), abs(m[1] - N[1])) for m in fora) / len(fora)
            if dist_fora > dist_fora0 + 1e-9:
                continue                                     # não se afasta de quem está fora da área
            score = cov1 * 10 + livres_atras * 2 - dist_fora * 0.5
            if melhor is None or score > melhor[0]:
                melhor = (score, nome, N, tx, ty, cov1, livres_atras)
        if melhor is None:
            return None, None, "nenhum sentido em cruz é seguro (parede, perde cobertura, afasta do grupo ou chega perto do personagem)"
        _, nome, N, tx, ty, cov1, la = melhor
        nao_loc = max(0, n_total - len(M))
        return tx, ty, (f"1 tile para {nome}: Ditto ({D[0]},{D[1]}) -> ({N[0]},{N[1]}), boxeado {obs}/8, cobertura {cov0} -> {cov1} de {len(M)} "
                        f"localizados, {len(fora)} fora, {la}/3 tiles de trás livres" + (f"; {nao_loc} da lista não localizados" if nao_loc else ""))

    def mergulho(self, pos, mon_off, d_off, n_total):
        """SOLUÇÃO 1: o Ditto vai para DENTRO do grupo, no tile que deixa MAIS monstros na área do Whirlwind (idealmente todos), e o R
        sai logo depois (o Ditto pode ficar dentro de monstros por um instante curto). Só tiles andáveis e abertos, alcançáveis pelo
        Ditto (monstro no tile pode: é a passiva dele), até 8 tiles, dentro da tela, nunca mais perto do treinador que 4 tiles.
        -> (tx, ty, passos, info) ou (None, motivo)."""
        M, D = self.tiles(pos, mon_off, d_off)
        if D is None or len(M) < 2:
            return None, "Ditto não localizado"
        alc = self._bfs(D, 12)
        base = self.cobertura(M, D)
        d_tr0 = max(abs(D[0] - pos[0]), abs(D[1] - pos[1]))
        melhor = None
        for dx in range(-8, 9):
            for dy in range(-8, 9):
                T = (D[0] + dx, D[1] + dy)
                if T == D or T not in alc or self.abertura(T[0], T[1]) < 1.0:
                    continue
                tx, ty = T[0] - pos[0], T[1] - pos[1]
                if abs(tx) > JANELA_X or abs(ty) > JANELA_Y:
                    continue
                if max(abs(tx), abs(ty)) < min(d_tr0, 4):
                    continue                                   # não traz o grupo para cima do treinador
                cov = self.cobertura(M, T)
                if cov <= base:
                    continue
                cap = self.capacidade(pos, T)
                perde = sum(1 for m in M if self.na_area(m, D) and not self.na_area(m, T))     # os que já estavam dentro e SAIRIAM da área
                score = -cov * 10 + perde * 6 + alc[T] * 0.8 + max(0, n_total + 1 - cap) * 1.5
                if melhor is None or score < melhor[0]:
                    melhor = (score, T, tx, ty, cov, alc[T], cap, perde)
        if melhor is None:
            return None, f"nenhum tile cobre mais que os {base}/{len(M)} de agora"
        _, T, tx, ty, cov, passos, cap, perde = melhor
        nao_loc = max(0, n_total - len(M))
        return tx, ty, passos, (f"MERGULHO no grupo: Ditto ({D[0]},{D[1]}) -> ({T[0]},{T[1]}) ({passos} tiles): cobertura {base} -> {cov} "
                                f"de {len(M)} localizados, perde {perde} (capacidade {cap})" + (f"; {nao_loc} da lista não localizados" if nao_loc else ""))

    def retirada(self, pos, mon_off, d_off, n_total, exigir_cap=None):
        """SOLUÇÃO 2: o Ditto SE AFASTA do grupo, 5 a 10 tiles, para o tile mais ABERTO do mapa (floresta/obstáculo nunca fecha todos),
        do lado oposto ao dos monstros, e o grupo vem atrás e se refaz em terreno aberto. Nunca mais perto do treinador que 4 tiles.
        -> (tx, ty, passos, info) ou (None, motivo)."""
        M, D = self.tiles(pos, mon_off, d_off)
        if D is None or not M:
            return None, "Ditto não localizado"
        cx = sum(m[0] for m in M) / len(M)
        cy = sum(m[1] for m in M) / len(M)
        ax, ay = D[0] - cx, D[1] - cy
        an = math.hypot(ax, ay) or 1.0
        alc = self._bfs(D, 14)
        d_tr0 = max(abs(D[0] - pos[0]), abs(D[1] - pos[1]))
        melhor = None
        for dx in range(-JANELA_X, JANELA_X + 1):
            for dy in range(-JANELA_Y, JANELA_Y + 1):
                T = (pos[0] + dx, pos[1] + dy)
                passos = alc.get(T)
                d_ditto = max(abs(T[0] - D[0]), abs(T[1] - D[1]))
                if passos is None or d_ditto < 5 or d_ditto > 10:
                    continue
                ab = self.abertura(T[0], T[1])
                if ab < 2.0 or max(abs(dx), abs(dy)) < min(d_tr0, 4):
                    continue
                cap = self.capacidade(pos, T)
                if exigir_cap and cap < exigir_cap:
                    continue
                afasta = ((T[0] - D[0]) * ax + (T[1] - D[1]) * ay) / (an * max(d_ditto, 1))      # +1 = direto para longe do grupo
                score = min(ab, CLEARANCE_MAX) + 1.2 * afasta - 0.1 * passos + 0.05 * min(cap, 40)
                if melhor is None or score > melhor[0]:
                    melhor = (score, T, dx, dy, passos, ab, cap)
        if melhor is None:
            return None, "nenhum tile aberto e longe o bastante dentro da tela"
        _, T, tx, ty, passos, ab, cap = melhor
        return tx, ty, passos, (f"RETIRADA para o aberto: Ditto ({D[0]},{D[1]}) -> ({T[0]},{T[1]}) ({passos} tiles), abertura {ab:.1f}, "
                                f"capacidade {cap}, para longe do grupo")

    def ajuste_lure(self, pos, mon_off, d_off, n_total, ordem=("mergulho", "retirada")):
        """Tenta as soluções na ordem dada. -> (tx, ty, passos, info, tipo) ou (None, motivo, None)."""
        motivos = []
        for tipo in ordem:
            r = self.mergulho(pos, mon_off, d_off, n_total) if tipo == "mergulho" else self.retirada(pos, mon_off, d_off, n_total)
            if r[0] is not None:
                return r + (tipo,)
            motivos.append(f"{tipo}: {r[1]}")
        return None, "; ".join(motivos), None

    def iniciar_lure(self, pos):
        """Depois do revive: o próximo lure é o que COMEÇA mais perto de onde o personagem está (<= 8 tiles)."""
        best = (1e9, 0)
        for li, lu in enumerate(self.rota):
            p0 = lu["pontos"][0]
            d = math.hypot(p0[0] - pos[0], p0[1] - pos[1])
            if d < best[0]:
                best = (d, li)
        if best[0] <= 8:
            self.li, self.idx = best[1], 0
        else:
            self.li = None              # perdido: localizar() procura em todos os lures
        return self.li

    # ---- alvo gravado: onde VOCÊ costuma deixar o Ditto no fim do lure (ditto_fim do lures.json)
    def _alvo_gravado(self, pos, li, i, pts, fd):
        tx, ty = fd["tile"]
        dx0, dy0 = tx - pos[0], ty - pos[1]
        if abs(dx0) > JANELA_X or abs(dy0) > JANELA_Y:
            return None                                     # ainda longe: segue a regra normal (à frente na rota)
        if self._jit[0] != li:                              # nova visita a este lure: sorteia a variação humana
            j = (0, 0)
            if self.rng.random() < JITTER_P:
                j = (self.rng.randint(-JITTER_TILES, JITTER_TILES), self.rng.randint(-JITTER_TILES, JITTER_TILES))
            self._jit = (li, j)
        jx, jy = self._jit[1]
        ax, ay = tx + jx, ty + jy
        melhor = None
        for x in range(ax - DITTO_FIM_RAIO, ax + DITTO_FIM_RAIO + 1):
            for y in range(ay - DITTO_FIM_RAIO, ay + DITTO_FIM_RAIO + 1):
                dx, dy = x - pos[0], y - pos[1]
                if abs(dx) > JANELA_X or abs(dy) > JANELA_Y or not self.livre(x, y):
                    continue
                perto = max(abs(dx), abs(dy)) < DITTO_FIM_MIN_TR
                score = -math.hypot(x - ax, y - ay) + 0.3 * min(self.abertura(x, y), CLEARANCE_MAX) - (5.0 if perto else 0.0)
                if melhor is None or score > melhor[0]:
                    melhor = (score, dx, dy)
        if melhor is None:
            return None
        _, dx, dy = melhor
        var = f" variação humana ({jx:+d},{jy:+d})" if (jx or jy) else ""
        return dx, dy, (f"lure {li + 1}, ponto {i}/{len(pts)}: GRAVADO (ditto_fim {tx},{ty}{var}), alvo "
                        f"({pos[0] + dx},{pos[1] + dy}) = ({dx:+d},{dy:+d}) a {math.hypot(dx, dy):.1f} tiles do personagem")

    # ---- alvo
    def alvo(self, pos, lure=None, n_mon=0):
        """-> (tx, ty, info): deslocamento do alvo em tiles em relação ao personagem + texto para o log."""
        li, i = self.localizar(pos, lure)
        pts = self.rota[li]["pontos"]
        fd = self.rota[li].get("ditto_fim")
        if fd and fd.get("tile"):
            r = self._alvo_gravado(pos, li, i, pts, fd)
            if r:
                return r
        fim = pts[-1]
        j = min(len(pts) - 1, i + OLHAR_PONTOS)
        vx, vy = pts[j][0] - pos[0], pts[j][1] - pos[1]
        n = math.hypot(vx, vy)
        if n < 1:                                  # no fim da rota: sentido = última direção gravada
            a = pts[max(0, len(pts) - 1 - OLHAR_PONTOS)]
            vx, vy = fim[0] - a[0], fim[1] - a[1]
            n = math.hypot(vx, vy)
        ux, uy = (vx / n, vy / n) if n > 0 else (0.0, 0.0)
        dx_p, dy_p = fim[0] - pos[0], fim[1] - pos[1]
        parada_na_tela = abs(dx_p) <= JANELA_X and abs(dy_p) <= JANELA_Y
        ahead = pts[i + 2:i + 24]
        melhor = None
        for dx in range(-JANELA_X, JANELA_X + 1):
            for dy in range(-JANELA_Y, JANELA_Y + 1):
                x, y = pos[0] + dx, pos[1] + dy
                if not self.livre(x, y):
                    continue
                proj = dx * ux + dy * uy
                ab = min(self.abertura(x, y), CLEARANCE_MAX)
                if parada_na_tela:
                    d_par = max(abs(x - fim[0]), abs(y - fim[1]))
                    if d_par > RAIO_PARADA:
                        continue
                    score = ab * 1.0 - 0.35 * d_par
                    if n_mon:                              # o Ditto tem que ter tiles para TODOS (capacidade) e ficar longe do treinador
                        score -= 0.7 * max(0, (n_mon + 1) - self.capacidade(pos, (x, y)))
                    d_tr = max(abs(dx), abs(dy))
                    if d_tr < 3:
                        score -= 3.0
                    elif d_tr > 8:
                        score -= 0.5 * (d_tr - 8)
                else:
                    if proj < FRENTE_MIN or proj > FRENTE_MAX:
                        continue
                    d_rota = min((math.hypot(x - p[0], y - p[1]) for p in ahead), default=0.0)
                    score = ab * 1.0 - 0.45 * d_rota - 0.22 * abs(proj - FRENTE_IDEAL)
                if melhor is None or score > melhor[0]:
                    melhor = (score, dx, dy, ab)
        if melhor is None:                          # nada aberto: um ponto da rota à frente (ela é andável)
            k = min(len(pts) - 1, i + 3)
            dx, dy = pts[k][0] - pos[0], pts[k][1] - pos[1]
            dx, dy = max(-JANELA_X, min(JANELA_X, dx)), max(-JANELA_Y, min(JANELA_Y, dy))
            return dx, dy, f"lure {li + 1}, ponto {i}/{len(pts)}: sem tile aberto; ponto da rota"
        _, dx, dy, ab = melhor
        modo = "perto da PARADA" if parada_na_tela else "o mais longe à frente na rota"
        return dx, dy, (f"lure {li + 1}, ponto {i}/{len(pts)}: {modo}, alvo ({pos[0] + dx},{pos[1] + dy}) "
                        f"= ({dx:+d},{dy:+d}) abertura {ab:.1f}")
