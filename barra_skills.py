# -*- coding: utf-8 -*-
"""
Leitor da barra de skills: diz se F, G, C e F2 (Whirlwind) estão PRONTAS ou em COOLDOWN.

Quando uma skill entra em cooldown o ícone ESCURECE e aparece um número grande por cima
(F e G ~40 s, C ~100 s, F2 ~40 s). A leitura usa o brilho (V do HSV) do ícone, que ficou
bem estável nas gravações: pronta F=145, G=106, C=104, F2=131; em cooldown ~70% a 85% disso.

Também detecta o REVIVE: durante ~0,5 s a barra de skills inteira fica preta (brilho ~25).

    from barra_skills import ler_skills
    s = ler_skills(frame)
    s["F"]["pronta"], s["revive_em_curso"]

LIMITES: as posições dos ícones são fixas (HUD atual, 1920x1080). Se você mover a barra
de skills, ajuste ICONES abaixo (x do centro de cada ícone) ou Y0/Y1.
"""
import numpy as np
import cv2

# x do centro de cada ícone; y do recorte (medidos no HUD do usuário)
ICONES = {"F": 747, "G": 782, "C": 957, "F2": 992, "F3": 1027, "F4": 1062}
Y0, Y1 = 952, 992
MEIA_LARGURA = 17
# brilho de referência de cada ícone quando está PRONTO (cresce sozinho se aparecer mais claro)
PRONTA_V = {"F": 145.0, "G": 106.0, "C": 104.0, "F2": 131.0, "F3": 120.0, "F4": 108.0}   # F3/F4 medidos no print de 22/10/2026
FRACAO_CD = 0.90            # abaixo de 90% do brilho de referência = em cooldown
APAGADA_V = 45.0            # abaixo disso (nos 4 ícones) = revive em curso


def brilho(frame, x):
    cell = frame[Y0:Y1, x - MEIA_LARGURA:x + MEIA_LARGURA]
    if cell.size == 0:
        return None
    return float(cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)[..., 2].mean())


class LeitorSkills:
    """Mantém a referência de brilho de cada ícone (pode crescer se o HUD for mais claro)."""

    def __init__(self):
        self.ref = dict(PRONTA_V)

    def ler(self, frame):
        v = {k: brilho(frame, x) for k, x in ICONES.items()}
        if any(b is None for b in v.values()):
            return None
        apagada = all(b < APAGADA_V for b in v.values())
        out = {"revive_em_curso": apagada}
        for k, b in v.items():
            if not apagada and b > self.ref[k]:
                self.ref[k] = b
            out[k] = {"pronta": (not apagada) and b >= self.ref[k] * FRACAO_CD, "v": round(b, 1)}
        return out


_padrao = LeitorSkills()


def ler_skills(frame):
    return _padrao.ler(frame)
