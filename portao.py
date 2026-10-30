# -*- coding: utf-8 -*-
"""
PORTÃO (fase 5, etapa 1): diz ao Player quando ele pode SAIR da parada e andar para o próximo lure.

Regra de ouro: na dúvida, FECHADO. O portão só abre com leitura de combate válida e fresca, e fecha sozinho
se o bot parar de alimentá-lo (quadro travado, erro de leitura, FPS baixo).

Abre quando TODAS valem, em LEITURAS_SEGUIDAS leituras seguidas e por >= ABRE_S segundos:
  - leitura válida (skills legíveis, FPS ok, nível não cinza)
  - Ditto presente e fora de revive
  - nenhum monstro na Battle list há >= SEM_MONSTROS_S
  - nenhum revive de 'resetar' pendente (skill gasta com a área vazia: o bot, ou você com --sem-revive, ainda vai reviver)
Se ficar fechado por mais de ALERTA_S, avisa (log + beep) e CONTINUA esperando: nunca anda por tempo.
"""
import threading

SEM_MONSTROS_S = 1.0     # sem monstros há pelo menos isso
ABRE_S = 0.5             # condições verdadeiras por pelo menos isso
LEITURAS_SEGUIDAS = 2
VALIDADE_S = 0.8         # leitura mais velha que isso = portão fechado
ALERTA_S = 30.0
BANDO_ESPERA_S = 15.0    # chegou andando na parada e NÃO viu monstro nenhum: espera o bando (pode estar atrás) até isso


class Portao:
    def __init__(self, log=print):
        self.log = log
        self.lock = threading.Lock()
        self.t_leitura = -99.0
        self.ok = False
        self.motivo = "sem leitura ainda"
        self.seguidas = 0
        self.desde = None
        self.parada_ativa, self.chegada, self.visto, self.exige, self._avisou = False, 0.0, False, False, False

    # -- chamado pelo Player ao chegar numa parada / ao sair dela (relógio do bot)
    def nova_parada(self, agora, exige):
        with self.lock:
            self.parada_ativa, self.chegada, self.visto, self.exige, self._avisou = True, agora, False, bool(exige), False

    def fim_parada(self):
        with self.lock:
            self.parada_ativa = False

    # -- chamado pelo laço de combate a cada quadro (agora = relógio do bot)
    def atualizar(self, agora, r, leitura_ok):
        motivo = self._avaliar(r, leitura_ok)
        with self.lock:
            self.t_leitura = agora
            if self.parada_ativa and r is not None and (r.get("n_monstros") or 0) > 0:
                self.visto = True                       # o bando apareceu NESTA parada
            if motivo is None:
                self.seguidas += 1
                if self.desde is None:
                    self.desde = agora
                abre = self.seguidas >= LEITURAS_SEGUIDAS and agora - self.desde >= ABRE_S
                self.ok = abre
                self.motivo = "" if abre else "confirmando"
            else:
                self.seguidas, self.desde, self.ok, self.motivo = 0, None, False, motivo

    @staticmethod
    def _avaliar(r, leitura_ok):
        """None = tudo certo; senão o motivo de estar fechado."""
        if not leitura_ok or r is None:
            return "leitura inválida (FPS baixo / skills ilegíveis)"
        if r.get("nivel") == "cinza":
            return "leitura cinza"
        if not r.get("ditto_presente") or r.get("revive_em_curso"):
            return "Ditto fora / revive em curso"
        if (r.get("n_monstros") or 0) > 0:
            return f"{r['n_monstros']} monstro(s) vivo(s)"
        if (r.get("sem_monstros_s") or 0.0) < SEM_MONSTROS_S:
            return f"sem monstros há só {(r.get('sem_monstros_s') or 0.0):.1f} s"
        if r.get("revive_tipo") == "resetar":
            return "revive de reset pendente"
        return None

    # -- chamado pelo Player (qualquer thread). Devolve (liberado, motivo)
    def estado(self, agora):
        with self.lock:
            if agora - self.t_leitura > VALIDADE_S:
                return False, "sem leitura fresca (bot não está alimentando o portão)"
            if self.ok and self.parada_ativa and self.exige and not self.visto:
                # chegou ANDANDO e nenhum monstro apareceu: lista vazia não prova que o bando morreu (pode estar atrás, fora da tela)
                if agora - self.chegada < BANDO_ESPERA_S:
                    return False, f"aguardando o bando chegar (nenhum monstro visto nesta parada; {agora - self.chegada:.0f}/{BANDO_ESPERA_S:.0f} s)"
                if not self._avisou:
                    self._avisou = True
                    self.log(f"[AVISO] {BANDO_ESPERA_S:.0f} s na parada e nenhum monstro apareceu: o lure veio vazio? Sigo para o próximo.")
            return self.ok, self.motivo
