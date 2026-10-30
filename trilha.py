# -*- coding: utf-8 -*-
"""
TRILHA (fase 5, fuga): memória por onde o personagem andou e QUANDO. Os monstros de um lugar são puxados pelo lure e só respawnam
depois de ~1 minuto; então um tile visitado há < LIMITE s é "seguro" para fugir, e um visitado há mais (ou nunca) NÃO é.
registrar(t, (x, y)) a cada leitura de posição (preenche os tiles entre duas leituras); idade(q, t) -> segundos desde a última visita
perto de q (raio em tiles), ou None se nunca passou ali.
"""


class Trilha:
    def __init__(self, raio=2, reter_s=130.0):
        self.raio, self.reter_s = raio, reter_s
        self.visita = {}
        self.ult = None
        self.n = 0

    def registrar(self, t, p):
        p = (int(p[0]), int(p[1]))
        if self.ult is not None and self.ult[1] != p:
            (t0, a), (t1, b) = self.ult, (t, p)
            passos, x, y = [], a[0], a[1]
            while (x, y) != b:                                  # preenche em cruz entre duas leituras (a posição vem a ~3 Hz)
                if x != b[0]:
                    x += 1 if b[0] > x else -1
                elif y != b[1]:
                    y += 1 if b[1] > y else -1
                passos.append((x, y))
            for i, q in enumerate(passos, 1):
                self.visita[q] = t0 + (t1 - t0) * i / (len(passos) + 1)
        self.visita[p] = t
        self.ult = (t, p)                                       # SEMPRE a última leitura (parado 10 s e depois andou: o intervalo é curto)
        self.n += 1
        if self.n % 300 == 0:                                   # poda
            lim = t - self.reter_s
            self.visita = {q: tv for q, tv in self.visita.items() if tv >= lim}

    def congelar(self):
        """Cópia só-leitura do que foi andado ATÉ AGORA. A fuga usa essa foto: os tiles que ela própria pisa depois NÃO viram 'seguros'
        (senão a região segura se estenderia sozinha e o limite de 1 minuto nunca valeria)."""
        c = Trilha(self.raio, self.reter_s)
        c.visita = dict(self.visita)
        return c

    def idade(self, q, t):
        melhor = None
        for dx in range(-self.raio, self.raio + 1):
            for dy in range(-self.raio, self.raio + 1):
                tv = self.visita.get((q[0] + dx, q[1] + dy))
                if tv is not None and (melhor is None or tv > melhor):
                    melhor = tv
        return None if melhor is None else max(0.0, t - melhor)
