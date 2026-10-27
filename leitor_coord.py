# -*- coding: utf-8 -*-
"""
Leitor da coordenada do jogo (texto "X, Y, Z" abaixo do título do Minimapa).

A fonte do jogo é fixa (branca com contorno preto, 8 px de altura), então a
leitura é feita por tabela de dígitos: sem OCR, uns poucos milissegundos.
Funciona sobre qualquer imagem (RGB, BGR ou BGRA) que contenha o texto.

    from leitor_coord import ler_coord
    ler_coord(imagem)   # -> (689, 1310, 7)  ou None se não conseguiu ler

Região padrão (tela 1920x1080, janela maximizada): (8, 56, 120, 12) = x, y, largura, altura.
"""
import re
import numpy as np

REGIAO_PADRAO = (8, 56, 120, 12)
LIMIAR = 170          # pixel "de texto" = todos os canais acima disso
ALTURA_FONTE = 12     # linhas guardadas por glifo (dígitos 8, vírgula desce até a 9ª)

# Glifos aprendidos dos prints do usuário ('#' = pixel branco do texto)
FONT = {
    ',': [
        '..',
        '..',
        '..',
        '..',
        '..',
        '..',
        '.#',
        '..',
        '#.',
        '..',
        '..',
        '..',
    ],
    '0': [
        '.###.',
        '##.#.',
        '#...#',
        '#...#',
        '#...#',
        '#...#',
        '##.#.',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '1': [
        '##',
        '.#',
        '.#',
        '.#',
        '.#',
        '.#',
        '.#',
        '.#',
        '..',
        '..',
        '..',
        '..',
    ],
    '2': [
        '.###.',
        '##.##',
        '....#',
        '....#',
        '...#.',
        '..#..',
        '.##..',
        '#####',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '3': [
        '.###.',
        '##.##',
        '....#',
        '..##.',
        '...##',
        '....#',
        '.#.##',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '4': [
        '...##.',
        '...##.',
        '..#.#.',
        '.#..#.',
        '.#..#.',
        '######',
        '....#.',
        '....#.',
        '......',
        '......',
        '......',
        '......',
    ],
    '5': [
        '####.',
        '#....',
        '#....',
        '####.',
        '#..##',
        '....#',
        '#..##',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '6': [
        '.###.',
        '##.##',
        '#....',
        '#.##.',
        '#..##',
        '#...#',
        '##.##',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '7': [
        '#####',
        '...##',
        '...#.',
        '...#.',
        '..#..',
        '..#..',
        '..#..',
        '.#...',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '8': [
        '.###.',
        '#..##',
        '#...#',
        '.###.',
        '#..##',
        '#...#',
        '##.##',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
    '9': [
        '.###.',
        '##.##',
        '#...#',
        '#...#',
        '.####',
        '....#',
        '##.##',
        '.###.',
        '.....',
        '.....',
        '.....',
        '.....',
    ],
}

_TABELA = {}
for _c, _rows in FONT.items():
    _bits = "".join(_rows)
    _TABELA[(len(_rows[0]), _bits)] = _c
_PADRAO = re.compile(r"^(\d{2,5}),(\d{2,5}),(\d{1,2})$")


def _grupos(mask1d):
    idx = np.flatnonzero(mask1d)
    if idx.size == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for x in idx[1:]:
        if x != p + 1:
            out.append((s, p))
            s = x
        p = x
    out.append((s, p))
    return out


def _glifo_para_char(sub, tolerancia):
    w = sub.shape[1]
    bits = "".join("#" if v else "." for v in sub.ravel())
    c = _TABELA.get((w, bits))
    if c is not None or tolerancia <= 0:
        return c
    # tolerância pequena (borda suja): vizinho mais próximo de mesma largura
    best, best_d = None, 99
    for (tw, tb), ch in _TABELA.items():
        if tw != w:
            continue
        d = sum(1 for x, y in zip(bits, tb) if x != y)
        if d < best_d:
            best, best_d = ch, d
    return best if best_d <= tolerancia else None


def _tentar(m, r0, tolerancia):
    mm = m[r0:r0 + ALTURA_FONTE]
    if mm.shape[0] < ALTURA_FONTE:
        mm = np.vstack([mm, np.zeros((ALTURA_FONTE - mm.shape[0], mm.shape[1]), bool)])
    cols = _grupos(mm.any(axis=0))
    if not cols:
        return None
    corte = [cols[0]]
    for g in cols[1:]:
        if g[0] - corte[-1][1] - 1 > 10:     # vão grande = outro texto (ex.: relógio)
            break
        corte.append(g)
    chars = []
    for a, b in corte:
        c = _glifo_para_char(mm[:, a:b + 1], tolerancia)
        if c is None:
            return None
        chars.append(c)
    t = "".join(chars)
    return t if _PADRAO.match(t) else None


def ler_texto(img):
    """Lê o texto da coordenada; devolve a string (ex.: '689,1310,7') ou None.
    Tenta cada linha como topo do texto, então a região pode ser folgada
    (pode incluir o FPS acima ou o relógio ao lado)."""
    m = img[:, :, :3].min(axis=2) > LIMIAR
    tops = [r for r in range(0, max(1, m.shape[0] - 7)) if m[r].any()]
    for tol in (0, 3):                       # 1º só casamento exato, depois tolerante
        for r0 in tops:
            t = _tentar(m, r0, tol)
            if t:
                return t
    return None


def ler_coord(img):
    """(x, y, z) ou None."""
    t = ler_texto(img)
    if not t:
        return None
    m = _PADRAO.match(t)
    return tuple(int(v) for v in m.groups()) if m else None


if __name__ == "__main__":
    import sys
    from PIL import Image
    for caminho in sys.argv[1:]:
        im = np.array(Image.open(caminho).convert("RGB"))
        x, y, w, h = REGIAO_PADRAO
        print(caminho, "->", ler_coord(im[y:y + h, x:x + w]))
