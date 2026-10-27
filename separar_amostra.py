# -*- coding: utf-8 -*-
"""
Monta uma amostra LEVE de uma gravação do gravador_quadros.py para enviar.

Escolhe sozinho os quadros que importam: os que ficam em volta dos seus apertos
(revive, atacar, pokébola) e das marcas, e depois 1 quadro por
segundo no resto. Os arquivos pequenos (meta, index, eventos, layout) vão sempre.
Os quadros são copiados sem recomprimir (os pixels continuam exatos).

Uso:
    python separar_amostra.py quadros\\2026-10-13_04-10-00
    python separar_amostra.py quadros\\2026-10-13_04-10-00 --max-mb 25 --partes 3

    --max-mb   tamanho máximo de cada zip (padrão 25)
    --partes   quantos zips gerar (padrão 1). Com 3 partes de 25 MB você manda ~75 MB.
Saída: amostra_1.zip, amostra_2.zip, ... na pasta onde você rodou o comando.
"""
import os
import sys
import json
import zipfile
import argparse

# mover/parar acontecem dezenas de vezes por volta: não ganham janela própria (só entram no 1 quadro/s)
PRIORIDADE = ["marca", "stun", "revive", "atacar", "pokebola"]
ANTES, DEPOIS = 1.0, 2.0       # segundos em volta de cada evento
PEQUENOS = ["meta.json", "index.jsonl", "eventos.jsonl"]


def ler_jsonl(caminho):
    out = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if linha:
                try:
                    out.append(json.loads(linha))
                except ValueError:
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--max-mb", type=float, default=25.0)
    ap.add_argument("--partes", type=int, default=1)
    a = ap.parse_args()

    pasta = a.pasta
    pf = os.path.join(pasta, "frames")
    if not os.path.isdir(pf):
        print("Não achei a pasta 'frames' dentro de", pasta)
        sys.exit(1)

    index = [d for d in ler_jsonl(os.path.join(pasta, "index.jsonl")) if not d.get("descartado")]
    eventos = ler_jsonl(os.path.join(pasta, "eventos.jsonl"))
    quadros = [(d["n"], d["t"]) for d in index if os.path.exists(os.path.join(pf, f"{d['n']:06d}.jpg"))]
    if not quadros:
        print("Nenhum quadro encontrado.")
        sys.exit(1)
    dur = quadros[-1][1]
    print(f"{len(quadros)} quadros, {dur:.0f} s, {len(eventos)} eventos registrados.")

    # eventos de interesse por categoria (só o apertar, não o soltar)
    cat = {c: [] for c in PRIORIDADE}
    for e in eventos:
        if e.get("tipo") == "marca":
            cat["marca"].append(e["t"])
        elif e.get("tipo") == "atalho" and e.get("acao") == "down" and e.get("funcao") in cat:
            cat[e["funcao"]].append(e["t"])
    for c in PRIORIDADE:
        if cat[c]:
            print(f"  {c}: {len(cat[c])} apertos")

    for c in PRIORIDADE:                     # agrupa rajadas (ex.: 7 apertos de R seguidos = 1 evento)
        ts, grupo = sorted(cat[c]), []
        for t in ts:
            if not grupo or t - grupo[-1] > 1.5:
                grupo.append(t)
        cat[c] = grupo

    ordem, visto = [], set()

    def add(n):
        if n not in visto:
            visto.add(n)
            ordem.append(n)

    # 1) em volta dos eventos, de 2 em 2 quadros, na ordem de prioridade
    janelas = []
    for c in PRIORIDADE:
        for t in cat[c]:
            sel = [n for n, tf in quadros if t - ANTES <= tf <= t + DEPOIS]
            janelas.append(sel)
            for n in sel[::2]:
                add(n)
    # 2) um quadro por segundo no resto
    ult = -1
    for n, t in quadros:
        if int(t) != ult:
            ult = int(t)
            add(n)
    # 3) o que sobrou das janelas
    for sel in janelas:
        for n in sel:
            add(n)

    # preenche as partes
    limite = a.max_mb * 1e6
    partes = [[] for _ in range(a.partes)]
    usado = [0.0] * a.partes
    pequenos = [f for f in PEQUENOS + ["layout.json"] if os.path.exists(os.path.join(pasta, f))]
    for f in pequenos:
        usado[0] += os.path.getsize(os.path.join(pasta, f))
    p = 0
    for n in ordem:
        tam = os.path.getsize(os.path.join(pf, f"{n:06d}.jpg"))
        while p < a.partes and usado[p] + tam > limite:
            p += 1
        if p >= a.partes:
            break
        partes[p].append(n)
        usado[p] += tam

    total = 0
    for i, ns in enumerate(partes, start=1):
        if not ns and i > 1:
            continue
        nome = f"amostra_{i}.zip"
        with zipfile.ZipFile(nome, "w", zipfile.ZIP_STORED) as z:
            if i == 1:
                for f in pequenos:
                    z.write(os.path.join(pasta, f), f)
            for n in sorted(ns):
                z.write(os.path.join(pf, f"{n:06d}.jpg"), f"frames/{n:06d}.jpg")
        mb = os.path.getsize(nome) / 1e6
        total += len(ns)
        print(f"{nome}: {len(ns)} quadros, {mb:.1f} MB")
    print(f"\nTotal: {total} de {len(quadros)} quadros ({100 * total / len(quadros):.0f}%).")
    if total < len(visto):
        print("Dica: se quiser mais quadros, aumente --max-mb ou --partes.")


if __name__ == "__main__":
    main()
