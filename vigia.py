# -*- coding: utf-8 -*-
"""
VIGIA DO DITTO (fase 5, fuga). Thread leve (~15 leituras/s) que olha SÓ o recorte da Battle list e diz se o Ditto está na lista.
O laço principal do bot lê ~3 quadros/s (o resto da leitura é pesado); para a MORTE do Ditto isso é lento demais.
Cada leitura do recorte leva ~1 ms (ler_battle_list em recorte pequeno, igual ao quadro inteiro nos prints testados).

estado() -> dict(presente, hp, n_monstros, ausente_desde, t, painel_ok)
  presente      True/False; None se o painel não parece ser a Battle list (tela preta, janela minimizada...): NUNCA conclua morte com None.
  ausente_desde instante (relógio do bot) da PRIMEIRA leitura sem o Ditto nesta sequência (None se ele está presente).
"""
import threading
import time

import numpy as np

from battle_list import ler_battle_list, FUNDO

PAINEL_MIN = 0.40        # fração do recorte com a cor de fundo do painel; menos que isso = não é a Battle list
HZ = 15.0


class VigiaDitto:
    def __init__(self, regiao, t0, mss_mod=None, log=print, hz=HZ):
        self.regiao, self.t0, self.log, self.hz = tuple(regiao), t0, log, hz
        self.mss_mod = mss_mod
        self.lock = threading.Lock()
        self._e = {"presente": None, "hp": None, "n_monstros": 0, "ausente_desde": None, "t": -99.0, "painel_ok": False}
        self.parar = threading.Event()
        self.th = None
        self.n_leituras = 0

    # ---- leitura de um recorte (BGR ou BGRA). Usada pela thread e pelos testes.
    def processar(self, recorte, agora):
        w, h = recorte.shape[1], recorte.shape[0]
        rec = recorte[:, :, :3]
        painel = float((np.abs(rec.astype(int) - FUNDO).max(axis=2) <= 14).mean())
        painel_ok = painel >= PAINEL_MIN
        b = ler_battle_list(rec, (0, 0, w, h)) if painel_ok else None
        with self.lock:
            e = self._e
            if not painel_ok:
                self._e = {"presente": None, "hp": None, "n_monstros": 0, "ausente_desde": None, "t": agora, "painel_ok": False}
                return self._e
            presente = b["ditto"] is not None
            ausente = None if presente else (e["ausente_desde"] if (e["presente"] is False and e["ausente_desde"] is not None) else agora)
            self._e = {"presente": presente, "hp": (b["ditto"]["hp"] if presente else None), "n_monstros": b["n_monstros"],
                       "ausente_desde": ausente, "t": agora, "painel_ok": True}
            self.n_leituras += 1
            return self._e

    def estado(self):
        with self.lock:
            return dict(self._e)

    def iniciar(self):
        def laco():
            x, y, w, h = self.regiao
            try:
                m = self.mss_mod
                with m.mss() as sct:
                    box = {"left": x, "top": y, "width": w, "height": h}
                    while not self.parar.is_set():
                        t = time.perf_counter()
                        try:
                            self.processar(np.array(sct.grab(box)), t - self.t0)
                        except Exception as ex:
                            self.log(f"[vigia] erro de leitura: {ex!r}")
                        dt = time.perf_counter() - t
                        time.sleep(max(0.0, 1.0 / self.hz - dt))
            except Exception as ex:
                self.log(f"[vigia] parou: {ex!r}")
        self.th = threading.Thread(target=laco, daemon=True)
        self.th.start()

    def encerrar(self):
        self.parar.set()
