# -*- coding: utf-8 -*-
"""
BOT FASE 1+2: defesa e cura AUTOMÁTICAS (G, F, C) e R em rajada. Revive (fase 3) só com segurança. Não aperta mover nem parar.

Usa o mesmo Cerebro do monitor_combate.py (mesmas regras) e APERTA:
    G (velocidade) e F (defesa): quando o monitor recomenda (monstro sem stun; G cedo, F com 3% de dano ou 8 s de lure)
    R (fase 2): rajada de 3 (0,25 s +-30%) quando o cartão ATAQUE do monitor manda (parado, monstros juntos, no alcance);
        NUNCA com menos de 3 monstros vivos (1 ou 2 = começo do próximo lure) nem em 'emergência' nem com monstro fora do alcance do Ditto (só parado e todos no alcance);
        repete a cada 1 s enquanto o ataque valer; máx. 12 R por lure; NÃO aperta se F2, F3 e F4 estiverem todas
        em recarga (R não adianta: só um revive reseta); segura o R por 1 s depois de um C.
    REVIVE (fase 3, tecla e): 'resetar' sem monstros há 0,6 s (2 leituras); 'cadência' com F2+F3+F4 em recarga e TODOS stunados
        com >= 3 s de stun; 'crítico' com Ditto <= 25%, C em recarga e todos stunados. NUNCA com monstro sem stun:
        nesse caso só avisa [PERIGO] com beep (o bot NÃO foge sozinho). Máx. 3 revives com monstros por lure.
    C (cura): Ditto <= 45% (se o R estiver recomendado, espera até 1,5 s, a não ser que o Ditto esteja <= 30%)
SEGURANÇA
    - Cada tecla é conferida pela barra de skills (ícone escurece). Se não escureceu em 0,7 s, repete 1 vez
      (máx. 2 tentativas por skill); se falhar, trava essa skill por 6 s e avisa.
    - Só age com leitura válida: skills legíveis, Battle list achada, Ditto presente, >= 6 leituras/s.
    - A decisão precisa se repetir em 2 leituras seguidas (evita um quadro ruim apertar tecla).
    - PARADA DE EMERGÊNCIA: Shift+F (solta tudo e encerra o bot). O 'f' enviado pelo próprio bot é ignorado
      pela parada, e o bot não envia 'f' enquanto o Shift estiver apertado.
Uso (terminal como ADMINISTRADOR, jogo na janela principal):
    python bot_fase1.py --seco        só mostra o que apertaria (não envia nada)
    python bot_fase1.py               aperta de verdade
    python bot_fase1.py --sem-revive  aperta G/F/C/R mas NÃO o revive (você revive à mão)
    python bot_fase1.py --ditto       + FASE 4: move/para o Ditto (usa o MOUSE; não mexa nele durante o lure) -- ver ditto_ctl.py
    (com --ditto o bot também REPOSICIONA o Ditto quando há monstro fora do alcance: ver alvo_ditto.reposicionar)
    python bot_fase1.py --diagnostico [print.png]   mostra o que cada leitor enxerga (não aperta nada)
    python bot_fase1.py --lures caminho\\lures.json   (opcional, pontos de parada)
Log: tela + monitor_logs\\bot_AAAA-MM-DD_HH-MM-SS.txt
Dependências: pip install opencv-python numpy pillow mss pynput
"""
import os
import sys
import json
import threading
import random
import time
import threading

import numpy as np

from monitor_combate import Cerebro, CURA_HP, CINZA, ACAO, PERIGO
from battle_list import ler_battle_list, calibrar
from detector_stun import achar_aneis, achar_caveiras, achar_ditto, configurar, _mapa_caveira, caveiras_do_mapa
from barra_skills import LeitorSkills
from leitor_coord import ler_coord

TECLA_R = "r"
TECLA_REVIVE = "e"
TECLAS = {"F": "f", "G": "g", "C": "c"}   # teclas do BOT (documento de passagem, seção 9)
CONFIRMA_S = 0.7            # prazo para o ícone escurecer depois do aperto
MAX_TENTATIVAS = 2          # por skill
TRAVA_S = 6.0               # depois de falhar 2x, não tenta de novo por isso
INTERVALO_TECLAS_S = 0.35   # mínimo entre duas teclas (G depois F)
LEITURAS_SEGUIDAS = 2       # a decisão precisa se repetir por isso
FPS_MIN = 2.0              # abaixo disso não aperta (G/F/C dependem pouco do stun; falha de stun só gera MAIS defesa)
R_POR_RAJADA = 3            # apertos de R por rajada
R_INTERVALO_S = 0.25        # entre apertos da rajada
R_PAUSA_S = 1.0             # pausa entre rajadas (confere se o macro disparou)
R_MIN_MONSTROS = 3          # só aperta R com pelo menos tantos monstros vivos (1 ou 2 = começo do próximo lure)
R_EMERG_DITTO = True         # o bot aperta R sozinho quando a vida do Ditto está acabando (ver regra em _passo_R)
R_EMERG_HP = 0.35
R_EMERG_PARADO_S = 2.0
R_MAX_LURE = 12             # máximo de R por lure
R_APOS_C_S = 1.0            # C logo antes do R atrasa o macro: segura o R depois de um C
HUMANIZAR = True            # varia o intervalo do R em +-30% (se o desempenho cair, ponha False)
REVIVE_STUN_MIN_S = 3.0     # revive com monstro vivo: o stun precisa ter pelo menos isso sobrando (o Ditto some ~1 s)
REVIVE_SEM_MONSTROS_S = 0.0 # revive só para resetar: lista vazia (todos mortos) = pode bater na hora, mas em 2 leituras seguidas
REVIVE_APOS_STUN_S = 1.0    # revive com monstros VIVOS (stunados): espera todos estarem stunados há pelo menos isso (monstro que já ativou a skill ainda causa dano)
REVIVE_CONFIRMA_S = 1.5     # prazo para o revive aparecer (Ditto some ou skills resetam)
REVIVE_MAX_TENT = 2
REVIVE_TRAVA_S = 10.0
REVIVE_ENTRE_S = 6.0        # mínimo entre dois revives
REVIVE_MAX_LURE = 3         # revives COM monstros vivos por lure
DITTO_CRITICO_HP = 0.25     # Ditto abaixo disso com C em recarga: revive (se todos stunados) em vez de deixá-lo morrer
AREA = ("F2", "F3", "F4")   # skills de área: todas em recarga = R não adianta (só o revive reseta)
CURA_ESPERA_R_S = 1.5       # adia a cura enquanto o R está recomendado (C logo antes do R atrasa o macro)
CURA_PERIGO_HP = 0.40       # abaixo disso a cura nunca espera o R e passa na frente de G e F


class Executor:
    """Decide QUAL tecla apertar agora. Sem tela, sem teclado: recebe leituras e devolve uma tecla (ou None)."""

    def __init__(self, enviar, log):
        self.enviar, self.log = enviar, log
        self.st = {k: {"tent": 0, "t_envio": None, "trava": 0.0, "motivo": ""} for k in TECLAS}
        self.visto = {k: 0 for k in TECLAS}
        self.t_ult_tecla = -99.0
        self.cura_quer_desde = None
        self.t_C = -99.0
        self.r_restam, self.r_prox, self.r_pausa_ate = 0, 0.0, 0.0
        self.r_count, self.r_visto, self.r_snap, self.r_limite_logado = 0, 0, None, False
        self.r_aviso_t = -99.0
        self.hp_hist = []              # (t, hp do Ditto) dos últimos ~2,5 s
        self.emerg_flag = False
        self.parado_s = None           # segundos com o personagem parado (o main preenche pela coordenada)
        self.emerg_txt = ""
        self.t_fora = -99.0            # última vez que vimos monstro FORA do alcance (com o Ditto localizado)
        self.r_bloq = None             # texto se o Ditto está se movendo/assentando (o main preenche a cada quadro)
        self.ausente = False
        self.revive_on = True
        self.rev = {"tent": 0, "t_envio": None, "trava": 0.0, "ult": -99.0, "count": 0, "snap": None}
        self.rev_aviso_t = self.perigo_t = -99.0
        self.rev_vazio = 0
        self.stun_garantido_ate = -99.0
        self.stun_todos_desde, self.stun_todos_n = None, 0   # desde quando TODOS os monstros vivos estão confirmados stunados

    def _avisar_perigo(self, t, r):
        if r["cartoes"]["REVIVE"][0] == PERIGO and t - self.perigo_t > 3.0:
            self.perigo_t = t
            if t < self.stun_garantido_ate:     # apertamos o revive com TODOS stunados: o stun dura mais um tempo
                self.log(f"[revive] sem alarme: todos estavam stunados quando o revive foi apertado "
                         f"(stun garantido por mais ~{self.stun_garantido_ate - t:.1f} s). Leitura do stun durante o revive: {r.get('stun_dbg', '')}")
                return
            self.log(f"[PERIGO] {r['cartoes']['REVIVE'][1]}  >>> O BOT NÃO FOGE SOZINHO: FUJA VOCÊ <<< ({r.get('stun_dbg', '')})")
            try:
                import winsound
                winsound.Beep(1400, 350)
            except Exception:
                pass

    def _passo_revive(self, t, r, cd):
        """Revive (tecla e). Três casos, todos com o Ditto presente e leitura válida:
          resetar : sem monstros há >= 1,5 s e alguma skill gasta
          cadencia: todos stunados com >= 3 s de stun E F2, F3, F4 todas em recarga (o R não adianta mais)
          critico : todos stunados com >= 3 s de stun, Ditto <= 25% e C em recarga
        Com monstro SEM stun: nunca (fica só o aviso)."""
        rv = self.rev
        if r.get("lure_t") is None:
            rv["count"] = 0
        tipo = None
        todos = r.get("n_monstros", 0) > 0 and r.get("sem_stun") == 0          # todos os vivos confirmados stunados
        if not todos:
            self.stun_todos_desde, self.stun_todos_n = None, 0
        elif self.stun_todos_desde is None or r["n_monstros"] > self.stun_todos_n:   # começou, ou chegou monstro novo: recomeça
            self.stun_todos_desde, self.stun_todos_n = t, r["n_monstros"]
        idade_stun = (t - self.stun_todos_desde) if self.stun_todos_desde is not None else 0.0
        vazio = r.get("revive_tipo") == "resetar" and r.get("sem_monstros_s", 0) >= REVIVE_SEM_MONSTROS_S
        self.rev_vazio = self.rev_vazio + 1 if vazio else 0
        if self.rev_vazio >= LEITURAS_SEGUIDAS:
            tipo = "resetar"
        elif r.get("revive_tipo") == "liberado" and (r.get("stun_restante") or 0) >= REVIVE_STUN_MIN_S:
            if idade_stun < REVIVE_APOS_STUN_S:          # monstro stunado ainda pode bater se já ativou a skill: espera 1 s
                if t - self.rev_aviso_t > 4.0:
                    self.rev_aviso_t = t
                    self.log(f"[REVIVE] esperando {REVIVE_APOS_STUN_S:.0f} s depois do stun de todos (faz {idade_stun:.1f} s)")
            elif r.get("area_esgotada"):
                tipo = "cadencia"
            elif (r["ditto_hp"] or 1.0) <= DITTO_CRITICO_HP and cd.get("C"):
                tipo = "critico"
        elif r.get("area_esgotada") and r["n_monstros"] > 0 and t - self.rev_aviso_t > 4.0:
            self.rev_aviso_t = t
            self.log(f"[REVIVE] aguardando (skills de área esgotadas): {r['cartoes']['REVIVE'][1]}")
        if tipo is None:
            return None
        if tipo != "resetar" and rv["count"] >= REVIVE_MAX_LURE:
            if t - self.rev_aviso_t > 4.0:
                self.rev_aviso_t = t
                self.log(f"[limite] {REVIVE_MAX_LURE} revives com monstros neste lure: bot não revive mais. Revive à mão se precisar.")
            return None
        if t < rv["trava"] or t - rv["ult"] < REVIVE_ENTRE_S or rv["t_envio"] is not None:
            return None
        rv["tent"] += 1
        rv["t_envio"], rv["snap"] = t, {k: bool(v) for k, v in cd.items()}
        if tipo != "resetar" and rv["tent"] == 1:
            rv["count"] += 1
        self.t_ult_tecla = t
        rest = r.get("stun_restante")
        if tipo != "resetar" and rest is not None:
            self.stun_garantido_ate = t + max(0.0, rest - 0.5)     # margem de 0,5 s
        rest_s = f"~{rest:.1f} s" if rest is not None else "?"
        hp_s = f"{(r['ditto_hp'] or 0) * 100:.0f}%"
        mot = {"resetar": "sem monstros, resetar skills",
               "cadencia": f"F2+F3+F4 em recarga, todos stunados ({rest_s}): resetar e bater R de novo",
               "critico": f"Ditto {hp_s} e C em recarga, todos stunados ({rest_s})"}[tipo]
        self.log(f"[tecla] REVIVE ('{TECLA_REVIVE}') tentativa {rv['tent']}/{REVIVE_MAX_TENT}: {mot}")
        self.enviar(TECLA_REVIVE)
        return "E"

    def _passo_R(self, t, r, cd, shift):
        """Rajada de R: só quando o cartão ATAQUE manda E as skills de área não estão esgotadas."""
        if r.get("lure_t") is None:
            self.r_count, self.r_limite_logado = 0, False
        tipo = r.get("R_tipo")
        # R de EMERGÊNCIA do bot: a vida do Ditto está acabando (se ele morre, o lure vira risco para o treinador). Só com o
        # personagem parado há >= 3 s, Ditto <= 35%, a maioria (>= 60%) no alcance e o Ditto parado e visto. Sobram os de fora.
        emerg_ok = False
        bloq_forte = False
        if (R_EMERG_DITTO and r.get("acao_R") and tipo in ("emergencia", "espera_fora") and not r.get("area_esgotada")
                and r.get("n_monstros", 0) >= R_MIN_MONSTROS and r.get("ditto_pos") is not None):
            hp_d = r.get("ditto_hp")
            parado_s = self.parado_s if self.parado_s is not None else (R_EMERG_PARADO_S if r.get("n_parado") else 0.0)
            n_dentro = r.get("n_loc", 0) - r.get("n_fora", 0)
            prev = 1.0                                          # vida prevista em 2 s pela TENDÊNCIA (16 Gogoats tiram 70% em ~2 s)
            if hp_d is not None and len(self.hp_hist) >= 2:
                t_max, h_max = max(((tt, h) for tt, h in self.hp_hist if t - tt <= 2.0), key=lambda x: x[1], default=(t, hp_d))
                if t - t_max >= 0.3 and h_max > hp_d:
                    prev = hp_d - (h_max - hp_d) / (t - t_max) * 2.0
                else:
                    prev = hp_d
            critico = hp_d is not None and (hp_d <= R_EMERG_HP or (hp_d <= 0.65 and prev <= 0.10))
            muito_critico = hp_d is not None and (hp_d <= 0.2 or prev <= 0.05)
            minimo = max(R_MIN_MONSTROS, (0.4 if muito_critico else 0.6) * r["n_monstros"])
            bloq_forte = self.r_bloq == "Ditto em movimento"    # esperar 'assentar'/'ver o Ditto' custa vida: só o movimento bloqueia
            if critico and parado_s >= R_EMERG_PARADO_S and not bloq_forte and n_dentro >= minimo:
                emerg_ok = True
                self.emerg_txt = (f"⚠ EMERGÊNCIA: Ditto {hp_d * 100:.0f}% (previsto {max(prev, 0) * 100:.0f}% em 2 s), parado há {parado_s:.0f} s, "
                                  f"{n_dentro} de {r['n_monstros']} no alcance; sobram {r['n_monstros'] - n_dentro}")
        self.emerg_flag = emerg_ok
        if (r.get("acao_R") and tipo in ("emergencia", "espera_fora") and not r.get("area_esgotada") and not emerg_ok
                and t - self.r_aviso_t > 4.0):
            self.r_aviso_t = t
            self.log(f"[R] NÃO apertado ({'emergência: ainda não parou/juntou' if tipo == 'emergencia' else 'monstros fora do alcance do Ditto'}): "
                     f"{r['cartoes']['ATAQUE'][1]} O bot só aperta R com o personagem parado e todos no alcance.")
        poucos = r.get("n_monstros", 0) < R_MIN_MONSTROS
        if (r.get("acao_R") and tipo in ("curso", "normal") and poucos and not r.get("area_esgotada")
                and t - self.r_aviso_t > 4.0):
            self.r_aviso_t = t
            self.log(f"[R] NÃO apertado: só {r['n_monstros']} monstro(s) vivo(s) (mínimo {R_MIN_MONSTROS}): "
                     "provavelmente começo do próximo lure.")
        quer = (bool(r.get("acao_R")) and not r.get("area_esgotada") and tipo in ("curso", "normal") and not poucos) or emerg_ok
        if r.get("ditto_pos") is not None and r.get("n_fora", 0) > 0:
            self.t_fora = t
        if quer and r.get("ditto_pos") is None and t - self.t_fora < 6.0:
            if t - self.r_aviso_t > 4.0:
                self.r_aviso_t = t
                self.log(f"[R] aguardando: Ditto não localizado e havia monstro fora do alcance há {t - self.t_fora:.1f} s "
                         "(o 'monstros juntos' sem o Ditto não prova que todos estão no alcance).")
            quer = False
        if quer and self.r_bloq and not (emerg_ok and not bloq_forte):          # emergência só respeita o Ditto EM MOVIMENTO
            if t - self.r_aviso_t > 4.0:
                self.r_aviso_t = t
                self.log(f"[R] aguardando: {self.r_bloq} (o R só sai com o Ditto parado e visto na posição final).")
            quer = False
        self.r_visto = self.r_visto + 1 if quer else 0
        if self.r_snap is not None and any(cd.get(k) and not self.r_snap.get(k) for k in AREA):
            nova = [k for k in AREA if cd.get(k) and not self.r_snap.get(k)]
            self.log(f"[ok] R disparou o macro: {'+'.join(nova)} entrou em recarga")
            self.r_snap = None
        if self.r_restam > 0 and not quer:
            self.log(f"[R] rajada cancelada (sobraram {self.r_restam}): ataque não recomendado agora"
                     + (" (skills de área esgotadas)" if r.get("area_esgotada") else ""))
            self.r_restam = 0
        if quer and r.get("area_esgotada") is False and self.r_count >= R_MAX_LURE and not self.r_limite_logado:
            self.r_limite_logado = True
            self.log(f"[limite] {R_MAX_LURE} R neste lure: bot não aperta mais R. Aperte à mão se precisar.")
        if t - self.t_C < R_APOS_C_S or shift:
            return None
        if self.r_restam == 0:
            if not (quer and self.r_visto >= (1 if emerg_ok else LEITURAS_SEGUIDAS) and t >= self.r_pausa_ate and self.r_count < R_MAX_LURE):
                return None
            self.r_restam, self.r_snap = R_POR_RAJADA, {k: bool(cd.get(k)) for k in AREA}
            self.log(f"[R] rajada de {R_POR_RAJADA}: {r['cartoes']['ATAQUE'][1]} "
                     f"[monstros: {r['n_monstros']} na lista, {r.get('n_loc', '?')} localizados na tela]"
                     + (f" {self.emerg_txt}" if emerg_ok else ""))
        elif t < self.r_prox:
            return None
        self.r_restam -= 1
        self.r_count += 1
        self.r_prox = t + R_INTERVALO_S * (random.uniform(0.7, 1.3) if HUMANIZAR else 1.0)
        if self.r_restam == 0:
            self.r_pausa_ate = t + R_PAUSA_S
        self.log(f"[tecla] R ('{TECLA_R}') {R_POR_RAJADA - self.r_restam}/{R_POR_RAJADA} (total no lure: {self.r_count}/{R_MAX_LURE})")
        self.enviar(TECLA_R)
        return "R"

    def _quer(self, r):
        q = {"G": "G" in r["acao_def"], "F": "F" in r["acao_def"], "C": bool(r["acao_cura"])}
        for k in TECLAS:
            self.visto[k] = self.visto[k] + 1 if q[k] else 0
        return {k: self.visto[k] >= LEITURAS_SEGUIDAS for k in TECLAS}

    def passo(self, t, r, fps, shift_apertado=False):
        if r.get("ditto_hp") is not None:
            self.hp_hist = [(tt, h) for tt, h in self.hp_hist if t - tt <= 2.5] + [(t, r["ditto_hp"])]
        cd = r.get("skills_cd")
        valido = (cd is not None and r["nivel"] != CINZA and r["ditto_presente"]
                  and not r["revive_em_curso"] and fps >= FPS_MIN)
        if not r["ditto_presente"] or r["revive_em_curso"]:
            self.ausente = True
        elif self.ausente:                                    # Ditto voltou do revive: ciclo novo
            self.ausente = False
            self.r_count, self.r_restam, self.r_visto, self.r_snap, self.r_limite_logado = 0, 0, 0, None, False
            self.r_pausa_ate = t + 1.0
        quer = self._quer(r) if valido else {k: False for k in TECLAS}
        if not valido:
            self.visto = {k: 0 for k in TECLAS}
            self.cura_quer_desde = None
        # ---- confirmação (ícone escureceu) e repetição
        for k, s in self.st.items():
            if cd is None or s["t_envio"] is None:
                continue
            if cd.get(k):
                self.log(f"[ok] {k} confirmado pela barra em {t - s['t_envio']:.2f} s")
                s["tent"], s["t_envio"] = 0, None
            elif t - s["t_envio"] > CONFIRMA_S and s["tent"] >= MAX_TENTATIVAS:
                self.log(f"[FALHOU] {k}: {s['tent']} tentativas sem confirmar. Trava {TRAVA_S:.0f} s. Aperte à mão se precisar.")
                s["trava"], s["tent"], s["t_envio"] = t + TRAVA_S, 0, None
        rv = self.rev
        if rv["t_envio"] is not None:
            reset = cd is not None and rv["snap"] and any(v and not cd.get(k) for k, v in rv["snap"].items())
            if not r["ditto_presente"] or r["revive_em_curso"] or reset:
                self.log(f"[ok] revive confirmado ({'Ditto sumiu' if not r['ditto_presente'] or r['revive_em_curso'] else 'skills resetadas'}) em {t - rv['t_envio']:.2f} s")
                rv["t_envio"], rv["tent"], rv["ult"] = None, 0, t
            elif t - rv["t_envio"] > REVIVE_CONFIRMA_S:
                if rv["tent"] >= REVIVE_MAX_TENT:
                    self.log(f"[FALHOU] revive: {rv['tent']} tentativas sem confirmar. Trava {REVIVE_TRAVA_S:.0f} s. Revive à mão se precisar.")
                    rv["trava"], rv["tent"] = t + REVIVE_TRAVA_S, 0
                rv["t_envio"] = None
        self._avisar_perigo(t, r)          # antes do 'valido': o perigo é justamente com o Ditto fora
        if not valido:
            self.r_restam, self.r_visto = 0, 0
            return None
        c_urgente = (quer["C"] and not cd.get("C") and (r["ditto_hp"] or 1.0) <= CURA_PERIGO_HP
                     and t >= self.st["C"]["trava"] and self.st["C"]["t_envio"] is None)
        if not c_urgente and self.revive_on and self._passo_revive(t, r, cd):
            return "E"
        if not c_urgente and self._passo_R(t, r, cd, shift_apertado):   # cura urgente passa na frente do R
            return "R"
        if t - self.t_ult_tecla < INTERVALO_TECLAS_S:
            return None
        # ---- cura: adia o C se o R está recomendado (exceto perigo)
        cura_ok = quer["C"]
        if cura_ok:
            if self.cura_quer_desde is None:
                self.cura_quer_desde = t
            if (r["cartoes"]["ATAQUE"][0] == ACAO and (r["ditto_hp"] or 1.0) > CURA_PERIGO_HP
                    and t - self.cura_quer_desde < CURA_ESPERA_R_S):
                cura_ok = False
        else:
            self.cura_quer_desde = None
        ordem = [("C", cura_ok and (r["ditto_hp"] or 1.0) <= CURA_PERIGO_HP), ("G", quer["G"]), ("F", quer["F"]), ("C", cura_ok)]
        for k, ok in ordem:
            s = self.st[k]
            if not ok or cd.get(k) or t < s["trava"]:
                continue
            if s["t_envio"] is not None and t - s["t_envio"] <= CONFIRMA_S:
                continue                                  # esperando a confirmação
            if TECLAS[k] == "f" and shift_apertado:
                continue                                  # evita virar Shift+F (emergência)
            s["tent"] += 1
            s["t_envio"] = t
            self.t_ult_tecla = t
            motivo = {"G": "monstro sem stun (G cedo)", "F": f"Ditto -{r['queda_total'] * 100:.0f}% / lure {r['lure_t'] or 0:.0f} s",
                      "C": f"Ditto {((r['ditto_hp'] or 0) * 100):.0f}%"}[k]
            self.log(f"[tecla] {k} ('{TECLAS[k]}') tentativa {s['tent']}/{MAX_TENTATIVAS}: {motivo}")
            if k == "C":
                self.t_C = t
            self.enviar(TECLAS[k])
            return k
        return None


def ler_visao(arr, b, pool):
    """Caveiras, anéis de stun e posição do Ditto. Sem monstros na Battle list nada disso é usado (o Cerebro só
    olha stun com monstro vivo), então pula: é a parte pesada (~400 ms). Com monstros: o mapa das caveiras é
    calculado 1 vez e os anéis rodam em paralelo (mesmos resultados de antes)."""
    if b["n_monstros"] == 0:
        return [], [], None
    fa = pool.submit(achar_aneis, arr)
    mapa = _mapa_caveira(arr)
    cav = caveiras_do_mapa(mapa)
    dpos = achar_ditto(arr, cav, b["ditto"]["hp"], mapa) if b["ditto"] else None
    return cav, fa.result(), dpos


def main(seco=False, lures=None, revive=True, ditto=False, andar=False, sem_hum=False):
    import mss
    from pynput import keyboard as pk
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        layout_all = json.load(open(os.path.join(base, "layout.json"), encoding="utf-8"))
    except Exception:
        layout_all = {}
    layout = layout_all.get("regioes", {})
    reg_bl = tuple(layout.get("battle_list", (5, 195, 185, 835)))
    reg_co = tuple(layout.get("coordenada", (1730, 54, 120, 14)))
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

    os.makedirs(os.path.join(base, "monitor_logs"), exist_ok=True)
    fh = open(os.path.join(base, "monitor_logs", "bot_" + time.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"),
              "a", encoding="utf-8", buffering=1)
    t0 = time.perf_counter()

    def log(msg):
        linha = f"{time.perf_counter() - t0:7.1f}s {msg}"
        print(linha, flush=True)
        try:
            fh.write(linha + "\n")
        except ValueError:                  # thread do Player logando depois de o bot fechar o arquivo
            pass

    ctl = pk.Controller()
    est = {"shift": False, "parar": False, "ult_envio": {}, "usuario": []}

    def enviar(tecla):
        if seco:
            log(f"   (seco) apertaria '{tecla}'")
            return
        est["ult_envio"][tecla] = time.perf_counter()
        ctl.press(tecla)
        time.sleep(0.04)
        ctl.release(tecla)
        if tecla == TECLA_R:
            est["usuario"].append("atacar")      # o Cerebro entende como 'rajada em curso'
        elif tecla == TECLA_REVIVE:
            est["usuario"].append("revive")      # ciclo novo para o Cerebro


    # ---- parada de emergência Shift+F (ignora o 'f' do próprio bot)
    def e_shift(k):
        return k in (pk.Key.shift, pk.Key.shift_l, pk.Key.shift_r)

    def on_press(k):
        if e_shift(k):
            est["shift"] = True
        elif est["shift"] and getattr(k, "char", None) and k.char.lower() == "f":
            if time.perf_counter() - est["ult_envio"].get("f", -9) > 0.15:
                est["parar"] = True
                pl = est.get("player")
                if pl is not None:                          # para de andar NA HORA (sem esperar o próximo quadro)
                    pl.parar.set()
                    try:
                        pl.teclas.soltar_tudo()
                    except Exception:
                        pass

    def on_release(k):
        if e_shift(k):
            est["shift"] = False

    ls = pk.Listener(on_press=on_press, on_release=on_release)
    ls.start()
    try:                                                   # seus apertos de R e revive (o Cerebro usa)
        from atalhos import Vigia
        mapa = {"key:r": "atacar", "key:e": "revive", "key:space": "pokebola"}
        for fn in ("atacar", "revive"):
            for a in layout_all.get("atalhos", {}).get("usuario", {}).get(fn, []):
                mapa[a] = fn

        def cb(fn, atalho, acao, pos):
            if acao == "down" and fn in ("atacar", "revive"):
                est["usuario"].append(fn)
        vigia = Vigia(mapa, cb)
        vigia.start()
    except Exception:
        vigia = None

    c, sk, ex = Cerebro(paradas), LeitorSkills(), Executor(enviar, log)
    ex.revive_on = revive
    dctl = None
    ctl_prev = {"ausente": False}
    if ditto:                                              # fase 4: mover/parar o Ditto (usa o MOUSE)
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # coordenadas do mouse em pixels reais
        except Exception:
            pass
        from pynput import mouse as pm
        from ditto_ctl import DittoCtl, ditto_perto, CROP_BOX
        alvo = None
        try:                                                # alvo do O pelo mapa de obstáculos + rota do laço
            from alvo_ditto import AlvoDitto
            from editor_mapa import ObstacleMap
            from player_lures import montar_rota
            obst = ObstacleMap.load(os.path.join(base, "mapa_obstaculos.json"))
            rota = montar_rota(json.load(open(lures, encoding="utf-8")))
            alvo = AlvoDitto(obst, rota)
            n_fim = sum(1 for lu in rota if lu.get("ditto_fim"))
            log(f"[ditto] alvo do O pelo MAPA + ROTA ({len(rota)} lures, {lures}).")
            log(f"[ditto] ditto_fim GRAVADO em {n_fim}/{len(rota)} lures" + ("" if n_fim else " (nenhum: uso a regra automática de antes)")
                + (": " + ", ".join(f"{k + 1}->{lu['ditto_fim']['tile']} ({lu['ditto_fim']['dist_tiles']} t)" for k, lu in enumerate(rota) if lu.get("ditto_fim") and lu["ditto_fim"].get("tile")) if n_fim else ""))
        except Exception as e:
            log(f"[ditto] sem mapa/rota ({e}): o alvo do O será N tiles à frente do rumo do personagem.")
        mouse_ctl, trava_teclas = pm.Controller(), threading.Lock()

        def tecla_ditto(k):
            if seco:
                return                                      # no modo seco só loga P (o O a 4,5 Hz encheria a tela)
            with trava_teclas:
                ctl_teclar(k)

        def mouse_ditto(p):
            if not seco:
                mouse_ctl.position = p

        def ctl_teclar(k):
            ctl_kb.press(k)
            time.sleep(0.03)
            ctl_kb.release(k)
        ctl_kb = pk.Controller()
        dctl = DittoCtl(tecla_ditto, mouse_ditto, lambda m: log(m + (" (seco)" if seco and "P (" in m else "")), alvo=alvo)
        log("[ditto] fase 4 LIGADA: o bot vai MOVER O MOUSE e apertar 'o'/'p'. Não use o mouse durante o lure.")

        def thread_ditto():
            import mss as _mss
            x, y, w, h = reg_co
            with _mss.mss() as s2:
                def ver_perto():
                    box = {"left": CROP_BOX[0], "top": CROP_BOX[1], "width": CROP_BOX[2] - CROP_BOX[0],
                           "height": CROP_BOX[3] - CROP_BOX[1]}
                    return ditto_perto(np.array(s2.grab(box))[:, :, :3], dctl.hp)
                dctl.ver_perto = ver_perto
                while not est["parar"] and not est.get("fim"):
                    try:
                        a2 = np.array(s2.grab({"left": x, "top": y, "width": w, "height": h}))[:, :, :3]
                        co2 = ler_coord(a2)
                        if co2:
                            dctl.passo(time.perf_counter() - t0, co2)
                    except Exception:
                        pass
                    time.sleep(0.025)
        threading.Thread(target=thread_ditto, daemon=True).start()
    # ---- FASE 5 / etapa 1: o Player (player_lures.py) anda o laço e fica na parada até o PORTÃO abrir. Só em --seco.
    player, portao = None, None
    if andar:
        from portao import Portao
        from player_lures import Player, MundoReal, TeclasSeco, TeclasPynput, Relogio, montar_rota
        from editor_mapa import ObstacleMap
        obst_p = ObstacleMap.load(os.path.join(base, "mapa_obstaculos.json"))
        rota_p = montar_rota(json.load(open(lures, encoding="utf-8")))
        portao = Portao(log)
        mundo_p = MundoReal(reg_co)                         # mesma região da coordenada que o bot lê (layout.json)
        mundo_p.iniciar()
        player = Player(obst_p, rota_p, mundo_p, TeclasSeco() if seco else TeclasPynput(), Relogio(1.0), nivel_hum=0, seco=seco,
                        tol_parada=2 if seco else 0, log=lambda m: log("[andar] " + m),
                        portao=lambda _agora: portao.estado(time.perf_counter() - t0))
        est["player"] = player                              # Shift+F para o Player na hora (ver on_press)

        def thread_player():
            if not seco:
                log("[andar] REAL: vou apertar WASD. Clique na janela do jogo. Começando em 5 s (Shift+F para tudo)...")
                for _ in range(50):
                    if est["parar"] or est.get("fim"):
                        return
                    time.sleep(0.1)
            res = player.rodar()
            player.teclas.soltar_tudo()
            log(f"[andar] Player terminou: {res}")
            if res != "PARADO" and not est["parar"] and not est.get("fim") and not seco:
                log("[andar] !!! O PLAYER PAROU SOZINHO (pode ser no meio do lure, com monstros atrás). ASSUMA O PERSONAGEM AGORA !!!")
                try:
                    import winsound
                    for _ in range(4):
                        winsound.Beep(1800, 400)
                except Exception:
                    pass
        threading.Thread(target=thread_player, daemon=True).start()
        log(f"[andar] {'SECO: só mostra o que faria. Você anda à mão.' if seco else 'REAL: o Player ANDA o laço (WASD). Fique de olho.'} ({len(rota_p)} lures)")
    reg = {"bl": reg_bl, "valida": False, "vazia_desde": None, "ult_calib": -9.0}
    log(f"BOT FASE 1 {'(SECO: não envia nada) ' if seco else ''}iniciado. Parada de emergência: Shift+F.")
    log("[versao] fase 5.2: ditto_fim gravado + revive 1 s após stun + --andar real/seco")
    if sem_hum:                                           # desliga TODA variação: R no ritmo fixo, Ditto no tile exato gravado
        global HUMANIZAR
        HUMANIZAR = False
        import alvo_ditto
        alvo_ditto.JITTER_P = 0.0
        log("[hum] SEM humanização: R em intervalo fixo, Ditto no tile gravado exato, Player sem variação (parada exata).")
    else:
        log("[hum] humanização LIGADA: R ±30% e Ditto ±1 tile em ~40% dos lures (Player andando: sem variação).")
    n, ult_aviso, ult_erro = 0, 0.0, -9.0
    fora_desde, ult_sem_plano, pred_feita, ultimo_tipo = {}, -9.0, False, None
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(1)
    marcas = deque()            # instantes das últimas leituras (fps dos últimos ~3 s)
    t_vis = deque(maxlen=30)    # ms gastos na visão (caveiras/anéis) quando há monstros
    try:
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            cal = calibrar(np.array(sct.grab(mon))[:, :, :3])
            if cal is not None:
                reg["bl"], reg["valida"] = cal, True
            log(f"[info] Battle list: {'achada em x=%d, y=%d' % cal[:2] if cal else 'NÃO achada (lista vazia ou fora da tela)'}")
            while not est["parar"]:
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
                            reg["bl"], reg["valida"], reg["vazia_desde"] = nova, True, None
                            b = ler_battle_list(arr, nova)
                else:
                    reg["valida"], reg["vazia_desde"] = True, None
                x, y, w, h = reg_co
                co = ler_coord(arr[y:y + h, x:x + w])
                sks = sk.ler(arr)
                evs, est["usuario"] = list(est["usuario"]), []
                tv = time.perf_counter()
                cav, aneis, dpos = ler_visao(arr, b, pool)
                if b["n_monstros"]:
                    t_vis.append((time.perf_counter() - tv) * 1000)
                r = c.processar(t, b, aneis, co, cav, sks, evs, dpos)
                if not reg["valida"] and vazia:
                    r = dict(r, nivel=CINZA)
                n += 1
                marcas.append(t)
                while marcas and t - marcas[0] > 3.0:
                    marcas.popleft()
                fps = (len(marcas) - 1) / max(t - marcas[0], 1e-6) if len(marcas) > 3 else 99.0
                if t > 4.0 and fps < FPS_MIN and t - ult_aviso > 5:
                    ult_aviso = t
                    vm = f", visão {sum(t_vis) / len(t_vis):.0f} ms/quadro" if t_vis else ""
                    log(f"[aviso] só {fps:.1f} leituras/s{vm}: bot NÃO aperta nada abaixo de {FPS_MIN:.0f}.")
                if sks is None and t - ult_aviso > 5:
                    ult_aviso = t
                    log("[aviso] não consegui ler a barra de skills: bot NÃO aperta nada.")
                try:
                    if portao is not None:
                        portao.atualizar(t, r, fps >= FPS_MIN and sks is not None)
                    if dctl is not None:
                        ex.r_bloq = dctl.bloqueia_R(t)
                        ex.parado_s = dctl.parado_ha(t)           # R só com o Ditto parado e visto na posição final
                    ex.passo(t, r, fps, est["shift"])
                    if dctl is not None:
                        livre = r["ditto_presente"] and not r["revive_em_curso"]
                        dctl.renovar(t, livre and r["nivel"] != CINZA and not est["shift"], r["ditto_hp"], r["n_monstros"])
                        if not livre:
                            ctl_prev["ausente"] = True
                        elif ctl_prev["ausente"]:               # Ditto voltou do revive (do bot ou seu)
                            ctl_prev["ausente"] = False
                            dctl.revive_voltou(t, r["n_monstros"])
                        if dpos is not None:
                            dctl.visto_ditto(t)
                        if dctl.consumir_esquecer():
                            c.esquecer_ditto()                   # posição guardada do Ditto (1,5 s) deixou de valer
                        # AJUSTE do Ditto: 1º MERGULHO no grupo (tile que põe mais monstros na área), 2º RETIRADA para o aberto; alterna se o
                        # anterior não resolveu. ANTECIPADO: 0,8 s depois de parar, se a área tem obstáculos demais para a lista.
                        # REATIVO: monstro parado fora da área há 1,0 s (0,6 s se o Ditto já perde vida).
                        if dctl.parado_ha(t) < 0.3:
                            pred_feita = False
                        if (alvo is not None and dctl.parado_ha(t) >= 0.8 and r["n_monstros"] >= R_MIN_MONSTROS
                                and not r["area_esgotada"] and r.get("R_tipo") != "curso" and not ex.emerg_flag):
                            d_ref = dpos if dpos is not None else (tuple(r["ditto_pos"]) if r.get("ditto_pos") else None)
                            if d_ref is not None and dctl.ult_coord is not None:
                                mon_off = [((cv[0] - 959) / 72.3, (cv[1] - 452) / 72.3) for cv in cav]
                                d_off = ((d_ref[0] - 959) / 72.3, (d_ref[1] - 452) / 72.3)
                                M_t, D_t = alvo.tiles(dctl.ult_coord, mon_off, d_off)
                                fora_t = [m for m in M_t if not alvo.na_area(m, D_t)]
                                fora_desde = {m: fora_desde.get(m, t) for m in fora_t}          # tile -> desde quando está ali
                                perigo_ditto = (r["ditto_hp"] or 1.0) < 0.7 or r["queda_total"] >= 0.08
                                limite = 0.6 if perigo_ditto else 1.0
                                parados = [m for m, desde in fora_desde.items() if t - desde >= limite]
                                if dctl.pode_reposicionar(t) and (parados or not pred_feita):
                                    reativo = bool(parados)
                                    pred_feita = True
                                    if reativo:
                                        # 1º MERGULHO no grupo (a área passa a cobrir todos); se não der, RETIRADA para o aberto.
                                        # Se o ajuste anterior não resolveu, troca de estratégia.
                                        ordem = ("retirada", "mergulho") if ultimo_tipo == "mergulho" else ("mergulho", "retirada")
                                        res = alvo.ajuste_lure(dctl.ult_coord, mon_off, d_off, r["n_monstros"], ordem)
                                    else:
                                        # ANTECIPADO: área com obstáculos que não comporta a lista -> sai para o aberto ANTES de os monstros chegarem
                                        cap0 = alvo.capacidade(dctl.ult_coord, D_t)
                                        if cap0 >= r["n_monstros"] + 1:
                                            res = (None, f"a área comporta ({cap0} tiles para {r['n_monstros']} monstros)", None)
                                        else:
                                            rr = alvo.retirada(dctl.ult_coord, mon_off, d_off, r["n_monstros"], exigir_cap=r["n_monstros"] + 1)
                                            res = rr + ("retirada",) if rr[0] is not None else (None, f"área aperta ({cap0} tiles): {rr[1]}", None)
                                    if res[0] is not None:
                                        ultimo_tipo = res[4]
                                        dctl.pedir_reposicionar(t, res[0], res[1], res[2], res[3] + f" [Ditto {((r['ditto_hp'] or 0) * 100):.0f}%]")
                                        fora_desde = {}
                                    elif t - ult_sem_plano > 5.0:
                                        ult_sem_plano = t
                                        log(f"[ditto] {'ajuste' if reativo else 'antecipação'}: {res[1]}." + (" Resolva à mão se precisar." if reativo else ""))
                        else:
                            fora_desde = {}
                except Exception as e:                      # um erro de leitura não derruba o bot nem aperta tecla
                    if t - ult_erro > 2.0:
                        ult_erro = t
                        import traceback
                        log("[ERRO] " + traceback.format_exc().strip().splitlines()[-1] + " (quadro ignorado; bot segue sem apertar nada nele)")
    except KeyboardInterrupt:
        pass
    finally:
        for tcl in TECLAS.values():
            try:
                ctl.release(tcl)
            except Exception:
                pass
        est["fim"] = True
        if player is not None:
            player.parar.set()
            try:
                player.teclas.soltar_tudo()
                player.mundo.parar()
            except Exception:
                pass
        if dctl is not None:
            dctl.desligar()
        log("PARADO (emergência Shift+F)." if est["parar"] else "PARADO.")
        ls.stop()
        if vigia:
            vigia.stop()
        fh.close()


def diagnostico(imagem=None):
    """Mostra o que CADA leitor enxerga em um quadro (uma captura agora, ou um print .png). Não aperta nada."""
    import cv2
    base = os.path.dirname(os.path.abspath(__file__))
    if imagem:
        arr = cv2.imdecode(np.fromfile(imagem, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        import mss
        with mss.mss() as sct:
            arr = np.array(sct.grab(sct.monitors[1]))[:, :, :3]
    print("quadro:", arr.shape[1], "x", arr.shape[0], "(esperado 1920 x 1080)")
    try:
        lay = json.load(open(os.path.join(base, "layout.json"), encoding="utf-8")).get("regioes", {})
    except Exception:
        lay = {}
    reg = tuple(lay.get("battle_list", (5, 195, 185, 835)))
    nova = calibrar(arr)
    print("Battle list: layout.json =", reg, "| achada pela calibração =", nova)
    from battle_list import resumo
    print("  lendo com a do layout :", resumo(ler_battle_list(arr, reg)))
    if nova:
        print("  lendo com a calibrada :", resumo(ler_battle_list(arr, nova)))
    x, y, w, h = tuple(lay.get("coordenada", (1730, 54, 120, 14)))
    print("Coordenada:", ler_coord(arr[y:y + h, x:x + w]))
    s = LeitorSkills().ler(arr)
    print("Skills:", None if s is None else {k: (v["pronta"], v["v"]) for k, v in s.items() if k != "revive_em_curso"},
          "| revive_em_curso:", None if s is None else s["revive_em_curso"])
    cav = achar_caveiras(arr)
    print("Caveiras (monstros com vida na tela):", len(cav), "| anéis de stun:", len(achar_aneis(arr)))


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--diagnostico" in a:
        i = a.index("--diagnostico")
        diagnostico(a[i + 1] if len(a) > i + 1 else None)
        sys.exit(0)
    main(seco="--seco" in a, lures=a[a.index("--lures") + 1] if "--lures" in a else None, revive="--sem-revive" not in a,
         ditto="--ditto" in a, andar="--andar" in a, sem_hum="--sem-hum" in a)
