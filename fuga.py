# -*- coding: utf-8 -*-
"""
FUGA (fase 5, v4). Só AGE com agir=True (flag --fuga do bot, sem --seco). Sem isso, só escreve no log o que faria.

SITUAÇÕES
  1) SOBRA: monstro vivo SEM stun e o Ditto sem skill de área para matá-lo (F2/F3/F4 em recarga) por >= STALL_S. Foge, sai da tela
     dos monstros e bate o revive.
  2) (ainda não implementada: Ditto perdendo vida rápido com o lure em andamento e o C já usado.)
  3) DITTO MORTO (prioridade; vale sempre). Sumiu da Battle list e o revive NÃO foi apertado até REVIVE_JANELA_S antes de ele sumir
     (o revive, do bot ou seu, também faz o Ditto sumir: a tecla 'e' vista há <= 1 s explica o sumiço). Então morreu: REVIVE NA HORA
     (sem esperar nada) e, se há monstro na tela e o Player está parado na parada, foge para o lado oposto dos monstros.

COMO FOGE
  * SÓ EM CRUZ (a diagonal é mais lenta e deixa o monstro cortar caminho). Modelo de perseguição + espaço aberto + rota gravada.
  * SÓ POR ONDE VOCÊ ANDOU NOS ÚLTIMOS ~52 s (TRILHA): os monstros de um lugar respawnam em ~1 min. Perto do limite, REVIVE JÁ, mesmo com
    monstro na tela (é melhor arriscar onde há poucos do que onde há vários).
  * ISCA: Ditto VIVO é mandado para o lado oposto ao da fuga.
QUANDO BATE O REVIVE (situação 1; na 3 é na hora)
  Battle list vazia há >= REVIVE_VAZIO_S  E  >= REVIVE_FINALIZA_S de corrida RETA pela frente (sem virada)  E, se houve virada, a ESQUINA
  fora da tela (os monstros que sobem pela reta anterior só aparecem quando chegam nela). Senão corre mais (conclui a virada) e reavalia.
  O revive leva ~0,5 s para terminar: a fuga só PARA quando o Ditto reaparece e passam REVIVE_FINALIZA_S. Limite de 20 s.
Limite: plano = foto do quadro; VEL_REL_MONSTRO e a velocidade V_PADRAO são suposições a medir no jogo.
"""
import heapq
import math
from collections import deque

LEITURAS = 2
STALL_S = 4.0                  # situação 1
REVIVE_JANELA_S = 1.0          # 'e' apertado até isso antes do Ditto sumir = foi o revive; senão = morreu
MORTE_DEBOUNCE_S = 0.25        # ausente por isso antes de concluir morte (o vigia lê a ~15 Hz)
REVIVE_RETRY_S = 1.5
REVIVE_TENT_MAX = 4
REVIVE_VAZIO_S = 0.5           # lista sem monstro há isso para poder reviver (situação 1)
REVIVE_FINALIZA_S = 0.5        # o revive leva isso para terminar (Ditto 100%)
V_PADRAO = 3.0                 # tiles/s do personagem correndo se ainda não deu para medir
FUGA_MAX_S = 20.0
REPLANS_MAX = 12
EXT_ANTES = 6                  # passos que faltam no caminho quando já planeja a continuação (a fuga não pode parar no fim do caminho)
COOLDOWN_S = 8.0
COOLDOWN_ALARME_S = 40.0
LIMITE_RESPAWN_S = 60.0        # monstro respawna em 1 min
MARGEM_RESPAWN_S = 8.0         # só foge por tile visitado há < LIMITE - MARGEM
RAIO_PASSOS = 32
VEL_REL_MONSTRO = 0.85
MARGEM = 0.5
PENA_CURVA = 0.6
BONUS_ROTA = 6.0
BONUS_RETA = 3.0               # continuar no mesmo sentido (sem virar para trás)
PESO_PASSOS = 0.7
PESO_EXPOSICAO = 1.2
ESPACO_MAX = 4.0
PASSOS_UTEIS = 10
ISCA_DIST = 6
JX, JY = 10, 5
LOG_ENTRE_S = 3.0
CARD = ((0, -1), (1, 0), (0, 1), (-1, 0))
OITO = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)


def tiles_monstros(coord, cav):
    return [(coord[0] + round((cv[0] - 959) / 72.3), coord[1] + round((cv[1] - 452) / 72.3)) for cv in cav]


def _nome(dx, dy):
    return {(0, -1): "norte", (0, 1): "sul", (1, 0): "leste", (-1, 0): "oeste"}.get((dx, dy), "parado")


def _sinal(v):
    return (v > 0) - (v < 0)


class Fuga:
    def __init__(self, obst, log=print, rota=None, agir=False, trilha=None):
        self.obst, self.log, self.rota, self.agir, self.trilha = obst, log, rota or [], agir, trilha
        self.trilha_ativa = None             # foto da trilha tirada quando a fuga começa (ver Trilha.congelar)
        self._foto_desde = None
        self.est = "idle"                    # idle | fugindo
        self.stall_desde = None
        self.seguidas = 0
        self.plano = None
        self.cooldown_ate = -1.0
        self.n_gatilhos = 0
        self.t_ini = 0.0
        self.replans = 0
        self.vazio_desde = None
        self.ult_M = []
        self.dir_ult = None
        self.t_replan = -99.0
        # Ditto: sumiço / morte / revive
        self.t_sumiu = None
        self.t_voltou = None
        self.morto_cls = None
        self.t_rev_morto = -99.0
        self.tent_morto = 0
        self.rev_press_t = None
        self.tent_press = 0
        self._aus_desde = None
        self.fim_espaco = False
        # movimento
        self.hist = deque()
        self.dir_mov = None
        self.curvas = []
        self.motivo = ""

    # ------------------------------------------------------------ movimento
    def _atualiza_mov(self, t, pos):
        if self.hist and self.hist[-1][1] != pos:
            ant = self.hist[-1][1]
            dx, dy = pos[0] - ant[0], pos[1] - ant[1]
            d = (_sinal(dx), 0) if abs(dx) >= abs(dy) else (0, _sinal(dy))
            if self.dir_mov is not None and d != self.dir_mov:
                self.curvas.append((t, ant))                     # a esquina é o tile onde o sentido mudou
                self.curvas = self.curvas[-6:]
            self.dir_mov = d
        self.hist.append((t, pos))
        while self.hist and t - self.hist[0][0] > 4.0:
            self.hist.popleft()

    def _vel(self):
        h = [(t, p) for t, p in self.hist if self.hist[-1][0] - t <= 1.5]
        if len(h) >= 2 and h[-1][0] - h[0][0] >= 0.6:
            passos = sum(abs(b[1][0] - a[1][0]) + abs(b[1][1] - a[1][1]) for a, b in zip(h, h[1:]))
            v = passos / (h[-1][0] - h[0][0])
            if passos >= 2:
                return max(1.5, min(6.0, v))
        return V_PADRAO

    # ---------------------------------------------------------------- plano
    def _rota_tras(self, li):
        pts = {}
        if li is not None and self.rota:
            n = len(self.rota)
            for k, w in (((li + 1) % n, 0.5), ((li - 1) % n, 1.0), (li, 1.0)):
                for q in self.rota[k]["pontos"]:
                    tq = (int(q[0]), int(q[1]))
                    pts[tq] = max(pts.get(tq, 0.0), w)
        return pts

    def _seguro_trilha(self, q, t):
        """Tile visitado há menos de (LIMITE - MARGEM) s: os monstros de lá foram puxados e ainda não respawnaram."""
        a = (self.trilha_ativa or self.trilha).idade(q, t)
        return a is not None and a < LIMITE_RESPAWN_S - MARGEM_RESPAWN_S

    def _tempo_monstros(self, M, raio=RAIO_PASSOS + 8):
        d, fila = {}, deque()
        for m in M:
            d[m] = 0
            fila.append(m)
        while fila:
            p = fila.popleft()
            if d[p] >= raio:
                continue
            for dx, dy in OITO:
                q = (p[0] + dx, p[1] + dy)
                if q in d or not self.obst.walkable_game(q[0], q[1]):
                    continue
                d[q] = d[p] + 1
                fila.append(q)
        return d

    @staticmethod
    def _curvas_de(caminho):
        c, ant = 0, None
        for a, b in zip(caminho, caminho[1:]):
            dd = (b[0] - a[0], b[1] - a[1])
            if ant is not None and dd != ant:
                c += 1
            ant = dd
        return c

    def _espaco(self, q, raio=8):
        vis, fila = {q}, deque([(q, 0)])
        while fila:
            p, k = fila.popleft()
            if k >= raio:
                continue
            for dx, dy in CARD:
                r = (p[0] + dx, p[1] + dy)
                if r not in vis and self.obst.walkable_game(r[0], r[1]):
                    vis.add(r)
                    fila.append((r, k + 1))
        return len(vis)

    def planejar(self, coord, M, li=None, t=None):
        """Plano SÓ EM CRUZ, restrito à TRILHA (tiles visitados há < 52 s) quando há trilha e t. Sem caminho na trilha: cai no mapa todo
        (plano['fora_da_trilha']=True). -> dict(alvo, passos, folga, seguro, expo, curvas, na_rota, espaco, caminho, primeiro, isca) ou None."""
        ini = (coord[0], coord[1])
        M = list(M) if M else list(self.ult_M)
        if not M:
            return self._reta(ini, li, t)
        usar_trilha = self.trilha is not None and t is not None
        p = self._planejar_core(ini, M, li, (lambda q: self._seguro_trilha(q, t)) if usar_trilha else None)
        if p is None and usar_trilha:
            p = self._planejar_core(ini, M, li, None)
            if p is not None:
                p["fora_da_trilha"] = True
        return p

    def _planejar_core(self, ini, M, li, ok):
        n = len(M)
        mons = set(M)
        rota = self._rota_tras(li)
        Tm = self._tempo_monstros(M)
        dist = {ini: 0}
        fila = deque([ini])
        while fila:
            p = fila.popleft()
            if dist[p] >= RAIO_PASSOS:
                continue
            for dx, dy in CARD:
                q = (p[0] + dx, p[1] + dy)
                if q in dist or q in mons or not self.obst.walkable_game(q[0], q[1]) or (ok is not None and not ok(q)):
                    continue
                dist[q] = dist[p] + 1
                fila.append(q)
        custo, prev, passos, expo, contato = {ini: 0.0}, {}, {ini: 0}, {ini: 0.0}, {ini: 0}
        heap = [(0.0, ini)]
        while heap:
            c, p = heapq.heappop(heap)
            if c > custo.get(p, 1e9) + 1e-9 or passos[p] >= RAIO_PASSOS:
                continue
            for dx, dy in CARD:
                q = (p[0] + dx, p[1] + dy)
                if q in mons or q not in dist:
                    continue
                k = passos[p] + 1
                d = max(0.0, MARGEM - (Tm.get(q, 99.0) / VEL_REL_MONSTRO - k))
                nc = c + 1.0 + 1.5 * d
                if nc < custo.get(q, 1e9) - 1e-9:
                    custo[q], prev[q], passos[q], expo[q] = nc, p, k, expo[p] + d
                    contato[q] = contato[p] + (1 if d > 0 else 0)
                    heapq.heappush(heap, (nc, q))
        cand = [q for q in custo if q != ini and passos[q] >= min(PASSOS_UTEIS, max(passos.values()))]
        if not cand:
            return None
        fe = min(n, 6) / 2.0

        def folga_fim(q):
            return Tm.get(q, 99.0) / VEL_REL_MONSTRO - passos[q]

        def s0(q):
            return PESO_PASSOS * passos[q] - PESO_EXPOSICAO * fe * expo[q] + max(-4.0, min(6.0, folga_fim(q)))

        def caminho_de(q):
            c = [q]
            while c[-1] in prev:
                c.append(prev[c[-1]])
            return c[::-1]
        melhor = None
        for q in sorted(cand, key=lambda q: -s0(q))[:60]:
            cam = caminho_de(q)
            curv = self._curvas_de(cam)
            esp = self._espaco(q)
            w_fim = max([rota.get(q, 0.0)] + [rota.get(tuple(x), 0.0) for x in cam[-4:]])
            frac = sum(rota.get(tuple(x), 0.0) for x in cam) / len(cam)
            pri = (cam[1][0] - ini[0], cam[1][1] - ini[1]) if len(cam) > 1 else (0, 0)
            dir_b = 0.0
            if self.dir_mov is not None:
                dir_b = BONUS_RETA if pri == self.dir_mov else (-BONUS_RETA if pri == (-self.dir_mov[0], -self.dir_mov[1]) else 0.0)
            score = (s0(q) - PENA_CURVA * curv + BONUS_ROTA * (0.6 * w_fim + 0.4 * frac) + dir_b
                     + ESPACO_MAX * min(esp, 120) / 120.0)
            if melhor is None or score > melhor[0]:
                melhor = (score, q, cam, curv, w_fim >= 1.0 or frac >= 0.5, esp)
        _, alvo, cam, curv, na_rota, esp = melhor
        pri = cam[1] if len(cam) > 1 else alvo
        plano = {"alvo": alvo, "passos": len(cam) - 1, "folga": round(folga_fim(alvo), 1), "seguro": expo[alvo] <= 1e-9,
                 "expo": round(expo[alvo], 1), "contato": contato[alvo], "curvas": curv, "na_rota": na_rota, "espaco": esp,
                 "primeiro": _nome(pri[0] - ini[0], pri[1] - ini[1]), "caminho": cam,
                 "dmin_hoje": round(min(math.hypot(ini[0] - m[0], ini[1] - m[1]) for m in M), 1)}
        plano["isca"] = self._isca(ini, cam)
        self.dir_ult = (pri[0] - ini[0], pri[1] - ini[1])
        return plano

    def _reta(self, ini, li, t=None):
        d = self.dir_mov or self.dir_ult
        if d is None or d not in CARD:
            return None
        cam, p = [ini], ini
        for _ in range(14):
            q = (p[0] + d[0], p[1] + d[1])
            if not self.obst.walkable_game(q[0], q[1]) or (self.trilha is not None and t is not None and not self._seguro_trilha(q, t)):
                break
            cam.append(q)
            p = q
        if len(cam) < 3:
            return None
        return {"alvo": cam[-1], "passos": len(cam) - 1, "folga": 99.0, "seguro": True, "expo": 0.0, "contato": 0, "curvas": 0,
                "na_rota": False, "espaco": 0, "primeiro": _nome(*d), "caminho": cam, "dmin_hoje": 99.0, "isca": None}

    def _isca(self, ini, cam):
        k = min(len(cam) - 1, 8)
        fx, fy = cam[k][0] - ini[0], cam[k][1] - ini[1]
        if abs(fx) >= abs(fy):
            ux, uy = (-1 if fx > 0 else 1), 0
        else:
            ux, uy = 0, (-1 if fy > 0 else 1)
        for dist in range(ISCA_DIST, 1, -1):
            tx, ty = ux * dist, uy * dist
            if abs(tx) > JX or abs(ty) > JY:
                continue
            if self.obst.walkable_game(ini[0] + tx, ini[1] + ty):
                return {"tile": (ini[0] + tx, ini[1] + ty), "rel": (tx, ty)}
        return None

    def _txt_plano(self, p):
        if p is None:
            return "sem rota de fuga (cercado / sem mapa)"
        risco = "" if p["seguro"] else f" (passa por {p.get('contato', 0)} tile(s) onde o monstro pode chegar antes: contato)"
        trilha = " FORA da trilha (sem caminho pelos últimos 52 s: os monstros podem ter respawnado)" if p.get("fora_da_trilha") else ""
        return (f"ir para {p['alvo']} em {p['passos']} passos SÓ EM CRUZ, {p['curvas']} curva(s) (primeiro: {p['primeiro']})"
                f"{' pela ROTA gravada' if p['na_rota'] else ''}; folga no destino {p['folga']} passos, espaço aberto {p.get('espaco', '?')} tiles"
                f"{risco}{trilha}" + (f"; isca: Ditto para {p['isca']['tile']}" if p.get("isca") else ""))

    # ------------------------------------------------- regras do revive (fuga)
    def _idx(self, pos, plano):
        cam = plano["caminho"]
        best = None
        for i, q in enumerate(cam):
            md = abs(q[0] - pos[0]) + abs(q[1] - pos[1])
            if md <= 1 and (best is None or (md, -i) < best[0]):
                best = ((md, -i), i)
        return None if best is None else best[1]

    def reta_restante(self, pos, plano, t=None):
        """Quantos tiles dá para correr SEM VIRAR a partir de pos: pelo caminho do plano e, se ele acaba em linha reta, pelos tiles andáveis
        (e dentro da trilha) à frente. Esse número / velocidade = quanto tempo de corrida reta resta."""
        cam = plano["caminho"]
        i = self._idx(pos, plano)
        if i is None or i >= len(cam) - 1:
            return 0
        d = (cam[i + 1][0] - cam[i][0], cam[i + 1][1] - cam[i][1])
        n = 0
        while i + 1 < len(cam) and (cam[i + 1][0] - cam[i][0], cam[i + 1][1] - cam[i][1]) == d:
            n += 1
            i += 1
        if i == len(cam) - 1:                                     # o caminho termina em reta: continua enquanto andável (e na trilha)
            q = cam[-1]
            for _ in range(10):
                q = (q[0] + d[0], q[1] + d[1])
                if not self.obst.walkable_game(q[0], q[1]) or (self.trilha is not None and t is not None and not self._seguro_trilha(q, t)):
                    break
                n += 1
        return n

    def esquina_fora_da_tela(self, pos):
        """Se houve virada depois que a lista esvaziou, a esquina precisa estar FORA da tela (os monstros só reaparecem quando a alcançam)."""
        if self.vazio_desde is None:
            return True
        rec = [(tc, q) for tc, q in self.curvas if tc >= self.vazio_desde - 0.7]
        if not rec:
            return True
        q = rec[-1][1]
        return abs(q[0] - pos[0]) > JX or abs(q[1] - pos[1]) > JY

    def chegando_ao_limite(self, pos, plano, precisa, t):
        """Os próximos tiles do caminho já são velhos demais (> 52 s) ou o caminho acabou e não há mais espaço seguro: reviver JÁ."""
        if self.trilha is None or plano is None:
            return False
        cam = plano["caminho"]
        i = self._idx(pos, plano)
        if i is None:
            return False
        proximos = cam[i + 1:i + 2 + precisa]
        if any(not self._seguro_trilha(q, t) for q in proximos):
            return True
        return self.fim_espaco

    # ------------------------------------------------------------- máquina
    def atualizar(self, t, r, coord, cav, revive_do_bot, li=None, na_parada=False, fuga_fim=False, t_revive=-99.0,
                  coord_quadro=None, vig=None, t_e=None):
        """Chamar a cada quadro válido. coord = posição VIVA; coord_quadro = posição no instante do quadro (as caveiras são relativas a ela).
        vig = estado() do VigiaDitto; t_e = instante da última tecla 'e' vista (sua ou do bot).
        Devolve None ou dict: fugir(plano) / isca(rel) / parar / revive / fim / alarme(texto)."""
        if r is None or coord is None:
            return None
        if self.est == "idle" and self._foto_desde is not None and t - self._foto_desde > 0.0:
            self.trilha_ativa, self._foto_desde = None, None   # fuga acabou: solta a foto da trilha
        pos = (coord[0], coord[1])
        self._atualiza_mov(t, pos)
        n = r.get("n_monstros", 0)
        sem_stun = r.get("sem_stun")
        M = tiles_monstros(coord_quadro or coord, cav)
        if M:
            self.ult_M = M
        # ---- o Ditto está na Battle list? (o vigia lê ~15x/s; sem ele, usa o quadro do laço)
        vig_ok = bool(vig) and vig.get("painel_ok") and vig.get("presente") is not None and (t - vig.get("t", -99.0)) <= 0.6
        if vig_ok:
            presente, aus_desde = bool(vig["presente"]), vig.get("ausente_desde")
            desconhecido = False
        else:
            presente = bool(r.get("ditto_presente")) and not r.get("revive_em_curso")
            desconhecido = r.get("nivel") == "cinza"
            self._aus_desde = None if presente else (self._aus_desde if self._aus_desde is not None else t)
            aus_desde = self._aus_desde
        t_e_last = max(t_e if t_e is not None else -99.0, t_revive if t_revive is not None else -99.0)
        if presente:
            if self.t_sumiu is not None:
                self.t_voltou = t
                self.tent_morto = 0
            self.t_sumiu, self.morto_cls = None, None
        elif self.t_sumiu is None and not desconhecido:
            self.t_sumiu = aus_desde if aus_desde is not None else t
            self.morto_cls = (self.t_sumiu - t_e_last) > REVIVE_JANELA_S       # sem 'e' até 1 s antes de sumir = MORREU
        morto = (not presente) and bool(self.morto_cls) and self.t_sumiu is not None and (t - self.t_sumiu) >= MORTE_DEBOUNCE_S and not desconhecido
        ac = {}

        # ====== SITUAÇÃO 3 (PRIORIDADE): Ditto morto -> revive na hora, em qualquer estado
        if morto:
            ult_press = max(self.t_rev_morto, t_e_last)
            if (ult_press < self.t_sumiu or t - ult_press >= REVIVE_RETRY_S) and self.tent_morto < REVIVE_TENT_MAX:
                self.tent_morto += 1
                self.t_rev_morto = t
                txt = (f"DITTO MORTO: sumiu da Battle list e o último revive foi há {self.t_sumiu - t_e_last:.1f} s (a janela é {REVIVE_JANELA_S:.0f} s); "
                       f"{n} monstro(s) na tela")
                if self.agir:
                    self.log(f"[fuga] {txt}. REVIVE AGORA (tentativa {self.tent_morto}/{REVIVE_TENT_MAX}).")
                    ac["revive"] = True
                else:
                    self.log(f"[fuga] (SÓ LOG: --fuga desligado) {txt}. Bateria o revive AGORA.")

        # ====== SITUAÇÃO 1: sobra sem recurso
        cond_b = presente and bool(r.get("area_esgotada")) and sem_stun is not None and sem_stun >= 1 and n >= 1 and not revive_do_bot
        if cond_b:
            if self.stall_desde is None:
                self.stall_desde = t
        else:
            self.stall_desde = None
        stall_s = (t - self.stall_desde) if self.stall_desde is not None else 0.0
        B = cond_b and stall_s >= STALL_S
        self.seguidas = self.seguidas + 1 if B else 0

        # ---------------- idle: decide se começa a fuga
        if self.est == "idle":
            if t < self.cooldown_ate:
                return ac or None
            gatilho_morte = morto and n >= 1
            if not (gatilho_morte or self.seguidas >= LEITURAS):
                return ac or None
            self.trilha_ativa = self.trilha.congelar() if self.trilha is not None else None      # a trilha de ANTES da fuga
            self._foto_desde = t
            self.plano = self.planejar(coord, M, li, t)
            self.n_gatilhos += 1
            if gatilho_morte:
                motivo, urg = "Ditto morto", "MÁXIMA"
            else:
                motivo, urg = f"sobra sem recurso há {stall_s:.0f} s (skills de área em recarga, {sem_stun} monstro(s) SEM stun)", ("ALTA" if n >= 3 else "média")
            self.motivo = motivo
            head = f"[fuga] GATILHO #{self.n_gatilhos}: {motivo}; {n} monstro(s) vivo(s), urgência {urg}. Plano: {self._txt_plano(self.plano)}."
            self.cooldown_ate = t + COOLDOWN_S
            if not self.agir:
                self.log(head.replace("[fuga]", "[fuga] (SÓ LOG: --fuga desligado, NÃO foge)"))
                return ac or None
            if not na_parada:
                self.log(head + " NÃO fujo: o Player não está parado na parada (está andando e o lure segue).")
                ac["alarme"] = "fuga com o Player andando"
                return ac
            if self.plano is None:
                self.log(head + " ASSUMA VOCÊ.")
                ac["alarme"] = "sem rota de fuga"
                return ac
            self.log(head.replace("[fuga]", "[fuga] FUGINDO") + " Shift+F cancela.")
            self.est, self.t_ini, self.replans = "fugindo", t, 0
            self.vazio_desde, self.rev_press_t, self.tent_press, self.fim_espaco, self.t_replan = None, None, 0, False, t
            if not gatilho_morte:
                self.t_voltou = None
            ac["fugir"] = self.plano
            if presente and self.plano.get("isca"):
                ac["isca"] = self.plano["isca"]["rel"]
            return ac

        # ---------------- fugindo
        self.vazio_desde = (self.vazio_desde if self.vazio_desde is not None else t) if n == 0 else None
        vazio_s = (t - self.vazio_desde) if self.vazio_desde is not None else 0.0
        if t - self.t_ini > FUGA_MAX_S:
            self.est, self.cooldown_ate = "idle", t + COOLDOWN_ALARME_S
            self.log(f"[fuga] {FUGA_MAX_S:.0f} s de fuga e o Ditto não voltou/ainda há monstro: devolvo o controle. ASSUMA VOCÊ.")
            ac.update({"parar": True, "fim": True, "alarme": "fuga estourou o tempo"})
            return ac
        t_press = max(self.rev_press_t if self.rev_press_t is not None else -99.0, self.t_rev_morto if self.t_rev_morto >= self.t_ini else -99.0,
                      t_e_last if t_e_last >= self.t_ini else -99.0)
        revive_feito = t_press > -50.0
        # sucesso: o Ditto reapareceu DEPOIS do revive e já passou o tempo de o revive terminar
        if presente and revive_feito and self.t_voltou is not None and self.t_voltou >= t_press and t - self.t_voltou >= REVIVE_FINALIZA_S:
            self.est, self.cooldown_ate = "idle", t + COOLDOWN_S
            self.log("[fuga] Ditto REAPARECEU em campo e o revive terminou: paro a fuga e devolvo o controle ao ciclo normal.")
            ac.update({"parar": True, "fim": True})
            return ac
        v = self._vel()
        precisa = max(2, math.ceil(REVIVE_FINALIZA_S * v) + 1)
        if not revive_feito and self.plano is not None:
            limite = self.chegando_ao_limite(pos, self.plano, precisa, t)
            reta = self.reta_restante(pos, self.plano, t)
            reta_ok = reta >= precisa
            esq_ok = self.esquina_fora_da_tela(pos)
            if limite or (vazio_s >= REVIVE_VAZIO_S and reta_ok and esq_ok):
                self.rev_press_t, self.tent_press = t, self.tent_press + 1
                if limite:
                    self.log(f"[fuga] chegando ao LIMITE de 1 min de onde passei (monstro respawna): REVIVE JÁ, mesmo com {n} monstro(s) na tela.")
                else:
                    self.log(f"[fuga] lista vazia há {vazio_s:.1f} s, {reta} tiles de reta pela frente (precisa {precisa}), esquina fora da tela: "
                             "REVIVE e continuo correndo.")
                ac["revive"] = True
            elif vazio_s >= REVIVE_VAZIO_S and t - self.t_replan > 1.0 and (not reta_ok or not esq_ok):
                self.t_replan = t
                self.log(f"[fuga] lista vazia, mas NÃO é seguro reviver agora ({'virada à frente: reta de só ' + str(reta) + ' tiles' if not reta_ok else 'esquina ainda à vista'}):"
                         " concluo a virada e reavalio.")
        elif (revive_feito and self.rev_press_t is not None and self.t_sumiu is None and (self.t_voltou is None or self.t_voltou < self.rev_press_t)
                and t - self.rev_press_t > 2.0 and self.tent_press < 3):
            self.rev_press_t, self.tent_press = t, self.tent_press + 1             # o 'e' não fez o Ditto sumir: tenta de novo
            self.log("[fuga] o revive não surtiu efeito (o Ditto não sumiu): aperto de novo.")
            ac["revive"] = True
        # continuação do caminho: a fuga NÃO pode parar no fim dele (e nem antes de o Ditto voltar)
        cam = self.plano["caminho"] if self.plano is not None else []
        i = self._idx(pos, self.plano) if self.plano is not None else None
        restante = (len(cam) - 1 - i) if i is not None else 0
        if (fuga_fim or restante <= EXT_ANTES) and t - self.t_replan >= 0.8:
            self.t_replan = t
            if self.replans >= REPLANS_MAX:
                self.est, self.cooldown_ate = "idle", t + COOLDOWN_ALARME_S
                self.log("[fuga] sem mais continuações. ASSUMA VOCÊ.")
                ac.update({"fim": True, "parar": True, "alarme": "fuga sem efeito"})
                return ac
            novo = self.planejar(coord, M, li, t)
            if novo is None:
                self.fim_espaco = True                                            # sem mais espaço seguro: o revive passa a ser imediato
                if fuga_fim and not revive_feito:
                    self.log("[fuga] acabou o espaço SEGURO (trilha de < 52 s): REVIVE agora e assumo.")
                    self.rev_press_t, self.tent_press = t, self.tent_press + 1
                    ac["revive"] = True
                    ac["alarme"] = "fim do espaço seguro"
            else:
                self.replans += 1
                self.plano = novo
                if fuga_fim or restante <= 2:
                    self.log(f"[fuga] CONTINUO a fuga ({self.replans}/{REPLANS_MAX}): {self._txt_plano(novo)}.")
                ac["fugir"] = novo
        return ac or None
