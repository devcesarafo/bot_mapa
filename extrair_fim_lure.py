# -*- coding: utf-8 -*-
"""
EXTRAIR FIM DE LURE (fase 5, etapa 1). NÃO aperta nada, NÃO abre o jogo: só lê arquivos.

Entrada : uma gravação do gravador_quadros.py (pasta quadros\\AAAA-MM-DD_HH-MM-SS) já analisada com analisar_ditto.py
          (precisa de index.jsonl e analise_ditto.json dentro da pasta).
Saída   : <pasta>\\lures_da_volta.json  no MESMO formato do lures.json do gravador_lures.py, com:
            - o trajeto de cada lure e a PARADA = onde VOCÊ parou nesta volta;
            - "ditto_fim": onde o Ditto ESTAVA quando o 1º R do lure saiu (tile absoluto, deslocamento em tiles e distância
              até o personagem), com quantas amostras sustentam o número.
          E um resumo na tela: distância Ditto-personagem por lure e quanto a parada mudou em relação ao lures.json antigo.
NÃO substitui o lures.json antigo (o bot só usa este se você passar --lures).

Uso:  python extrair_fim_lure.py quadros\\AAAA-MM-DD_HH-MM-SS [lures_antigo.json] [mapa_obstaculos.json]
"""
import os
import sys
import json
import math
import statistics

from gravador_lures import montar_lures, validar_no_mapa, tempos_por_passo
from editor_mapa import ObstacleMap

JANELA_ANTES_S = 2.0      # amostras do Ditto de [chegada na parada + 0,5 s] até [R + 3 s]
JANELA_DEPOIS_S = 3.0


def ler_jsonl(p):
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def trajeto(index):
    """[(t, x, y, z)] só quando a coordenada muda (como o gravador_lures)."""
    pts = []
    for q in index:
        c = q.get("coord")
        if not c or len(c) < 3:
            continue
        if not pts or (pts[-1][1], pts[-1][2], pts[-1][3]) != (c[0], c[1], c[2]):
            pts.append((q["t"], c[0], c[1], c[2]))
    return pts


def ditto_fim(a, paradas, Rs):
    """Por parada: mediana do deslocamento do Ditto (tiles) nas amostras de 0,5 s em volta do 1º R."""
    tr = [q for q in a["trilha_0_5s"] if q.get("ditto_tiles")]
    out = []
    for k, p in enumerate(paradas):
        R = next((r for r in Rs if p["ini"] <= r <= p["fim"]), None)
        if R is None:
            out.append(None)
            continue
        amostras = [q for q in tr if p["ini"] + 0.5 <= q["t"] <= min(p["fim"], R + JANELA_DEPOIS_S)
                    and q["t"] >= R - JANELA_ANTES_S - 4.0]
        # só as mais perto do R (as de antes do R podem ser o Ditto ainda andando): as de [R-2, R+3] se houver
        perto = [q for q in amostras if R - JANELA_ANTES_S <= q["t"] <= R + JANELA_DEPOIS_S] or amostras
        if not perto:
            out.append({"R_t": R, "erro": "nenhuma amostra do Ditto perto do R (Ditto escondido sob os monstros)"})
            continue
        dx = statistics.median(q["ditto_tiles"][0] for q in perto)
        dy = statistics.median(q["ditto_tiles"][1] for q in perto)
        pos = p["coord"]
        out.append({"R_t": R, "offset": [round(dx, 1), round(dy, 1)],
                    "tile": [int(round(pos[0] + dx)), int(round(pos[1] + dy))],
                    "dist_tiles": round(math.hypot(dx, dy), 1), "amostras": len(perto),
                    "espalhamento_tiles": round(max(math.hypot(q["ditto_tiles"][0] - dx, q["ditto_tiles"][1] - dy) for q in perto), 1)})
    return out


def main(pasta, antigo=None, mapa=None):
    a = json.load(open(os.path.join(pasta, "analise_ditto.json"), encoding="utf-8"))
    index = ler_jsonl(os.path.join(pasta, "index.jsonl"))
    paradas = a["paradas_do_personagem"]
    pontos = trajeto(index)
    obst = None
    if mapa and os.path.exists(mapa):
        obst = ObstacleMap.load(mapa)
    lures = montar_lures(pontos, [p["ini"] + 0.3 for p in paradas], obst)   # +0,3 s: o "ini" cai no instante do último passo
    fins = ditto_fim(a, paradas, a["R"])
    for lu, fim, p in zip(lures, fins, paradas):
        lu["parada_nesta_volta_s"] = [p["ini"], p["fim"]]
        if fim:
            lu["ditto_fim"] = fim
    ruins = validar_no_mapa(lures, obst)
    dados = {"versao": 2, "origem": os.path.abspath(pasta), "mapa_obstaculos": mapa or "",
             "velocidade_medida": tempos_por_passo(pontos), "lures": lures, "passos_fora_do_andavel": ruins,
             "bruto": [[round(p[0], 3), p[1], p[2], p[3]] for p in pontos]}
    destino = os.path.join(pasta, "lures_da_volta.json")
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=1)
    old = json.load(open(antigo, encoding="utf-8"))["lures"] if antigo and os.path.exists(antigo) else None
    print(f"Salvo: {destino}\n")
    print(f"{'lure':6} {'parada nesta volta':20} {'Δ p/ antigo':12} {'Ditto no R (dx,dy)':20} {'dist':>5} {'amostras':>8}")
    for k, lu in enumerate(lures):
        f = lu.get("ditto_fim") or {}
        dif = "-"
        if old and k < len(old):
            g = old[k]["parada"]
            dif = f"{max(abs(lu['parada'][0] - g[0]), abs(lu['parada'][1] - g[1]))} tiles"
        off = tuple(f["offset"]) if f.get("offset") else f.get("erro", "?")
        print(f"{k + 1:<6} {str(lu['parada'][:2]):20} {dif:12} {str(off):20} {f.get('dist_tiles', '-'):>5} {f.get('amostras', '-'):>8}")
    ds = [f["ditto_fim"]["dist_tiles"] for f in lures if f.get("ditto_fim", {}).get("dist_tiles") is not None]
    if ds:
        print(f"\nDistância Ditto-personagem no R: mín {min(ds)}, mediana {statistics.median(ds)}, máx {max(ds)} tiles"
              f"  (Gogoat anda 2 tiles/s: {min(ds) / 2:.1f} a {max(ds) / 2:.1f} s para chegar ao personagem, sem contar a área)")
    if ruins is not None:
        print(f"Passos fora do andável no mapa: {len(ruins)}" + (f"  (ex.: {ruins[0]})" if ruins else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    base = os.path.dirname(os.path.abspath(__file__))
    ant = sys.argv[2] if len(sys.argv) > 2 else None
    if ant is None:
        g = os.path.join(base, "gravacoes")
        if os.path.isdir(g):
            c = sorted(d for d in os.listdir(g) if os.path.exists(os.path.join(g, d, "lures.json")))
            ant = os.path.join(g, c[-1], "lures.json") if c else None
    mp = sys.argv[3] if len(sys.argv) > 3 else os.path.join(base, "mapa_obstaculos.json")
    main(sys.argv[1], ant, mp)
