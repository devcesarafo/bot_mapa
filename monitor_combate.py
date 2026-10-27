# -*- coding: utf-8 -*-
"""
Monitor de combate (DRY-RUN): lê a tela e mostra o que o bot DECIDIRIA.
NÃO aperta nenhuma tecla e NÃO controla o jogo.

A janela tem 4 CARTÕES, um para cada tipo de decisão (podem estar todos ativos ao mesmo tempo):
    ATAQUE   quando apertar R (e continuar em rajada)
    DEFESA   quando usar G e depois F
    CURA     quando usar C
    REVIVE   quando reviver / quando FUGIR (stun conferido monstro a monstro)
Cores:  cinza = nada a fazer | verde = tudo certo | AMARELO = ação recomendada | VERMELHO = perigo

Lê a ~8 quadros por segundo: Battle list, anéis de stun + caveiras, barra de skills
(cooldown de F, G, C e F2), coordenada do jogo e (opcional) os SEUS apertos de teclas do jogo,
para comparar no log o que o monitor recomendou com o que você fez.

Atalhos: Ctrl+Alt+S = tira um PRINT (a janela do monitor APARECE no print; as capturas
do monitor continuam ignorando a janela). Também há o botão "Print".
Os prints vão para monitor_logs\\prints\\ (um .png e um .json com o estado exato naquele instante).

Uso ao vivo:  python monitor_combate.py        (rode como administrador)
              python monitor_combate.py --janela          janela normal, maior
              python monitor_combate.py --sem-registro    não registra seus apertos de teclas
              python monitor_combate.py --lures caminho\\lures.json   pontos de parada (opcional)
Replay:       python monitor_combate.py --replay quadros\\2026-10-14_02-33-57
Dependências:  pip install opencv-python numpy pillow mss pynput
Arquivos na mesma pasta: leitor_coord.py, battle_list.py, detector_stun.py, barra_skills.py, atalhos.py
"""
import os
import sys
import json
import time
import queue
import threading

import numpy as np

from battle_list import ler_battle_list, calibrar
from detector_stun import (achar_aneis, achar_caveiras, achar_ditto, status_por_monstro, JanelaAneis, configurar,
                           distancia_tiles, na_area_do_stun)
from barra_skills import LeitorSkills
from leitor_coord import ler_coord

# ---------------------------------------------------------------- ajustes (comentados com a origem)
CURA_HP = 0.45              # usar C abaixo disso (usuário: 40 a 45%)
DEFESA_QUEDA = 0.03         # usuário: com 3 a 5% de dano a defesa já deve ter sido usada
DEFESA_APOS_S = 8.0         # usuário: bater F+G ~8 s depois do começo do lure
F_DURACAO_S = 12.0          # usuário: a defesa (F) dura 12 s
G_DURACAO_S = 10.0          # usuário: o G dura 10 s e só aumenta a velocidade do Ditto: usar CEDO
LURE_MAX_S = 20.0           # usuário: o lure dura ~20 s
R_PARADO_MIN_S = 2.5        # medido (coordenada): R veio 2,7 a 5,0 s (mediana 4,1 s) depois de o personagem parar
R_PARADO_MAX_S = 5.0        # espera máxima antes de apertar R de qualquer jeito (o maior tempo medido)
R_EXTENSAO_MAX = 7.0        # tiles: monstros "juntos" (medido: 4 a 6 tiles no momento do R)
R_VELOC_MAX = 2.0           # tiles/s: só barra um grupo claramente em movimento (o pacote "treme" ~1 a 1,9 tiles/s)
LATCH_TOL_PX = 40           # um monstro stunado não anda: se saiu mais que isso da posição confirmada, perdeu o stun
DITTO_MEMORIA_S = 1.5       # se o Ditto não for visto num quadro, usa a última posição por esse tempo
R_ESTAVEL_S = 1.0           # o nº de monstros na Battle list precisa ficar igual por isso (parou de chegar)
R_EMERGENCIA_HP = 0.55      # Ditto abaixo disso com 3+ monstros: R já
PARADO_S = 0.8              # coordenada sem mudar por isso = personagem parado
AREA_SKILLS = ("F2", "F3", "F4")   # skills de dano em área do macro R (usuário, 22/10): esgotadas = R não adianta até o revive
STUN_DURACAO_S = 6.2        # medido
STUN_AVISO_S = 2.5
REVIVE_STUN_MIN_S = 2.5     # só libera revive com monstro vivo se restar pelo menos isso de stun
JANELA_ANEIS_S = 0.5
TILE_PX = 72.3

NADA, OK, ACAO, PERIGO, CINZA = "nada", "ok", "acao", "perigo", "cinza"
ORDEM_NIVEL = {NADA: 0, OK: 1, ACAO: 2, PERIGO: 3, CINZA: 4}


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class Cerebro:
    """Transforma leituras em decisões (uma por cartão). Independe de tela e de interface.

    STUN: confere MONSTRO A MONSTRO (caveira + anel). Contar anéis não basta."""

    def __init__(self, paradas=None):
        self.janela = JanelaAneis(JANELA_ANEIS_S)
        self.paradas = [tuple(p[:2]) for p in (paradas or [])]
        self.hp_hist = []
        self.stun_inicio = None
        self.latch = None
        self.lure_inicio = None
        self.sem_monstros_desde = None
        self.hp_base = None
        self.ditto_ausente_ant = False
        self.coord_ult = None
        self.coord_mudou_t = None
        self.skull_hist = []
        self.n_mon_ult = None
        self.n_mon_mudou_t = None
        self.ditto_pos = None
        self.ditto_pos_t = -99.0
        self.mon_hp_hist = []
        self.cd_desde = {}
        self.pronta_ant = {}
        self.ult_R_usuario = -99.0
        self.ult_revive = -99.0
        self.ultimo = None

    # ---- medidas auxiliares
    def _movimento_monstros(self, t, caveiras):
        pts = [(c[0], c[1]) for c in caveiras]
        self.skull_hist.append((t, pts))
        self.skull_hist = [(tt, p) for tt, p in self.skull_hist if t - tt <= 2.0]
        if not pts:
            return None, None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        ext = (max(xs) - min(xs)) / TILE_PX, (max(ys) - min(ys)) / TILE_PX
        ant = [(tt, p) for tt, p in self.skull_hist if 0.35 <= t - tt <= 0.9]
        veloc = None
        if ant and ant[-1][1]:
            tt, p0 = ant[-1]
            d = [min(_cheb(a, b) for b in p0) for a in pts]
            veloc = float(np.median(d)) / TILE_PX / max(t - tt, 1e-6)
        return ext, veloc

    def _atacando(self, t, battle):
        """Algum monstro perdeu vida no último segundo?"""
        hps = [e["hp"] for e in battle["monstros"]]
        if not hps:
            self.mon_hp_hist = []
            return False
        self.mon_hp_hist.append((t, float(np.mean(hps)), len(hps)))
        self.mon_hp_hist = [x for x in self.mon_hp_hist if t - x[0] <= 1.5]
        a = [x for x in self.mon_hp_hist if t - x[0] >= 0.6]
        return bool(a) and (a[0][1] - float(np.mean(hps)) >= 0.08) and a[0][2] >= len(hps)

    def observar_coord(self, t, coord):
        """Registra a coordenada sem processar um quadro (o replay usa isso para os quadros que não foram salvos)."""
        if coord is not None and coord[:2] != self.coord_ult:
            self.coord_ult, self.coord_mudou_t = coord[:2], t

    def esquecer_ditto(self):
        """O bot moveu o Ditto: a posição guardada (memória de 1,5 s) está errada; só vale uma detecção nova."""
        self.ditto_pos = None

    def processar(self, t, battle, aneis, coord=None, caveiras=None, skills=None, eventos=(), ditto_pos=None):
        for e in eventos:
            if e == "atacar":
                self.ult_R_usuario = t
            elif e == "revive":
                self.ult_revive = t
                self.ult_R_usuario = -99.0       # revive = ciclo novo: a 'rajada em curso' do lure anterior acabou
                self.hp_hist = []                # a barra do Ditto muda de valor no revive: não é dano
        self.janela.adicionar(t, aneis)
        n_mon = battle["n_monstros"]
        ditto = battle["ditto"]
        hp = ditto["hp"] if ditto else None

        # ---- vida do Ditto, lure e dano acumulado
        if hp is not None:
            self.hp_hist.append((t, hp))
            self.hp_hist = [(tt, h) for tt, h in self.hp_hist if t - tt <= 3.0]
        if ditto is None or (skills and skills.get("revive_em_curso")):    # barra de skills preta também marca o revive
            self.ditto_ausente_ant = True
        elif self.ditto_ausente_ant:                 # acabou de voltar do revive: vida cheia, skills resetadas
            self.ditto_ausente_ant = False
            self.ult_R_usuario = -99.0               # monstro que aparece depois do revive é do PRÓXIMO lure
            self.hp_hist = []
            self.hp_base = hp
            self.lure_inicio = t if n_mon > 0 else None
        if n_mon == 0:
            self.sem_monstros_desde = self.sem_monstros_desde if self.sem_monstros_desde is not None else t
            if t - self.sem_monstros_desde >= 2.0:
                self.lure_inicio = None
        else:
            self.sem_monstros_desde = None
            if self.lure_inicio is None:
                self.lure_inicio = t
                self.hp_base = hp
        if hp is not None and self.lure_inicio is not None and t - self.lure_inicio <= 2.0:
            self.hp_base = max(self.hp_base or 0.0, hp)
        lure_t = (t - self.lure_inicio) if self.lure_inicio is not None else None
        queda_total = (self.hp_base - hp) if (hp is not None and self.hp_base is not None) else 0.0

        # ---- personagem parado?
        if coord is not None:
            if coord[:2] != self.coord_ult:
                self.coord_ult, self.coord_mudou_t = coord[:2], t
        parado_for = (t - self.coord_mudou_t) if (coord is not None and self.coord_mudou_t is not None) else None
        parado = parado_for is not None and parado_for >= PARADO_S
        no_ponto = None
        if coord is not None and self.paradas:
            no_ponto = any(_cheb(coord[:2], p) <= 2 for p in self.paradas)

        # ---- skills (cooldown)
        revive_em_curso = bool(skills and skills.get("revive_em_curso"))
        cd = {}
        if skills and not revive_em_curso:
            for k in ("F", "G", "C", "F2", "F3", "F4"):
                pronta = skills[k]["pronta"]
                cd[k] = not pronta
                if self.pronta_ant.get(k, True) and not pronta:
                    self.cd_desde[k] = t
                if pronta:
                    self.cd_desde.pop(k, None)
                self.pronta_ant[k] = pronta
        known = bool(cd)

        # ---- stun, monstro a monstro
        pos = self.janela.posicoes(t)
        if n_mon <= 0:
            sem_stun = 0
        elif caveiras is not None:
            _, sem_stun = status_por_monstro(caveiras, pos, n_mon)
        else:
            sem_stun = max(0, n_mon - self.janela.max_aneis(t))
        sem_stun = min(sem_stun, n_mon)
        # --- TRAVA da confirmação (o anel pisca e às vezes fica escondido por um quadro):
        # depois que TODOS foram confirmados stunados, vale pelos STUN_DURACAO_S segundos, e só cai
        # com evidência contrária: monstro que saiu do lugar, monstro novo ou Ditto levando dano.
        if caveiras is not None:
            status, _ = status_por_monstro(caveiras, pos, n_mon)
            if n_mon > 0 and sem_stun == 0 and (self.latch is None or t > self.latch["t0"] + STUN_DURACAO_S):
                self.latch = {"t0": t, "pos": [(m["x"], m["y"]) for m in status], "n": n_mon}
            if n_mon == 0:
                self.latch = None
            if self.latch is not None and hp is not None:
                recentes = [h for tt, h in self.hp_hist if t - tt <= 1.0]
                em_revive = revive_em_curso or self.ditto_ausente_ant or (t - self.ult_revive) <= 3.0
                if recentes and max(recentes) - hp >= 0.05 and not em_revive:
                    self.latch = None             # o Ditto está apanhando: alguém não está stunado (no revive a barra muda: não vale)
            if self.latch is not None and t <= self.latch["t0"] + STUN_DURACAO_S:
                sem_latch = sum(1 for m in status if not m["stunado"] and
                                not any(_cheb((m["x"], m["y"]), q) <= LATCH_TOL_PX for q in self.latch["pos"]))
                sem_latch += max(0, n_mon - self.latch["n"])       # monstros que chegaram depois
                sem_stun = min(sem_stun, min(sem_latch, n_mon))
        if n_mon > 0 and self.janela.max_aneis(t) > 0:
            if self.stun_inicio is None or t > self.stun_inicio + STUN_DURACAO_S:
                self.stun_inicio = t
        if n_mon == 0:
            self.stun_inicio = None
        restante = None
        if self.stun_inicio is not None and n_mon > 0:
            restante = max(0.0, self.stun_inicio + STUN_DURACAO_S - t)
        curto = restante is not None and restante < STUN_AVISO_S

        ext, veloc = self._movimento_monstros(t, caveiras or [])
        atacando = self._atacando(t, battle)
        if n_mon != self.n_mon_ult:                      # ainda chegando monstro? (o nº na Battle list muda)
            self.n_mon_ult, self.n_mon_mudou_t = n_mon, t
        n_estavel = t - self.n_mon_mudou_t

        # ---- onde está o Ditto e quem está FORA do alcance dele (área do Whirlwind: 7x7 sem os cantos)
        if ditto is None:
            self.ditto_pos = None
        elif ditto_pos is not None:
            self.ditto_pos, self.ditto_pos_t = tuple(ditto_pos), t
        dp = self.ditto_pos if (self.ditto_pos is not None and t - self.ditto_pos_t <= DITTO_MEMORIA_S) else None
        fora_lista, dist_max, direcao = [], None, ""
        if dp is not None and caveiras:
            for c in caveiras:
                dx, dy = distancia_tiles(dp, c)
                if not na_area_do_stun(dx, dy):
                    fora_lista.append((dx, dy))
            if fora_lista:
                dist_max = max(max(abs(a), abs(b)) for a, b in fora_lista)
                mx = sum(a for a, b in fora_lista) / len(fora_lista)
                my = sum(b for a, b in fora_lista) / len(fora_lista)
                vert = "N" if my < -0.7 else ("S" if my > 0.7 else "")
                horz = "L" if mx > 0.7 else ("O" if mx < -0.7 else "")
                direcao = (vert + horz) or "o grupo"
        n_fora = len(fora_lista)

        # ================= CARTÃO ATAQUE (R)
        area_esgotada = bool(known and all(cd.get(k) for k in AREA_SKILLS))
        r_tipo = None     # "curso" | "normal" | "emergencia" | "espera_fora": o bot só aperta R em curso/normal
        emerg = False
        if ditto is None or n_mon == 0:
            c_ataque = (NADA, "Sem monstros para atacar." if n_mon == 0 else "Ditto fora.")
        elif area_esgotada:
            c_ataque = (NADA, "Skills de área (F2, F3, F4) todas em recarga: R não adianta. Só um revive reseta.")
        else:
            emerg = (hp <= R_EMERGENCIA_HP and n_mon >= 3) or (lure_t is not None and lure_t >= LURE_MAX_S)
            aviso_fora = (f" ⚠ {n_fora} fora do alcance do Ditto (até {dist_max:.0f} tiles) ficam vivos." if n_fora else "")
            if atacando or (t - self.ult_R_usuario) <= 2.5:
                r_tipo = "curso"
                c_ataque = (ACAO, f"Rajada em curso: continuar o R até zerar ({n_mon} vivo(s))." + aviso_fora)
            elif emerg and not parado:         # parado: avalia pelas regras normais primeiro (antes a emergência as mascarava)
                r_tipo = "emergencia"
                c_ataque = (ACAO, f"R AGORA (emergência: Ditto {hp * 100:.0f}%" + (f", lure {lure_t:.0f} s)." if lure_t else ").") + aviso_fora)
            elif parado:
                if dp is not None:
                    juntos = n_fora == 0 and n_estavel >= R_ESTAVEL_S           # todos DENTRO da área do stun
                else:
                    juntos = ext is not None and ext[0] <= R_EXTENSAO_MAX and ext[1] <= R_EXTENSAO_MAX \
                        and (veloc is None or veloc <= R_VELOC_MAX) and n_estavel >= R_ESTAVEL_S
                lugar = "" if no_ponto is None else (", no ponto de parada" if no_ponto else ", FORA do ponto de parada")
                nota = "" if dp is not None else " (Ditto não localizado)"
                r_tipo = "normal"
                if n_fora > 0 and parado_for >= R_PARADO_MAX_S:
                    r_tipo = "espera_fora"
                    c_ataque = (ACAO, f"R AGORA (espera esgotada, parado {parado_for:.1f} s)." + aviso_fora + f" Mover o Ditto para {direcao}.")
                elif n_fora > 0:
                    c_ataque = (NADA, f"Aguardar: {n_fora} de {n_mon} FORA do alcance do Ditto (até {dist_max:.0f} tiles). Mover o Ditto para {direcao} (~{max(1, round(dist_max - 2))} tiles).")
                elif parado_for >= R_PARADO_MAX_S:
                    c_ataque = (ACAO, f"R AGORA: parado há {parado_for:.1f} s{lugar} (espera esgotada).{nota}")
                elif parado_for >= R_PARADO_MIN_S and juntos:
                    jt = "todos no alcance do Ditto" if dp is not None else f"monstros juntos ({ext[0]:.0f}x{ext[1]:.0f} tiles)"
                    c_ataque = (ACAO, f"R AGORA: {jt}{lugar}, parado {parado_for:.1f} s.")
                else:
                    falta = max(0.0, R_PARADO_MIN_S - parado_for)
                    motivo = f", faltam ~{falta:.1f} s" if falta > 0 else (", ainda chegando monstro" if n_estavel < R_ESTAVEL_S else ", monstros ainda se mexendo")
                    c_ataque = (NADA, f"Aguardar juntar: parado {parado_for:.1f} s{motivo}.{nota}")
            elif parado_for is None:
                c_ataque = (NADA, f"{n_mon} monstro(s). Sem coordenada: não sei se parou.")
            else:
                c_ataque = (NADA, "Lure andando" + (f" ({lure_t:.0f} s)" if lure_t is not None else "") + ": aguardar o ponto de parada.")
        if emerg and r_tipo == "normal" and c_ataque[0] == NADA:     # parado mas ainda sem juntar, com o Ditto baixo: aviso ao humano
            r_tipo = "emergencia"
            c_ataque = (ACAO, f"R AGORA (emergência: Ditto {hp * 100:.0f}%" + (f", lure {lure_t:.0f} s)." if lure_t else ").") + aviso_fora)

        # ================= CARTÃO DEFESA (G depois F)
        acao_def, acao_cura = [], False          # decisões estruturadas (o bot_fase1 usa estas, não o texto)
        if ditto is None or n_mon == 0:
            c_def = (NADA, "—")
        elif sem_stun == 0:
            c_def = (NADA, "Todos stunados: defesa dispensável agora.")
        elif known:
            fcd, gcd = cd.get("F"), cd.get("G")
            rf = max(0.0, F_DURACAO_S - (t - self.cd_desde.get("F", t)))
            rg = max(0.0, G_DURACAO_S - (t - self.cd_desde.get("G", t)))
            gatilho_f = queda_total >= DEFESA_QUEDA or (lure_t is not None and lure_t >= DEFESA_APOS_S)
            if fcd and gcd:
                c_def = (OK, f"Defesa ativa (F ~{rf:.0f} s, G ~{rg:.0f} s).")
            else:
                passos = []
                if not gcd:
                    passos.append("G (cedo)")
                if not fcd and gatilho_f:
                    passos.append("F")
                if passos:
                    acao_def = [p[0] for p in passos]
                    why = f"Ditto perdeu {queda_total * 100:.0f}%" if queda_total >= DEFESA_QUEDA else (f"lure em {lure_t:.0f} s" if lure_t is not None else "")
                    c_def = (ACAO, "USAR " + " e depois ".join(passos) + (f" ({why})." if len(passos) > 1 and why else "."))
                elif gcd:
                    c_def = (NADA, f"G ativo (~{rg:.0f} s). F: usar com {DEFESA_QUEDA * 100:.0f}% de dano ou aos {DEFESA_APOS_S:.0f} s.")
                else:
                    c_def = (NADA, "F pronto: aguardando o gatilho.")
        else:
            if queda_total >= DEFESA_QUEDA or (lure_t is not None and lure_t >= DEFESA_APOS_S):
                c_def = (ACAO, "USAR G e depois F (sem leitura das skills).")
            else:
                c_def = (NADA, "Aguardando gatilho da defesa.")

        # ================= CARTÃO CURA (C)
        if ditto is None:
            c_cura = (NADA, "—")
        elif known and cd.get("C"):
            c_cura = (NADA, f"Cura em recarga (usada há {t - self.cd_desde.get('C', t):.0f} s).")
        elif hp <= CURA_HP:
            acao_cura = bool(known)
            c_cura = (PERIGO if hp <= 0.30 else ACAO,
                      f"USAR C: Ditto em {hp * 100:.0f}%." + (" Se o R estiver perto, espere ~1 s." if c_ataque[0] == ACAO else ""))
        else:
            c_cura = (NADA, f"Ditto {hp * 100:.0f}%: cura abaixo de {CURA_HP * 100:.0f}%.")

        # ================= CARTÃO REVIVE / SEGURANÇA
        if ditto is None:
            if n_mon > 0 and sem_stun > 0:
                c_rev = (PERIGO, f"PERIGO: Ditto fora e {sem_stun} de {n_mon} monstro(s) SEM stun. FUGIR da tela.")
            elif n_mon > 0:
                c_rev = (ACAO if curto else OK, f"Revive em andamento, todos stunados (~{restante:.1f} s).")
            else:
                c_rev = (OK, "Revive em andamento, sem monstros.")
        elif n_mon == 0:
            gastas = known and (cd.get("F2") or cd.get("F") or cd.get("G") or cd.get("C"))
            c_rev = (ACAO, "Revive: resetar skills (sem monstros na tela).") if gastas else (NADA, "—")
        elif sem_stun > 0:
            c_rev = (ACAO, f"NÃO reviver: {sem_stun} de {n_mon} monstro(s) SEM stun.") if sem_stun < n_mon else (NADA, "Sem stun ainda.")
        else:
            if restante is not None and restante >= REVIVE_STUN_MIN_S and (hp <= 0.6 or (known and cd.get("F2"))):
                c_rev = (ACAO, f"Revive liberado: todos os {n_mon} stunados (~{restante:.1f} s).")
            else:
                c_rev = (OK, f"Todos os {n_mon} stunados" + (f" (~{restante:.1f} s)." if restante is not None else "."))

        # estado estruturado do revive (o bot_fase1 usa estes, não o texto)
        sem_monstros_s = (t - self.sem_monstros_desde) if self.sem_monstros_desde is not None else 0.0
        revive_tipo = None
        if ditto is not None:
            if n_mon == 0 and known and (cd.get("F2") or cd.get("F") or cd.get("G") or cd.get("C")):
                revive_tipo = "resetar"          # sem monstros: só reseta as skills
            elif n_mon > 0 and sem_stun == 0 and restante is not None and restante >= REVIVE_STUN_MIN_S:
                revive_tipo = "liberado"         # todos stunados e com stun sobrando
        cartoes = {"ATAQUE": c_ataque, "DEFESA": c_def, "CURA": c_cura, "REVIVE": c_rev}
        pior = max(cartoes.values(), key=lambda c: ORDEM_NIVEL[c[0]])
        r = {"t": round(t, 2), "cartoes": cartoes, "nivel": pior[0], "msg": pior[1],
             "n_monstros": n_mon, "n_shiny": battle["n_shiny"], "ditto_hp": hp, "ditto_presente": ditto is not None,
             "sem_stun": sem_stun, "stun_restante": restante,
             "fracao_stun": round(1.0 if n_mon <= 0 else (n_mon - sem_stun) / n_mon, 2),
             "lure_t": lure_t, "parado_for": parado_for, "no_ponto": no_ponto, "queda_total": round(queda_total, 3),
             "extensao": ext, "veloc_monstros": veloc, "coord": coord, "revive_em_curso": revive_em_curso,
             "skills_cd": cd if known else None, "n_fora": n_fora, "dist_max_fora": dist_max,
             "ditto_pos": list(dp) if dp is not None else None,
             "acao_def": acao_def, "acao_cura": acao_cura, "acao_R": c_ataque[0] == ACAO, "R_tipo": r_tipo, "n_parado": bool(parado), "area_esgotada": area_esgotada,
             "revive_tipo": revive_tipo, "sem_monstros_s": sem_monstros_s, "n_loc": len(caveiras or []),
             "stun_dbg": f"latch={'sim' if self.latch is not None else 'NÃO'} sem_stun={sem_stun}/{n_mon} aneis_max={self.janela.max_aneis(t)}"}
        self.ultimo = r
        return r


# ---------------------------------------------------------------- replay
def replay(pasta, so_mudancas=True, lures=None):
    import cv2
    pf = os.path.join(pasta, "frames")
    quadros = sorted(int(f[:6]) for f in os.listdir(pf) if f.endswith(".jpg"))
    coords, evs = {}, []
    try:
        for l in open(os.path.join(pasta, "index.jsonl"), encoding="utf-8"):
            d = json.loads(l)
            coords[d["n"]] = tuple(d["coord"]) if d.get("coord") else None
    except Exception:
        pass
    try:
        evs = [json.loads(l) for l in open(os.path.join(pasta, "eventos.jsonl"), encoding="utf-8")]
    except Exception:
        pass
    evs = [(e["t"], e.get("funcao")) for e in evs if e.get("tipo") == "atalho" and e.get("acao") == "down"]
    paradas = []
    if lures and os.path.exists(lures):
        paradas = [l["parada"] for l in json.load(open(lures, encoding="utf-8"))["lures"]]
    c, sk = Cerebro(paradas), LeitorSkills()
    ult, i_ev, i_co = None, 0, 1
    for n in quadros:
        t = (n - 1) / 10.0
        while i_co < n:                      # coordenada de TODOS os quadros (mesmo os não salvos)
            c.observar_coord((i_co - 1) / 10.0, coords.get(i_co))
            i_co += 1
        im = cv2.imread(os.path.join(pf, f"{n:06d}.jpg"))
        novos = []
        while i_ev < len(evs) and evs[i_ev][0] <= t:
            novos.append(evs[i_ev][1])
            i_ev += 1
        reg = calibrar(im) or (5, 195, 185, 835)
        b = ler_battle_list(im, reg)
        cav = achar_caveiras(im)
        dpos = achar_ditto(im, cav, b["ditto"]["hp"]) if b["ditto"] else None
        r = c.processar(t, b, achar_aneis(im), coords.get(n), cav, sk.ler(im), novos, dpos)
        chave = tuple((k, v[0], v[1][:22]) for k, v in r["cartoes"].items())
        for e in novos:
            if e in ("atacar", "revive", "cura", "defesa", "velocidade", "stun", "pokebola", "parar_ditto"):
                print(f"t={t:6.1f}   >>> USUÁRIO apertou: {e}")
        if not so_mudancas or chave != ult:
            ativos = [f"{k}[{v[0]}] {v[1]}" for k, v in r["cartoes"].items() if v[0] in (ACAO, PERIGO, CINZA)]
            print(f"t={t:6.1f}  " + (" | ".join(ativos) if ativos else "(nada a fazer)"))
            ult = chave


# ---------------------------------------------------------------- ao vivo
LARGURA, ALTURA = 194, 560          # modo compacto (largura do PNG do usuário; altura maior p/ os 4 cartões)
CORES = {NADA: "#2a2f3a", OK: "#1b8a3a", ACAO: "#c78a00", PERIGO: "#c0272d", CINZA: "#555555"}


def _afinidade(root, valor):
    """Windows 10 (2004+): 0x11 = janela some das capturas de tela; 0 = aparece."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        root.update_idletasks()
        hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
        return bool(u.SetWindowDisplayAffinity(hwnd, valor))
    except Exception:
        return False


def _sobrepostos(rect, regioes):
    x, y, w, h = rect
    return [n for n, (rx, ry, rw, rh) in regioes.items() if x < rx + rw and rx < x + w and y < ry + rh and ry < y + h]


def main_ao_vivo(compacto=True, registrar=True, lures=None):
    import tkinter as tk
    import mss
    from PIL import Image
    base = os.path.dirname(os.path.abspath(__file__))
    layout_all = {}
    try:
        with open(os.path.join(base, "layout.json"), "r", encoding="utf-8") as fh:
            layout_all = json.load(fh)
    except Exception:
        pass
    layout = layout_all.get("regioes", {})
    reg_bl = tuple(layout.get("battle_list", (5, 195, 185, 835)))
    reg_co = tuple(layout.get("coordenada", (1730, 54, 120, 14)))
    lidas = {k: tuple(v) for k, v in layout.items() if k in ("coordenada", "vida_treinador", "area_jogo")}
    configurar(0.25, range(32, 46, 4))
    paradas = []
    pasta_grav = os.path.join(base, "gravacoes")
    if lures is None and os.path.isdir(pasta_grav):
        cand = sorted((os.path.join(pasta_grav, d, "lures.json") for d in os.listdir(pasta_grav)
                       if os.path.exists(os.path.join(pasta_grav, d, "lures.json"))), reverse=True)
        lures = cand[0] if cand else None
    try:
        if lures:
            paradas = [l["parada"] for l in json.load(open(lures, encoding="utf-8"))["lures"]]
    except Exception:
        paradas = []

    pos_arq = os.path.join(base, "monitor_pos.json")
    px, py = 1920 - LARGURA - 3, 360
    try:
        with open(pos_arq, "r", encoding="utf-8") as fh:
            d = json.load(fh)
            px, py = int(d["x"]), int(d["y"])
    except Exception:
        pass

    root = tk.Tk()
    root.title("Monitor de combate")
    FUNDO, TEXTO = "#15171c", "#d8dce4"
    root.configure(bg=FUNDO)
    if compacto:
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{LARGURA}x{ALTURA}+{px}+{py}")
        root.resizable(False, False)
        f_tit, f_card, f_val, f_log, wrap = ("Arial", 7, "bold"), ("Arial", 8, "bold"), ("Consolas", 8), ("Consolas", 7), LARGURA - 18
    else:
        root.attributes("-topmost", True)
        root.geometry("620x760")
        f_tit, f_card, f_val, f_log, wrap = ("Arial", 9, "bold"), ("Arial", 12, "bold"), ("Consolas", 11), ("Consolas", 9), 560

    topo = tk.Label(root, text="Parado", font=("Arial", 8, "bold"), bg="#444", fg="white", wraplength=wrap + 10,
                    justify="left", anchor="w", padx=6, pady=3)
    topo.pack(fill="x")
    cards = {}
    for nome in ("ATAQUE", "DEFESA", "CURA", "REVIVE"):
        fr = tk.Frame(root, bg=CORES[NADA])
        fr.pack(fill="x", padx=3, pady=2)
        tt = tk.Label(fr, text=nome, font=f_tit, bg=CORES[NADA], fg="#aeb6c6", anchor="w")
        tt.pack(fill="x", padx=5)
        tx = tk.Label(fr, text="—", font=f_card, bg=CORES[NADA], fg="white", wraplength=wrap, justify="left",
                      anchor="nw", height=3 if compacto else 2)
        tx.pack(fill="x", padx=5, pady=(0, 3))
        cards[nome] = (fr, tt, tx)
    aviso = tk.Label(root, text="", font=("Arial", 7, "bold"), bg="#7a3b00", fg="white", wraplength=wrap + 10, justify="left")
    v = {k: tk.StringVar(value="-") for k in ("ditto", "mon", "stun", "lure", "skills", "fps")}
    quadro = tk.Frame(root, bg=FUNDO)
    quadro.pack(fill="x", padx=6, pady=(3, 1))
    for i, (k, nome) in enumerate((("ditto", "Ditto"), ("mon", "Monstros"), ("stun", "Stun"), ("lure", "Lure"),
                                   ("skills", "Skills"), ("fps", "Leit/s"))):
        tk.Label(quadro, text=nome, font=f_val, bg=FUNDO, fg="#8b93a3", width=8, anchor="w").grid(row=i, column=0, sticky="w")
        tk.Label(quadro, textvariable=v[k], font=f_val, bg=FUNDO, fg=TEXTO, anchor="w").grid(row=i, column=1, sticky="w")
    barra = tk.Frame(root, bg=FUNDO)
    barra.pack(side="bottom", fill="x", padx=4, pady=4)
    txt = tk.Text(root, font=f_log, bg="#0d0f13", fg=TEXTO, bd=0, wrap="word", height=3,
                  highlightthickness=0, insertbackground=TEXTO)
    txt.pack(fill="both", expand=True, padx=4, pady=2)

    estado = {"rodando": False, "r": None, "fps": 0.0, "novo": [], "excluida": False, "pausa": False,
              "usuario": [], "mon": None, "skills": None}
    fila = queue.Queue()
    pasta_log = os.path.join(base, "monitor_logs")
    pasta_prints = os.path.join(pasta_log, "prints")
    os.makedirs(pasta_prints, exist_ok=True)
    arquivo = os.path.join(pasta_log, time.strftime("%Y-%m-%d_%H-%M-%S") + ".txt")

    def conferir_sobreposicao():
        if not compacto:
            return
        rect = (root.winfo_x(), root.winfo_y(), root.winfo_width(), root.winfo_height())
        cob = _sobrepostos(rect, lidas)
        if cob and not estado["excluida"]:
            aviso.config(text="⚠ A janela cobre: " + ", ".join(cob) + ". Mova para uma área livre.")
            aviso.pack(fill="x", after=topo)
        else:
            aviso.pack_forget()

    # ---- registro dos seus apertos (só os atalhos do jogo configurados no layout.json)
    vigia = None
    if registrar:
        try:
            from atalhos import Vigia
            mapa = {}
            for fn, lista in layout_all.get("atalhos", {}).get("usuario", {}).items():
                for a in lista:
                    mapa[a] = fn
            for a, fn in (("key:f", "defesa"), ("key:g", "velocidade"), ("key:c", "cura"), ("key:f2", "stun")):
                mapa.setdefault(a, fn)

            def cb(fn, atalho, acao, pos):
                if acao == "down":
                    estado["usuario"].append((time.perf_counter(), fn))
            vigia = Vigia(mapa, cb)
            vigia.start()
        except Exception:
            vigia = None

    def laco():
        t0 = time.perf_counter()
        c = Cerebro(paradas)
        sk = LeitorSkills()
        n = 0
        ult = None
        reg = {"bl": reg_bl, "valida": False, "vazia_desde": None, "ult_calib": -9.0}
        fh = open(arquivo, "a", encoding="utf-8", buffering=1)

        def log(msg):
            linha = f"{time.perf_counter() - t0:6.1f}s {msg}"
            estado["novo"].append(linha)
            fh.write(linha + "\n")

        log(f"[info] pontos de parada carregados: {len(paradas)}" if paradas else "[info] sem pontos de parada (usa só 'personagem parado')")
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            while estado["rodando"]:
                if estado["pausa"]:
                    time.sleep(0.01)
                    continue
                arr = np.array(sct.grab(mon))[:, :, :3]
                t = time.perf_counter() - t0
                b = ler_battle_list(arr, reg["bl"])
                vazia = len(b["entradas"]) == 0
                if vazia:
                    reg["vazia_desde"] = reg["vazia_desde"] if reg["vazia_desde"] is not None else t
                    if (not reg["valida"] or t - reg["vazia_desde"] > 2.5) and t - reg["ult_calib"] > 1.0:
                        reg["ult_calib"] = t
                        nova = calibrar(arr)
                        if nova is not None:
                            if nova != reg["bl"]:
                                log(f"[info] Battle list encontrada em x={nova[0]}, y={nova[1]}")
                            reg["bl"], reg["valida"], reg["vazia_desde"] = nova, True, None
                            b = ler_battle_list(arr, nova)
                else:
                    reg["valida"], reg["vazia_desde"] = True, None
                x, y, w, h = reg_co
                co = ler_coord(arr[y:y + h, x:x + w])
                sks = sk.ler(arr)
                evs = [fn for (tt, fn) in estado["usuario"]]
                for tt, fn in estado["usuario"]:
                    if fn != "mover_ditto":
                        log(f"[eu] {fn}")
                estado["usuario"].clear()
                cav = achar_caveiras(arr)
                dpos = achar_ditto(arr, cav, b["ditto"]["hp"]) if b["ditto"] else None
                r = c.processar(t, b, achar_aneis(arr), co, cav, sks, evs, dpos)
                if not reg["valida"] and vazia:
                    r = dict(r, nivel=CINZA, msg="SEM LEITURA: não achei a Battle list.",
                             cartoes={k: (CINZA, "SEM LEITURA da Battle list. NÃO confie nas recomendações.") for k in r["cartoes"]})
                elif vazia and reg["vazia_desde"] is not None and t - reg["vazia_desde"] > 3.0:
                    r["cartoes"]["REVIVE"] = (ACAO, f"Battle list vazia há {t - reg['vazia_desde']:.0f} s. Confira se ela está na tela.")
                n += 1
                estado["r"], estado["fps"], estado["mon"], estado["skills"] = r, n / max(t, 1e-6), b, sks
                chave = tuple((k, vv[0], vv[1][:22]) for k, vv in r["cartoes"].items())
                if chave != ult:
                    ativos = [f"{k[:3]}[{vv[0][:3]}] {vv[1]}" for k, vv in r["cartoes"].items() if vv[0] in (ACAO, PERIGO, CINZA)]
                    log("[mon] " + (" | ".join(ativos) if ativos else "(nada)"))
                    ult = chave
        fh.close()

    # ---- print com a janela VISÍVEL (as capturas do monitor continuam ignorando a janela)
    def tirar_print():
        estado["pausa"] = True
        time.sleep(0.15)
        try:
            if compacto and estado["excluida"]:
                _afinidade(root, 0)
                time.sleep(0.12)
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
            nome = time.strftime("%Y-%m-%d_%H-%M-%S")
            Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX").save(os.path.join(pasta_prints, nome + ".png"))
            r = estado["r"] or {}
            dump = {k: (list(vv) if isinstance(vv, tuple) else vv) for k, vv in r.items() if k != "cartoes"}
            dump["cartoes"] = {k: list(vv) for k, vv in r.get("cartoes", {}).items()}
            dump["hora"] = nome
            dump["battle_list"] = [{"tipo": e["tipo"], "hp": e["hp"]} for e in (estado["mon"] or {}).get("entradas", [])]
            dump["skills"] = estado["skills"]
            with open(os.path.join(pasta_prints, nome + ".json"), "w", encoding="utf-8") as fj:
                json.dump(dump, fj, ensure_ascii=False, indent=1, default=str)
            estado["novo"].append(f"[print] salvo {nome}.png")
        finally:
            if compacto and estado["excluida"]:
                _afinidade(root, 0x11)
            estado["pausa"] = False

    hk = None
    try:
        from pynput import keyboard as pk
        hk = pk.GlobalHotKeys({"<ctrl>+<alt>+s": lambda: fila.put("print")})
        hk.start()
    except Exception:
        hk = None

    def tick():
        try:
            while True:
                if fila.get_nowait() == "print":
                    threading.Thread(target=tirar_print, daemon=True).start()
        except queue.Empty:
            pass
        r = estado["r"]
        if r and estado["rodando"]:
            for nome, (fr, tt, tx) in cards.items():
                nivel, msg = r["cartoes"][nome]
                for w_ in (fr, tt, tx):
                    w_.config(bg=CORES[nivel])
                tx.config(text=msg, fg="white" if nivel != NADA else "#8b93a3")
            lure = r.get("lure_t")
            pf = r.get("parado_for")
            fase = "-" if pf is None else ("PARADO %.0fs" % pf if pf >= PARADO_S else "andando")
            topo.config(text=f"Lure {lure:.0f}s | {fase}" if lure is not None else f"Sem lure | {fase}", bg="#2a2f3a")
            if estado["fps"] < 6.0 and r["t"] > 4.0:       # o anel pisca a ~5 Hz: com poucas leituras o stun falha
                topo.config(text=f"⚠ LENTO: {estado['fps']:.1f} leituras/s. Pode dar falso 'sem stun'.", bg="#7a3b00")
            d = r["ditto_hp"]
            v["ditto"].set(f"{d * 100:.0f}%" if r["ditto_presente"] else "FORA (revive)")
            v["mon"].set(f"{r['n_monstros']} (shiny {r['n_shiny']})")
            v["stun"].set(f"{r['fracao_stun'] * 100:.0f}%" + (f" ~{r['stun_restante']:.1f}s" if r["stun_restante"] is not None else ""))
            v["lure"].set((f"{lure:.0f}s" if lure is not None else "-") + (" no ponto" if r.get("no_ponto") else ""))
            cdm = r.get("skills_cd")
            v["skills"].set("?" if not cdm else " ".join(f"{k}{'·' if cdm[k] else '✓'}" for k in ("F", "G", "C", "F2")))
            v["fps"].set(f"{estado['fps']:.1f}")
        while estado["novo"]:
            txt.insert("end", estado["novo"].pop(0) + "\n")
            txt.see("end")
        root.after(100, tick)

    def alternar():
        if estado["rodando"]:
            estado["rodando"] = False
            btn.config(text="Iniciar")
            topo.config(text="Parado", bg="#444")
        else:
            estado["rodando"] = True
            btn.config(text="Parar")
            threading.Thread(target=laco, daemon=True).start()

    def fechar():
        estado["rodando"] = False
        for o in (vigia, hk):
            try:
                if o:
                    o.stop()
            except Exception:
                pass
        root.destroy()

    btn = tk.Button(barra, text="Iniciar", command=alternar, font=("Arial", 8, "bold"), width=6)
    btn.pack(side="left")
    tk.Button(barra, text="Print", command=lambda: threading.Thread(target=tirar_print, daemon=True).start(),
              font=("Arial", 8, "bold"), width=5).pack(side="left", padx=2)
    if compacto:
        tk.Button(barra, text="✕", command=fechar, font=("Arial", 8, "bold"), width=3).pack(side="right")
        tk.Label(barra, text="arraste ↑", font=("Arial", 7), bg=FUNDO, fg="#6b7384").pack(side="right", padx=3)
        arr_ = {}

        def ini(e):
            arr_["dx"], arr_["dy"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

        def mov(e):
            root.geometry(f"+{e.x_root - arr_['dx']}+{e.y_root - arr_['dy']}")

        def fim(e):
            try:
                with open(pos_arq, "w", encoding="utf-8") as fj:
                    json.dump({"x": root.winfo_x(), "y": root.winfo_y()}, fj)
            except Exception:
                pass
            conferir_sobreposicao()

        for w_ in (topo, aviso):
            w_.bind("<ButtonPress-1>", ini)
            w_.bind("<B1-Motion>", mov)
            w_.bind("<ButtonRelease-1>", fim)
        root.update_idletasks()
        estado["excluida"] = _afinidade(root, 0x11)
        conferir_sobreposicao()
    root.protocol("WM_DELETE_WINDOW", fechar)
    root.after(100, tick)
    root.mainloop()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--replay" in args:
        lur = args[args.index("--lures") + 1] if "--lures" in args else None
        replay(args[args.index("--replay") + 1], lures=lur)
    else:
        main_ao_vivo(compacto="--janela" not in args, registrar="--sem-registro" not in args,
                     lures=args[args.index("--lures") + 1] if "--lures" in args else None)
