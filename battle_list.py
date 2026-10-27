# -*- coding: utf-8 -*-
"""
Leitor da Battle list.

Dado um quadro da tela (BGR, 1920x1080), diz quem está na lista:
  - o Ditto está presente? (some durante o revive)
  - quantos monstros, e quantos são shiny
  - a vida (0 a 1) de cada entrada

Como funciona: a lista tem uma linha a cada 30 px. Cada linha tem um ícone
pequeno e uma barra de vida. O tipo vem da COR do ícone (o shiny tem o mesmo
nome, mas o ícone tem outra cor) e a vida vem da parte colorida da barra.

    from battle_list import ler_battle_list
    r = ler_battle_list(frame)
    r["ditto"]            # None ou {"hp": 0.93, ...}
    r["n_monstros"], r["n_shiny"], r["monstros"]

Região: a mesma "battle_list" do layout.json (x, y, largura, altura do painel).
Se o painel mudar de lugar, use calibrar(frame), que o acha sozinho (o monitor faz isso ao iniciar).

Uso na linha de comando (para conferir um quadro):
    python battle_list.py quadros\\2026-10-13_04-10-00\\frames\\000173.jpg
"""
import os
import sys
import json

import numpy as np

REGIAO_PADRAO = (5, 195, 185, 835)

# geometria relativa ao canto do painel (medida nos quadros do usuário)
DY_PRIMEIRA = 30        # y da 1ª barra = y do painel + 30
PASSO = 30              # distância entre linhas
BARRA_X0, BARRA_X1 = 36, 161      # início e fim da barra (x relativo ao painel)
BARRA_H = 5
ICONE_X0, ICONE_X1 = 5, 27
ICONE_DY0, ICONE_DY1 = -24, -3    # relativo ao y da barra
MAX_LINHAS = 26

FUNDO = np.array([20, 17, 13])
# cor média (BGR) do ícone de cada tipo (medida nos quadros)
ICONES = {
    "ditto": np.array([40, 64, 64]),
    "gogoat": np.array([40, 40, 32]),
    "shiny": np.array([29, 50, 48]),
}
LIMITE_ICONE = 22.0     # acima disso, o ícone é "desconhecido" (tratado como monstro)


def regiao_do_layout(caminho="layout.json"):
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return tuple(json.load(fh)["regioes"]["battle_list"])
    except Exception:
        return REGIAO_PADRAO


def _linha(frame, x0, y0, k, estrito=False):
    y = y0 + DY_PRIMEIRA + PASSO * k
    if y + BARRA_H >= frame.shape[0]:
        return None
    xa, xb = x0 + BARRA_X0, x0 + BARRA_X1
    band = frame[y:y + BARRA_H, xa:xb].astype(int)
    if band.size == 0:
        return None
    # coluna "ocupada" = difere do fundo do painel na maioria das linhas da barra
    dif = np.abs(band - FUNDO).max(axis=2) > 14
    if estrito:
        # a barra de vida (com o fundo cinza) ocupa a LARGURA INTEIRA em todas as linhas dela;
        # outras barras da tela (energia da Pokebar, por exemplo) não ocupam
        if dif.mean(axis=1).min() < 0.93:
            return None
    ocupada = dif.mean(axis=0) >= 0.6
    if ocupada.mean() < 0.6:               # não há barra nessa linha
        return None
    # parte colorida (vida): saturada
    cheia = ((band.max(axis=2) - band.min(axis=2)) >= 50).mean(axis=0) >= 0.6
    hp = float(cheia.mean())
    if hp < 0.02:                          # entrada de verdade sempre tem vida > 0 (tela preta/cinza não conta)
        return None
    ic = frame[y + ICONE_DY0:y + ICONE_DY1, x0 + ICONE_X0:x0 + ICONE_X1].reshape(-1, 3).astype(float).mean(axis=0)
    dist = {n: float(np.linalg.norm(ic - c)) for n, c in ICONES.items()}
    tipo = min(dist, key=dist.get)
    if dist[tipo] > LIMITE_ICONE:
        tipo = "desconhecido"
    return {"linha": k, "y": y, "tipo": tipo, "hp": round(hp, 3), "icone": [int(v) for v in ic],
            "dist_icone": round(dist[min(dist, key=dist.get)], 1)}


def calibrar(frame):
    """Acha sozinho onde está a Battle list no quadro (para quando o HUD mudar de lugar).

    Cada linha da lista tem uma barra de vida de 7 linhas de pixels (contorno + 5 + contorno)
    e ~128 px de largura, e as linhas ficam 30 px uma da outra. Acha essas barras, confere o
    ícone de cada uma (Ditto, Gogoat ou shiny) e pega a maior sequência. Devolve a região
    (x, y, 185, 835) ou None (lista vazia ou fora da tela). Leva poucos milissegundos."""
    H = frame.shape[0]
    zona = frame[:, :200].astype(np.int16)
    occ = np.abs(zona - FUNDO).max(axis=2) > 14
    interior = occ[:, 42:165].mean(axis=1) >= 0.97           # a barra preenche o miolo todo
    fora = (occ[:, 28:34].mean(axis=1) <= 0.5) & (occ[:, 174:182].mean(axis=1) <= 0.5)   # e para antes das bordas
    ok = interior & fora
    # bandas de 6 a 8 linhas seguidas
    bandas, y = [], 0
    while y < H:
        if ok[y]:
            j = y
            while j < H and ok[j]:
                j += 1
            if 6 <= j - y <= 8:
                bandas.append(y + 1)                           # 1ª linha de preenchimento
            y = j
        else:
            y += 1
    if not bandas:
        return None
    # borda esquerda do miolo -> x do painel (referência: borda em 38,5 com o painel em x=5)
    xs = []
    for yy in bandas[:6]:
        linha = occ[yy + 2]
        c = 42
        while c > 0 and linha[c - 1]:                          # anda para a esquerda enquanto for barra
            c -= 1
        xs.append(c)
    dx = int(round(np.median(xs) - 38.5)) if xs else 0
    x0 = REGIAO_PADRAO[0] + (dx if abs(dx) > 3 else 0)        # só desloca se o painel mudou de verdade
    validas = {}
    for yy in bandas:
        e = _linha(frame, x0, yy - DY_PRIMEIRA, 0)
        if e is not None and e["tipo"] != "desconhecido":
            validas[yy] = e["tipo"]
    melhor, y_ini = 0, None
    for yy in sorted(validas):
        if (yy - PASSO) in validas:
            continue                                            # não é o começo da sequência
        n = 0
        while (yy + PASSO * n) in validas:
            n += 1
        if n > melhor:
            melhor, y_ini = n, yy
    if y_ini is None:
        return None
    return (x0, y_ini - DY_PRIMEIRA, REGIAO_PADRAO[2], REGIAO_PADRAO[3])


def ler_battle_list(frame, regiao=None):
    x0, y0, _, _ = regiao or REGIAO_PADRAO
    entradas = []
    for k in range(MAX_LINHAS):
        e = _linha(frame, x0, y0, k)
        if e is None:
            break                          # a lista é contínua: acabou
        entradas.append(e)
    ditto = next((e for e in entradas if e["tipo"] == "ditto"), None)
    monstros = [e for e in entradas if e["tipo"] != "ditto"]
    return {
        "entradas": entradas,
        "ditto": ditto,
        "monstros": monstros,
        "n_monstros": len(monstros),
        "n_shiny": sum(1 for e in monstros if e["tipo"] == "shiny"),
        "n_desconhecidos": sum(1 for e in monstros if e["tipo"] == "desconhecido"),
    }


def resumo(r):
    d = r["ditto"]
    dt = f"Ditto {d['hp'] * 100:.0f}%" if d else "Ditto AUSENTE"
    vivos = ", ".join(f"{e['tipo']} {e['hp'] * 100:.0f}%" for e in r["monstros"][:6])
    extra = " ..." if r["n_monstros"] > 6 else ""
    return f"{dt} | monstros: {r['n_monstros']} (shiny {r['n_shiny']}, ?{r['n_desconhecidos']}) [{vivos}{extra}]"


if __name__ == "__main__":
    import cv2
    reg = regiao_do_layout()
    for caminho in sys.argv[1:]:
        data = np.fromfile(caminho, dtype=np.uint8)
        im = cv2.imdecode(data, cv2.IMREAD_COLOR)
        print(os.path.basename(caminho), "->", resumo(ler_battle_list(im, reg)))
