# -*- coding: utf-8 -*-
"""
ANALISADOR DO DITTO (mover 'o'/mouse:x2 e parar 'p'/mouse:x1): extrai o PADRÃO DE USO de uma volta inteira.

NÃO aperta nada. Lê uma gravação do gravador_quadros.py (pasta quadros\\AAAA-MM-DD_HH-MM-SS) e, para cada aperto de
mover/parar, junta: onde você estava (coordenada), para onde ia, onde estava o mouse (em tiles em relação ao
personagem), onde estava o Ditto, quantos monstros, e quando o personagem parou.

COMO GRAVAR (uma volta completa da hunt, jogando normalmente, SEM o bot apertando):
  1) python gravador_quadros.py   (terminal como ADMINISTRADOR; o layout já está calibrado)
  2) marque "Registrar meus apertos das teclas do jogo", fps 10, duração máxima >= 300 s
  3) Ctrl+Alt+G inicia; faça o lure da volta inteira como sempre faz (inclua o revive e o R);
     Ctrl+Alt+M coloca uma marca nos momentos interessantes (ex.: início de cada lure); Ctrl+Alt+G para.
  4) python analisar_ditto.py quadros\\AAAA-MM-DD_HH-MM-SS
Saída: analise_ditto.json (dentro da pasta da gravação; é pequeno, é ele que você me envia) + resumo na tela.
Dependências: opencv-python numpy; arquivos na mesma pasta: battle_list.py, detector_stun.py.
Pode demorar alguns minutos (lê os quadros a cada 0,5 s).
"""
import os
import sys
import json
import math
import bisect
import statistics

import numpy as np
import cv2

TILE = 72.3
CENTRO_JOGO = (960.0, 490.0)        # centro do tile do personagem (para o mouse)
CENTRO_BARRA = (959.0, 452.0)       # barra de vida do treinador (mesma referência do Ditto e das caveiras)
AMOSTRA_S = 0.5                     # visão a cada 0,5 s
RAJADA_GAP_S = 0.6                  # dois 'mover' com mais que isso entre si = rajadas diferentes
PARADO_MIN_S = 1.0                  # sem mudar de coordenada por isso = personagem parado
FN_MOVER, FN_PARAR = "mover_ditto", "parar_ditto"


def jsonl(p):
    out = []
    with open(p, encoding="utf-8") as fh:
        for l in fh:
            l = l.strip()
            if l:
                try:
                    out.append(json.loads(l))
                except ValueError:
                    pass
    return out


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    k = (len(v) - 1) * p / 100.0
    a, b = int(math.floor(k)), int(math.ceil(k))
    return round(v[a] + (v[b] - v[a]) * (k - a), 2)


def resumo(v, nd=2):
    if not v:
        return None
    return {"n": len(v), "p10": pct(v, 10), "mediana": pct(v, 50), "p90": pct(v, 90), "min": round(min(v), nd), "max": round(max(v), nd)}


class Linha:
    """Coordenada do personagem no tempo."""

    def __init__(self, index):
        self.t = [q["t"] for q in index if q.get("coord")]
        self.c = [tuple(q["coord"][:2]) for q in index if q.get("coord")]

    def em(self, t):
        if not self.t:
            return None
        i = bisect.bisect_right(self.t, t) - 1
        return self.c[max(0, i)]

    def vetor(self, t0, t1):
        a, b = self.em(t0), self.em(t1)
        return None if a is None or b is None else (b[0] - a[0], b[1] - a[1])

    def paradas(self, minimo):
        """[(t_ini, t_fim, coord)] com o personagem parado >= minimo s."""
        out, ini = [], 0
        for i in range(1, len(self.t) + 1):
            if i == len(self.t) or self.c[i] != self.c[ini]:
                if self.t[i - 1] - self.t[ini] >= minimo:
                    out.append((self.t[ini], self.t[i - 1], self.c[ini]))
                ini = i
        return out


def tiles_mouse(pos):
    return ((pos[0] - CENTRO_JOGO[0]) / TILE, (pos[1] - CENTRO_JOGO[1]) / TILE)


def decompor(off, vet):
    """off e vet em tiles: (à frente, lateral) do deslocamento 'off' em relação à direção 'vet'."""
    if vet is None:
        return None, None
    n = math.hypot(vet[0], vet[1])
    if n < 1.0:
        return None, None
    ux, uy = vet[0] / n, vet[1] / n
    return round(off[0] * ux + off[1] * uy, 2), round(-off[0] * uy + off[1] * ux, 2)


def main(pasta):
    from battle_list import ler_battle_list, calibrar
    from detector_stun import achar_caveiras, achar_ditto, configurar
    configurar(0.25, range(32, 46, 4))
    meta = json.load(open(os.path.join(pasta, "meta.json"), encoding="utf-8"))
    index = jsonl(os.path.join(pasta, "index.jsonl"))
    ev = jsonl(os.path.join(pasta, "eventos.jsonl"))
    linha = Linha(index)
    ts_idx = [q["t"] for q in index]
    dur = meta.get("duracao_s") or (ts_idx[-1] if ts_idx else 0)
    print(f"Gravação: {dur:.0f} s, {len(index)} quadros, {len(ev)} eventos")

    # ---------------- visão amostrada
    def quadro(t):
        i = max(0, min(len(index) - 1, bisect.bisect_left(ts_idx, t)))
        if i > 0 and abs(ts_idx[i - 1] - t) < abs(ts_idx[i] - t):
            i -= 1
        p = os.path.join(pasta, "frames", f"{index[i]['n']:06d}.jpg")
        if not os.path.exists(p):
            return None
        return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

    reg_bl = [None]

    def ver(t):
        arr = quadro(t)
        if arr is None:
            return None
        if reg_bl[0] is None:
            reg_bl[0] = calibrar(arr)
        b = ler_battle_list(arr, reg_bl[0]) if reg_bl[0] else None
        if b is None or (not b["ditto"] and not b["n_monstros"]):
            nova = calibrar(arr)
            if nova:
                reg_bl[0] = nova
                b = ler_battle_list(arr, nova)
        if b is None:
            return None
        hp = b["ditto"]["hp"] if b["ditto"] else None
        cav = achar_caveiras(arr) if b["n_monstros"] else []
        dp = achar_ditto(arr, cav, hp) if hp is not None else None
        d = {"t": round(t, 2), "n_mon": b["n_monstros"], "ditto_hp": hp}
        if dp is not None:
            d["ditto_tiles"] = [round((dp[0] - CENTRO_BARRA[0]) / TILE, 1), round((dp[1] - CENTRO_BARRA[1]) / TILE, 1)]
        if cav:
            dist = [math.hypot((c[0] - CENTRO_BARRA[0]) / TILE, (c[1] - CENTRO_BARRA[1]) / TILE) for c in cav]
            d["mon_dist_min"], d["mon_dist_med"] = round(min(dist), 1), round(statistics.median(dist), 1)
            if dp is not None:                      # área do Whirlwind: 3 tiles de cada lado, sem os cantos
                dentro = 0
                for c in cav:
                    dx, dy = abs(c[0] - dp[0]) / TILE, abs(c[1] - dp[1]) / TILE
                    if dx <= 3.4 and dy <= 3.4 and not (dx > 2.6 and dy > 2.6):
                        dentro += 1
                d["mon_na_area_do_ditto"] = dentro
        return d

    print("Lendo quadros (visão a cada 0,5 s)...")
    track = []
    t = 0.0
    while t <= dur:
        d = ver(t)
        if d:
            track.append(d)
        t += AMOSTRA_S
        if len(track) % 40 == 0 and track:
            print(f"  {t:.0f}/{dur:.0f} s", end="\r")
    ts_track = [d["t"] for d in track]

    def visao_perto(t):
        if not track:
            return {}
        i = max(0, min(len(track) - 1, bisect.bisect_left(ts_track, t)))
        return track[i]

    # ---------------- eventos
    movs = [e for e in ev if e.get("tipo") == "atalho" and e.get("funcao") == FN_MOVER and e.get("acao") == "down"]
    pars = [e for e in ev if e.get("tipo") == "atalho" and e.get("funcao") == FN_PARAR and e.get("acao") == "down"]
    atac = [e["t"] for e in ev if e.get("tipo") == "atalho" and e.get("funcao") == "atacar" and e.get("acao") == "down"]
    revs = [e["t"] for e in ev if e.get("tipo") == "atalho" and e.get("funcao") == "revive" and e.get("acao") == "down"]
    marcas = [e["t"] for e in ev if e.get("tipo") == "marca"]
    paradas = linha.paradas(PARADO_MIN_S)
    print(f"mover: {len(movs)} | parar: {len(pars)} | R: {len(atac)} | revive: {len(revs)} | "
          f"paradas do personagem (>= {PARADO_MIN_S:.0f} s): {len(paradas)}")

    # ---------------- rajadas de mover
    rajadas, atual = [], []
    for e in movs:
        if atual and e["t"] - atual[-1]["t"] > RAJADA_GAP_S:
            rajadas.append(atual)
            atual = []
        atual.append(e)
    if atual:
        rajadas.append(atual)
    linhas_raj, intervalos = [], []
    off_frente, off_lat, off_dist, off_frente_prox = [], [], [], []
    for r in rajadas:
        t0, t1 = r[0]["t"], r[-1]["t"]
        offs = [tiles_mouse(e["mouse"]) for e in r if e.get("mouse")]
        for a, b in zip(r, r[1:]):
            intervalos.append(b["t"] - a["t"])
        vet_ant = linha.vetor(t0 - 1.5, t0)
        vet_prox = linha.vetor(t1, t1 + 1.5)
        row = {"t0": round(t0, 2), "t1": round(t1, 2), "n": len(r),
               "taxa_hz": round((len(r) - 1) / (t1 - t0), 2) if t1 > t0 else None,
               "coord_ini": linha.em(t0), "coord_fim": linha.em(t1),
               "andando": bool(vet_ant and (abs(vet_ant[0]) + abs(vet_ant[1])) >= 1)}
        if offs:
            mx, my = statistics.median(o[0] for o in offs), statistics.median(o[1] for o in offs)
            row["mouse_tiles"] = [round(mx, 1), round(my, 1)]
            row["mouse_dist"] = round(math.hypot(mx, my), 1)
            fr, la = decompor((mx, my), vet_ant)
            fr2, _ = decompor((mx, my), vet_prox)
            row["a_frente_do_rumo"], row["lateral"], row["a_frente_do_proximo_rumo"] = fr, la, fr2
            off_dist.append(row["mouse_dist"])
            if fr is not None:
                off_frente.append(fr)
                off_lat.append(la)
            if fr2 is not None:
                off_frente_prox.append(fr2)
        row["visao"] = visao_perto(t0)
        linhas_raj.append(row)

    # ---------------- apertos de parar
    linhas_par = []
    for e in pars:
        t = e["t"]
        ult_mov = max([m["t"] for m in movs if m["t"] <= t], default=None)
        parada = next(((a, b, c) for a, b, c in paradas if a - 3.0 <= t <= b), None)
        v = ver(t) or visao_perto(t)
        row = {"t": round(t, 2), "mouse": e.get("mouse"), "coord": linha.em(t),
               "desde_ultimo_mover_s": round(t - ult_mov, 2) if ult_mov is not None else None,
               "personagem_parado": bool(parada and parada[0] <= t),
               "relativo_ao_inicio_da_parada_s": round(t - parada[0], 2) if parada else None,
               "proximo_R_s": next((round(a - t, 2) for a in atac if a >= t), None),
               "visao": v}
        linhas_par.append(row)

    # ---------------- paradas do personagem
    linhas_stop = []
    for a, b, c in paradas:
        ps = [p["t"] - a for p in pars if a - 4 <= p["t"] <= b]
        rs = [r - a for r in atac if a <= r <= b + 6]
        rv = [r - a for r in revs if a <= r <= b + 6]
        n_mov = sum(1 for m in movs if a <= m["t"] <= b)
        linhas_stop.append({"ini": round(a, 2), "fim": round(b, 2), "dur": round(b - a, 1), "coord": c,
                            "parar_ditto_rel_s": [round(x, 2) for x in ps], "R_rel_s": [round(x, 2) for x in rs],
                            "revive_rel_s": [round(x, 2) for x in rv], "mover_durante": n_mov,
                            "visao": visao_perto(a + 0.5)})

    # ---------------- Ditto atrás do personagem durante as rajadas
    dist_ditto = [math.hypot(*d["ditto_tiles"]) for d in track if d.get("ditto_tiles")]
    parar_rel = [x["relativo_ao_inicio_da_parada_s"] for x in linhas_par if x["relativo_ao_inicio_da_parada_s"] is not None]
    perfil = {
        "mover_total": len(movs), "parar_total": len(pars), "rajadas_mover": len(rajadas),
        "mover_intervalo_s": resumo(intervalos, 3),
        "mover_por_rajada": resumo([r["n"] for r in linhas_raj], 0),
        "rajada_duracao_s": resumo([r["t1"] - r["t0"] for r in linhas_raj]),
        "mouse_distancia_tiles": resumo(off_dist),
        "mouse_a_frente_do_rumo_tiles": resumo(off_frente),
        "mouse_a_frente_do_proximo_rumo_tiles": resumo(off_frente_prox),
        "mouse_lateral_tiles": resumo(off_lat),
        "parar_relativo_ao_inicio_da_parada_s": resumo(parar_rel),
        "parar_com_personagem_parado": sum(1 for x in linhas_par if x["personagem_parado"]),
        "ditto_dist_ao_personagem_tiles": resumo(dist_ditto),
    }
    saida = {"meta": {"duracao_s": dur, "layout": meta.get("layout")}, "perfil": perfil,
             "rajadas_mover": linhas_raj, "parar": linhas_par, "paradas_do_personagem": linhas_stop,
             "R": [round(x, 2) for x in atac], "revive": [round(x, 2) for x in revs], "marcas": [round(x, 2) for x in marcas],
             "trilha_0_5s": track}
    with open(os.path.join(pasta, "analise_ditto.json"), "w", encoding="utf-8") as fh:
        json.dump(saida, fh, ensure_ascii=False)
    # ---------------- resumo na tela
    print("\n================ RESUMO ================")
    for k, v in perfil.items():
        print(f"{k}: {v}")
    print("\nRajadas de MOVER (t0 -> t1, n, Hz, mouse em tiles [dx,dy], à frente do rumo):")
    for r in linhas_raj[:60]:
        print(f"  {r['t0']:7.1f} -> {r['t1']:7.1f}  n={r['n']:3d}  {r['taxa_hz']} Hz  mouse={r.get('mouse_tiles')}  "
              f"frente={r.get('a_frente_do_rumo')}  andando={r['andando']}")
    print("\nApertos de PARAR (t, parado?, s desde o início da parada do personagem, monstros):")
    for r in linhas_par[:60]:
        v = r["visao"] or {}
        print(f"  {r['t']:7.1f}  parado={r['personagem_parado']}  rel_parada={r['relativo_ao_inicio_da_parada_s']}  "
              f"desde_mover={r['desde_ultimo_mover_s']}  monstros={v.get('n_mon')}  na_area={v.get('mon_na_area_do_ditto')}")
    print(f"\nSalvo em {os.path.join(pasta, 'analise_ditto.json')}  (me envie este arquivo)")


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
