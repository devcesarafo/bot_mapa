# -*- coding: utf-8 -*-
"""
Editor do mapa de obstáculos.

Abre o mapa grande (mapa_recortado.png), divide em uma grade de tiles e deixa
você marcar cada tile como ANDÁVEL, BLOQUEADO ou DESCONHECIDO.
Salva em mapa_obstaculos.json (+ um PNG de pré-visualização).

Dependências:
    pip install opencv-python numpy pillow

Uso:
    python editor_mapa.py

Atalhos:
    1 = Andável   2 = Bloqueado   3 = Apagar
    B = Pincel    R = Retângulo   F = Balde   A = Âncora
    [ e ]  = diminui / aumenta o pincel
    Ctrl+Z / Ctrl+Y = desfazer / refazer      Ctrl+S = salvar
    Roda do mouse = zoom     Botão direito ou do meio (arrastar) = mover
    Home = ajustar o mapa à janela

Para usar o resultado no bot, importe a classe ObstacleMap deste arquivo:
    from editor_mapa import ObstacleMap
    om = ObstacleMap.load("mapa_obstaculos.json")
    om.walkable_game(689, 1310)      # True/False pela coordenada do jogo
"""
import os
import re
import json
import math
from collections import deque

import numpy as np
import cv2
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = os.path.join(APP_DIR, "mapa_recortado.png")
DEFAULT_OUT = os.path.join(APP_DIR, "mapa_obstaculos.json")

UNK, WALK, BLOCK = 0, 1, 2
CLASS_NAMES = {UNK: "?", WALK: "Andável", BLOCK: "Bloqueado"}
NAME_TO_CLASS = {v: k for k, v in CLASS_NAMES.items()}
LUT = np.array([[0, 0, 0, 0],
                [30, 144, 255, 105],     # andável  = azul
                [255, 40, 40, 145]],     # bloqueado = vermelho
               np.uint8)


# ----------------------------------------------------------------------------
# Classe para o bot usar o resultado
# ----------------------------------------------------------------------------
class ObstacleMap:
    """Carrega o JSON gerado pelo editor. Tile desconhecido NÃO é andável."""

    def __init__(self, data):
        self.tile = int(data["tile"])
        self.ox = int(data["offset_x"])
        self.oy = int(data["offset_y"])
        self.grid = np.array([[int(c) for c in row] for row in data["grid"]],
                             dtype=np.uint8)
        self.rows, self.cols = self.grid.shape
        self.anchor = data.get("anchor")      # {col,row,x,y,z} ou None

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def walkable(self, col, row):
        return (0 <= row < self.rows and 0 <= col < self.cols
                and self.grid[row, col] == WALK)

    def tile_of_pixel(self, px, py):
        return (int((px - self.ox) // self.tile), int((py - self.oy) // self.tile))

    def tile_of_game(self, x, y):
        if not self.anchor:
            raise ValueError("Mapa sem âncora: defina a âncora no editor.")
        a = self.anchor
        return a["col"] + (x - a["x"]), a["row"] + (y - a["y"])

    def game_of_tile(self, col, row):
        if not self.anchor:
            raise ValueError("Mapa sem âncora: defina a âncora no editor.")
        a = self.anchor
        return a["x"] + (col - a["col"]), a["y"] + (row - a["row"])

    def walkable_game(self, x, y):
        return self.walkable(*self.tile_of_game(x, y))


# ----------------------------------------------------------------------------
# Imagem e grade
# ----------------------------------------------------------------------------
def load_image_rgba(path):
    data = np.fromfile(path, dtype=np.uint8)
    im = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise ValueError(f"Não foi possível abrir a imagem: {path}")
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA)
    elif im.shape[2] == 3:
        im = cv2.cvtColor(im, cv2.COLOR_BGR2BGRA)
    return cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)


def detect_grid(rgba):
    """Descobre o tamanho do tile e o deslocamento pela repetição das bordas."""
    rgb = rgba[:, :, :3].astype(int)
    opaque = rgba[:, :, 3] > 0
    res = []
    for axis in (1, 0):
        d = np.abs(np.diff(rgb, axis=axis)).max(axis=2) > 25
        if axis == 1:
            d &= opaque[:, 1:] & opaque[:, :-1]
            pos = np.nonzero(d)[1] + 1
        else:
            d &= opaque[1:, :] & opaque[:-1, :]
            pos = np.nonzero(d)[0] + 1
        best = None
        if len(pos) >= 50:
            for s in range(2, 41):
                for o in range(s):
                    if np.mean(((pos - o) % s) == 0) >= 0.95:
                        best = (s, o)
        res.append(best)
    sx, sy = res
    tile = sx[0] if sx else (sy[0] if sy else 8)
    ox = sx[1] if sx and sx[0] == tile else 0
    oy = sy[1] if sy and sy[0] == tile else 0
    return tile, ox, oy


def build_tiles(rgba, s, ox, oy):
    """Cor representativa de cada tile + paleta (cores parecidas são fundidas)."""
    H, W = rgba.shape[:2]
    cols = int(math.ceil((W - ox) / s))
    rows = int(math.ceil((H - oy) / s))
    pad = np.zeros((rows * s, cols * s, 4), np.uint8)
    sub = rgba[oy:oy + rows * s, ox:ox + cols * s]
    pad[:sub.shape[0], :sub.shape[1]] = sub
    blocks = pad.reshape(rows, s, cols, s, 4)
    alpha = blocks[..., 3].mean(axis=(1, 3)) / 255.0
    col = np.median(blocks[..., :3], axis=(1, 3))

    flat = col.reshape(-1, 3).round().astype(int)
    opaque = (alpha >= 0.5).reshape(-1)
    pal_idx = -np.ones(rows * cols, int)
    palette, counts = [], []
    if opaque.any():
        u, inv, cnt = np.unique(flat[opaque], axis=0,
                                return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        assign = -np.ones(len(u), int)
        for i in np.argsort(-cnt):
            for j, p in enumerate(palette):
                if np.abs(u[i] - p).max() <= 30:
                    assign[i] = j
                    break
            else:
                palette.append(u[i])
                assign[i] = len(palette) - 1
        pal_idx[opaque] = assign[inv]
        counts = np.bincount(pal_idx[opaque], minlength=len(palette)).tolist()
    return rows, cols, pal_idx.reshape(rows, cols), palette, counts


def hexcolor(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


# ----------------------------------------------------------------------------
# Aplicação
# ----------------------------------------------------------------------------
class Editor:
    def __init__(self, root):
        self.root = root
        root.title("Editor de Mapa de Obstáculos")
        root.geometry("1360x840")

        # dados
        self.map_path = None
        self.rgba = None
        self.base_rgb = None
        self.ov = None
        self.W = self.H = 0
        self.tile, self.ox, self.oy = 8, 0, 0
        self.rows = self.cols = 0
        self.state = self.manual = self.pal_idx = None
        self.palette, self.pal_counts = [], []
        self.legend = {}
        self.hl = None
        self.anchor = None
        self.undo, self.redo = [], []
        self.dirty = False
        self.out_path = DEFAULT_OUT

        # visão
        self.zoom = 4.0
        self.vx = self.vy = 0.0
        self._photo = None
        self._pending = False
        self._pan = None
        self._last_tile = None
        self._rect_start = None
        self._hover = None

        # variáveis de UI
        self.v_tool = tk.StringVar(value="brush")
        self.v_cls = tk.IntVar(value=BLOCK)
        self.v_brush = tk.IntVar(value=1)
        self.v_tile = tk.IntVar(value=8)
        self.v_ox = tk.IntVar(value=0)
        self.v_oy = tk.IntVar(value=0)
        self.v_overlay = tk.BooleanVar(value=True)
        self.v_grid = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar(value="Abra um mapa.")
        self.v_stats = tk.StringVar(value="")

        self._build_ui()
        self._bind()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.startup)

    # -- interface ---------------------------------------------------------
    def _build_ui(self):
        left = ttk.Frame(self.root, width=290)
        left.pack(side="left", fill="y", padx=6, pady=6)
        left.pack_propagate(False)

        f = ttk.LabelFrame(left, text="Arquivo")
        f.pack(fill="x", pady=3)
        r = ttk.Frame(f); r.pack(fill="x", padx=4, pady=3)
        ttk.Button(r, text="Abrir mapa...", command=self.open_map).pack(side="left", expand=True, fill="x")
        ttk.Button(r, text="Abrir JSON...", command=self.open_json).pack(side="left", expand=True, fill="x")
        r = ttk.Frame(f); r.pack(fill="x", padx=4, pady=3)
        ttk.Button(r, text="Salvar (Ctrl+S)", command=self.save).pack(side="left", expand=True, fill="x")
        ttk.Button(r, text="Salvar como...", command=self.save_as).pack(side="left", expand=True, fill="x")

        f = ttk.LabelFrame(left, text="Pintar com")
        f.pack(fill="x", pady=3)
        for txt, val in (("1  Andável (azul)", WALK), ("2  Bloqueado (vermelho)", BLOCK), ("3  Apagar", UNK)):
            ttk.Radiobutton(f, text=txt, variable=self.v_cls, value=val).pack(anchor="w", padx=6)

        f = ttk.LabelFrame(left, text="Ferramenta")
        f.pack(fill="x", pady=3)
        for txt, val in (("B  Pincel", "brush"), ("R  Retângulo", "rect"),
                         ("F  Balde (região da mesma cor)", "fill"), ("A  Âncora (coordenada do jogo)", "anchor")):
            ttk.Radiobutton(f, text=txt, variable=self.v_tool, value=val).pack(anchor="w", padx=6)
        r = ttk.Frame(f); r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="Pincel (tiles):").pack(side="left")
        ttk.Spinbox(r, from_=1, to=15, width=4, textvariable=self.v_brush).pack(side="left", padx=4)

        f = ttk.LabelFrame(left, text="Grade")
        f.pack(fill="x", pady=3)
        r = ttk.Frame(f); r.pack(fill="x", padx=4, pady=3)
        ttk.Label(r, text="Tile (px)").pack(side="left")
        ttk.Spinbox(r, from_=2, to=64, width=4, textvariable=self.v_tile).pack(side="left", padx=2)
        ttk.Label(r, text="X").pack(side="left")
        ttk.Spinbox(r, from_=0, to=63, width=3, textvariable=self.v_ox).pack(side="left", padx=2)
        ttk.Label(r, text="Y").pack(side="left")
        ttk.Spinbox(r, from_=0, to=63, width=3, textvariable=self.v_oy).pack(side="left", padx=2)
        r = ttk.Frame(f); r.pack(fill="x", padx=4, pady=3)
        ttk.Button(r, text="Detectar", command=self.auto_detect).pack(side="left", expand=True, fill="x")
        ttk.Button(r, text="Aplicar grade", command=self.apply_grid).pack(side="left", expand=True, fill="x")
        ttk.Checkbutton(f, text="Mostrar grade", variable=self.v_grid, command=self.schedule).pack(anchor="w", padx=6)
        ttk.Checkbutton(f, text="Mostrar marcação", variable=self.v_overlay, command=self.schedule).pack(anchor="w", padx=6)

        f = ttk.LabelFrame(left, text="Legenda por cor")
        f.pack(fill="both", expand=True, pady=3)
        self.leg_canvas = tk.Canvas(f, highlightthickness=0, height=150)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.leg_canvas.yview)
        self.leg_inner = ttk.Frame(self.leg_canvas)
        self.leg_inner.bind("<Configure>", lambda e: self.leg_canvas.configure(scrollregion=self.leg_canvas.bbox("all")))
        self.leg_canvas.create_window((0, 0), window=self.leg_inner, anchor="nw")
        self.leg_canvas.configure(yscrollcommand=sb.set)
        self.leg_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(left, text="Clique na cor para destacar no mapa.", foreground="#555").pack(anchor="w")
        ttk.Button(left, text="Aplicar legenda a TUDO",
                   command=self.apply_legend_all).pack(fill="x", pady=2)
        ttk.Label(left, textvariable=self.v_stats, justify="left", wraplength=270).pack(anchor="w", pady=2)

        main = ttk.Frame(self.root)
        main.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
        self.canvas = tk.Canvas(main, bg="#202020", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        ttk.Label(main, textvariable=self.v_status, anchor="w").pack(fill="x")

    def _bind(self):
        c = self.canvas
        c.bind("<Configure>", lambda e: self.schedule())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Motion>", self.on_motion)
        for b in (2, 3):
            c.bind(f"<ButtonPress-{b}>", self.pan_start)
            c.bind(f"<B{b}-Motion>", self.pan_move)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.25))
        c.bind("<Button-5>", lambda e: self.zoom_at(e.x, e.y, 0.8))
        r = self.root
        r.bind("1", lambda e: self._key(lambda: self.v_cls.set(WALK)))
        r.bind("2", lambda e: self._key(lambda: self.v_cls.set(BLOCK)))
        r.bind("3", lambda e: self._key(lambda: self.v_cls.set(UNK)))
        r.bind("b", lambda e: self._key(lambda: self.v_tool.set("brush")))
        r.bind("r", lambda e: self._key(lambda: self.v_tool.set("rect")))
        r.bind("f", lambda e: self._key(lambda: self.v_tool.set("fill")))
        r.bind("a", lambda e: self._key(lambda: self.v_tool.set("anchor")))
        r.bind("[", lambda e: self._key(lambda: self.v_brush.set(max(1, self.v_brush.get() - 1))))
        r.bind("]", lambda e: self._key(lambda: self.v_brush.set(min(15, self.v_brush.get() + 1))))
        r.bind("<Control-z>", lambda e: self.do_undo())
        r.bind("<Control-y>", lambda e: self.do_redo())
        r.bind("<Control-s>", lambda e: self.save())
        r.bind("<Home>", lambda e: self.fit_view())

    def _key(self, fn):
        # não captura teclas digitadas em campos de texto/spinbox
        if isinstance(self.root.focus_get(), (ttk.Entry, ttk.Spinbox, tk.Entry)):
            return
        fn()

    # -- carregamento --------------------------------------------------------
    def startup(self):
        if os.path.exists(DEFAULT_OUT):
            try:
                self.load_json(DEFAULT_OUT)
                return
            except Exception as e:
                messagebox.showwarning("Aviso", f"Não consegui abrir {DEFAULT_OUT}:\n{e}")
        if os.path.exists(DEFAULT_MAP):
            self.load_map(DEFAULT_MAP)
        else:
            self.open_map()

    def open_map(self):
        p = filedialog.askopenfilename(filetypes=[("Imagens", "*.png *.jpg *.bmp")])
        if p:
            self.load_map(p)

    def open_json(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            try:
                self.load_json(p)
            except Exception as e:
                messagebox.showerror("Erro", str(e))

    def _set_image(self, path):
        self.rgba = load_image_rgba(path)
        self.map_path = path
        self.H, self.W = self.rgba.shape[:2]
        a = self.rgba[:, :, 3:4].astype(np.float32) / 255.0
        self.base_rgb = (self.rgba[:, :, :3] * a + 40 * (1 - a)).astype(np.uint8)

    def load_map(self, path):
        try:
            self._set_image(path)
        except Exception as e:
            messagebox.showerror("Erro", str(e))
            return
        self.anchor = None
        self.tile, self.ox, self.oy = detect_grid(self.rgba)
        self.v_tile.set(self.tile); self.v_ox.set(self.ox); self.v_oy.set(self.oy)
        self.rebuild_grid(reset_state=True)
        self.fit_view()
        self.dirty = False
        self.v_status.set(f"Mapa {self.W}×{self.H}px, tile {self.tile}px, grade {self.cols}×{self.rows}. "
                          "Defina a legenda por cor e/ou pinte direto.")

    def load_json(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        mp = d.get("map_file", "")
        cand = [mp, os.path.join(os.path.dirname(path), mp), os.path.join(os.path.dirname(path), os.path.basename(mp)), DEFAULT_MAP]
        mp = next((c for c in cand if c and os.path.exists(c)), None)
        if mp is None:
            mp = filedialog.askopenfilename(title="Onde está o mapa grande?",
                                            filetypes=[("Imagens", "*.png *.jpg *.bmp")])
            if not mp:
                raise ValueError("Mapa não encontrado.")
        self._set_image(mp)
        self.tile, self.ox, self.oy = int(d["tile"]), int(d["offset_x"]), int(d["offset_y"])
        self.v_tile.set(self.tile); self.v_ox.set(self.ox); self.v_oy.set(self.oy)
        self.rebuild_grid(reset_state=True)
        g = d["grid"]
        if len(g) == self.rows and all(len(r) == self.cols for r in g):
            self.state = np.array([[int(c) for c in r] for r in g], np.uint8)
            m = d.get("manual_grid")
            if m and len(m) == self.rows:
                self.manual = np.array([[c == "1" for c in r] for r in m], bool)
        hexes = {hexcolor(p): i for i, p in enumerate(self.palette)}
        self.legend = {hexes[h]: int(v) for h, v in d.get("legend", {}).items() if h in hexes}
        self.anchor = d.get("anchor")
        self.out_path = path
        self.refresh_legend()
        self.rebuild_overlay()
        self.fit_view()
        self.dirty = False
        self.v_status.set(f"Carregado {os.path.basename(path)}.")

    # -- grade -----------------------------------------------------------------
    def rebuild_grid(self, reset_state):
        self.rows, self.cols, self.pal_idx, self.palette, self.pal_counts = \
            build_tiles(self.rgba, self.tile, self.ox, self.oy)
        if reset_state:
            self.state = np.zeros((self.rows, self.cols), np.uint8)
            self.manual = np.zeros((self.rows, self.cols), bool)
            self.legend = {}
            self.undo, self.redo = [], []
            self.hl = None
        self.refresh_legend()
        self.rebuild_overlay()

    def auto_detect(self):
        if self.rgba is None:
            return
        t, ox, oy = detect_grid(self.rgba)
        self.v_tile.set(t); self.v_ox.set(ox); self.v_oy.set(oy)
        self.v_status.set(f"Detectado: tile {t}px, deslocamento ({ox},{oy}). Clique em Aplicar grade.")

    def apply_grid(self):
        if self.rgba is None:
            return
        try:
            t, ox, oy = int(self.v_tile.get()), int(self.v_ox.get()), int(self.v_oy.get())
        except tk.TclError:
            return
        if t < 2:
            return
        if (t, ox, oy) == (self.tile, self.ox, self.oy):
            return
        if self.manual is not None and (self.manual.any() or self.legend):
            if not messagebox.askyesno("Trocar a grade", "Mudar a grade apaga a marcação atual. Continuar?"):
                self.v_tile.set(self.tile); self.v_ox.set(self.ox); self.v_oy.set(self.oy)
                return
        self.tile, self.ox, self.oy = t, ox, oy
        self.anchor = None
        self.rebuild_grid(reset_state=True)
        self.dirty = True
        self.schedule()

    # -- legenda ---------------------------------------------------------------
    def refresh_legend(self):
        for w in self.leg_inner.winfo_children():
            w.destroy()
        self.leg_vars = {}
        order = sorted(range(len(self.palette)), key=lambda i: -self.pal_counts[i])
        for i in order:
            row = ttk.Frame(self.leg_inner)
            row.pack(fill="x", pady=1)
            sw = tk.Label(row, bg=hexcolor(self.palette[i]), width=4, relief="solid", bd=1, cursor="hand2")
            sw.pack(side="left", padx=2)
            sw.bind("<Button-1>", lambda e, k=i: self.toggle_highlight(k))
            ttk.Label(row, text=f"{self.pal_counts[i]} tiles", width=9).pack(side="left")
            var = tk.StringVar(value=CLASS_NAMES[self.legend.get(i, UNK)])
            cb = ttk.Combobox(row, textvariable=var, state="readonly", width=10,
                              values=[CLASS_NAMES[UNK], CLASS_NAMES[WALK], CLASS_NAMES[BLOCK]])
            cb.pack(side="left", padx=2)
            cb.bind("<<ComboboxSelected>>", lambda e, k=i, v=var: self.set_legend(k, NAME_TO_CLASS[v.get()]))
            self.leg_vars[i] = var

    def toggle_highlight(self, k):
        self.hl = None if self.hl == k else k
        self.rebuild_overlay()
        self.schedule()

    def set_legend(self, k, cls):
        self.push_undo()
        self.legend[k] = cls
        m = (self.pal_idx == k) & (~self.manual)
        self.state[m] = cls
        self.dirty = True
        self.rebuild_overlay()
        self.schedule()

    def apply_legend_all(self):
        if self.manual.any() and not messagebox.askyesno(
                "Aplicar legenda", "Isso sobrescreve a pintura manual. Continuar?"):
            return
        self.push_undo()
        for k, cls in self.legend.items():
            self.state[self.pal_idx == k] = cls
        self.state[self.pal_idx < 0] = UNK
        self.manual[:] = False
        self.dirty = True
        self.rebuild_overlay()
        self.schedule()

    # -- overlay e desenho -------------------------------------------------------
    def rebuild_overlay(self):
        if self.state is None:
            return
        s = self.tile
        t = LUT[self.state].copy()
        if self.hl is not None:
            t[self.pal_idx == self.hl] = (255, 255, 0, 175)
        big = np.repeat(np.repeat(t, s, axis=0), s, axis=1)
        ov = np.zeros((self.H, self.W, 4), np.uint8)
        h = max(0, min(self.H - self.oy, big.shape[0]))
        w = max(0, min(self.W - self.ox, big.shape[1]))
        ov[self.oy:self.oy + h, self.ox:self.ox + w] = big[:h, :w]
        self.ov = ov
        n = np.bincount(self.state.ravel(), minlength=3)
        self.v_stats.set(f"Andável: {n[WALK]}\nBloqueado: {n[BLOCK]}\nDesconhecido: {n[UNK]}")

    def schedule(self):
        if not self._pending:
            self._pending = True
            self.root.after_idle(self.redraw)

    def redraw(self):
        self._pending = False
        c = self.canvas
        c.delete("all")
        if self.base_rgb is None:
            return
        cw, ch = c.winfo_width(), c.winfo_height()
        z = self.zoom
        x0, y0 = self.vx, self.vy
        ix0, iy0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
        ix1 = min(self.W, int(math.ceil(x0 + cw / z)))
        iy1 = min(self.H, int(math.ceil(y0 + ch / z)))
        if ix1 > ix0 and iy1 > iy0:
            base = self.base_rgb[iy0:iy1, ix0:ix1]
            if self.v_overlay.get() and self.ov is not None:
                ov = self.ov[iy0:iy1, ix0:ix1]
                a = ov[..., 3:4].astype(np.float32) / 255.0
                out = (base * (1 - a) + ov[..., :3] * a).astype(np.uint8)
            else:
                out = base
            size = (max(1, int(round((ix1 - ix0) * z))), max(1, int(round((iy1 - iy0) * z))))
            img = Image.fromarray(out).resize(size, Image.NEAREST)
            self._photo = ImageTk.PhotoImage(img)
            c.create_image((ix0 - x0) * z, (iy0 - y0) * z, image=self._photo, anchor="nw")

        s = self.tile
        if self.v_grid.get() and s * z >= 6:
            c0 = int(math.floor((x0 - self.ox) / s)); c1 = int(math.ceil((x0 + cw / z - self.ox) / s))
            r0 = int(math.floor((y0 - self.oy) / s)); r1 = int(math.ceil((y0 + ch / z - self.oy) / s))
            for cc in range(max(0, c0), min(self.cols, c1) + 1):
                ex = (self.ox + cc * s - x0) * z
                c.create_line(ex, 0, ex, ch, fill="#262626")
            for rr in range(max(0, r0), min(self.rows, r1) + 1):
                ey = (self.oy + rr * s - y0) * z
                c.create_line(0, ey, cw, ey, fill="#262626")

        if self.anchor:
            a = self.anchor
            ex, ey = self.tile_to_screen(a["col"] + 0.5, a["row"] + 0.5)
            c.create_oval(ex - 7, ey - 7, ex + 7, ey + 7, outline="#ff00ff", width=2)
            c.create_text(ex + 10, ey - 10, text="âncora", fill="#ff00ff", anchor="sw")
        if self._hover and self.tool_is_paint():
            self._draw_brush(self._hover)
        if self._rect_start and self._hover:
            (c0, r0), (c1, r1) = self._rect_start, self._hover
            ax, ay = self.tile_to_screen(min(c0, c1), min(r0, r1))
            bx, by = self.tile_to_screen(max(c0, c1) + 1, max(r0, r1) + 1)
            c.create_rectangle(ax, ay, bx, by, outline="#ffff00", width=2)

    def tool_is_paint(self):
        return self.v_tool.get() in ("brush", "fill", "anchor", "rect")

    def _draw_brush(self, tile):
        col, row = tile
        if self.v_tool.get() == "brush":
            n = max(1, self.v_brush.get()); half = n // 2
            ax, ay = self.tile_to_screen(col - half, row - half)
            bx, by = self.tile_to_screen(col - half + n, row - half + n)
        else:
            ax, ay = self.tile_to_screen(col, row)
            bx, by = self.tile_to_screen(col + 1, row + 1)
        self.canvas.create_rectangle(ax, ay, bx, by, outline="#ffffff", width=1)

    # -- coordenadas -------------------------------------------------------------
    def tile_to_screen(self, col, row):
        return ((self.ox + col * self.tile - self.vx) * self.zoom,
                (self.oy + row * self.tile - self.vy) * self.zoom)

    def screen_to_tile(self, ex, ey):
        mx = self.vx + ex / self.zoom
        my = self.vy + ey / self.zoom
        return (int(math.floor((mx - self.ox) / self.tile)),
                int(math.floor((my - self.oy) / self.tile)))

    def inside(self, col, row):
        return 0 <= col < self.cols and 0 <= row < self.rows

    def game_of(self, col, row):
        if not self.anchor:
            return None
        a = self.anchor
        return a["x"] + (col - a["col"]), a["y"] + (row - a["row"]), a["z"]

    def fit_view(self):
        if self.base_rgb is None:
            return
        cw, ch = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        self.zoom = max(0.5, min(cw / self.W, ch / self.H))
        self.vx = self.vy = 0.0
        self.schedule()

    def zoom_at(self, ex, ey, factor):
        if self.base_rgb is None:
            return
        mx = self.vx + ex / self.zoom
        my = self.vy + ey / self.zoom
        self.zoom = max(0.5, min(48.0, self.zoom * factor))
        self.vx = mx - ex / self.zoom
        self.vy = my - ey / self.zoom
        self.schedule()

    def on_wheel(self, e):
        self.zoom_at(e.x, e.y, 1.25 if e.delta > 0 else 0.8)

    def pan_start(self, e):
        self._pan = (e.x, e.y, self.vx, self.vy)

    def pan_move(self, e):
        if self._pan:
            sx, sy, vx, vy = self._pan
            self.vx = vx - (e.x - sx) / self.zoom
            self.vy = vy - (e.y - sy) / self.zoom
            self.schedule()

    # -- pintura -------------------------------------------------------------------
    def push_undo(self):
        self.undo.append((self.state.copy(), self.manual.copy()))
        if len(self.undo) > 200:
            self.undo.pop(0)
        self.redo.clear()

    def do_undo(self):
        if self.undo:
            self.redo.append((self.state.copy(), self.manual.copy()))
            self.state, self.manual = self.undo.pop()
            self.dirty = True
            self.rebuild_overlay(); self.schedule()

    def do_redo(self):
        if self.redo:
            self.undo.append((self.state.copy(), self.manual.copy()))
            self.state, self.manual = self.redo.pop()
            self.dirty = True
            self.rebuild_overlay(); self.schedule()

    def paint_box(self, c0, r0, c1, r1):
        c0, c1 = max(0, c0), min(self.cols, c1)
        r0, r1 = max(0, r0), min(self.rows, r1)
        if c1 > c0 and r1 > r0:
            self.state[r0:r1, c0:c1] = self.v_cls.get()
            self.manual[r0:r1, c0:c1] = True
            self.dirty = True

    def paint_brush(self, col, row):
        n = max(1, self.v_brush.get()); half = n // 2
        self.paint_box(col - half, row - half, col - half + n, row - half + n)

    def paint_line(self, a, b):
        (c0, r0), (c1, r1) = a, b
        steps = max(abs(c1 - c0), abs(r1 - r0), 1)
        for i in range(steps + 1):
            t = i / steps
            self.paint_brush(int(round(c0 + (c1 - c0) * t)), int(round(r0 + (r1 - r0) * t)))

    def flood(self, col, row):
        if not self.inside(col, row):
            return
        target = self.pal_idx[row, col]
        cls = self.v_cls.get()
        seen = np.zeros_like(self.manual)
        q = deque([(row, col)])
        seen[row, col] = True
        while q:
            r, c = q.popleft()
            self.state[r, c] = cls
            self.manual[r, c] = True
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < self.rows and 0 <= cc < self.cols and not seen[rr, cc] \
                        and self.pal_idx[rr, cc] == target:
                    seen[rr, cc] = True
                    q.append((rr, cc))
        self.dirty = True

    def set_anchor(self, col, row):
        if not self.inside(col, row):
            return
        init = ""
        if self.anchor:
            a = self.anchor
            init = f"{a['x']}, {a['y']}, {a['z']}"
        txt = simpledialog.askstring(
            "Âncora",
            f"Tile ({col}, {row}).\nDigite a coordenada do jogo desse tile (X, Y, Z),\n"
            "como aparece sob o título do Minimapa. Ex.: 689, 1310, 7",
            initialvalue=init, parent=self.root)
        if not txt:
            return
        m = re.findall(r"-?\d+", txt)
        if len(m) != 3:
            messagebox.showwarning("Aviso", "Digite três números: X, Y, Z.")
            return
        x, y, z = map(int, m)
        self.anchor = {"col": col, "row": row, "x": x, "y": y, "z": z}
        self.dirty = True
        self.schedule()

    # -- eventos do mouse ----------------------------------------------------------
    def on_press(self, e):
        if self.state is None:
            return
        col, row = self.screen_to_tile(e.x, e.y)
        tool = self.v_tool.get()
        if tool == "brush":
            self.push_undo()
            self._last_tile = (col, row)
            self.paint_brush(col, row)
            self.rebuild_overlay()
        elif tool == "rect":
            self._rect_start = (col, row)
        elif tool == "fill":
            self.push_undo()
            self.flood(col, row)
            self.rebuild_overlay()
        elif tool == "anchor":
            self.set_anchor(col, row)
        self._hover = (col, row)
        self.schedule()

    def on_drag(self, e):
        if self.state is None:
            return
        col, row = self.screen_to_tile(e.x, e.y)
        if self.v_tool.get() == "brush" and self._last_tile:
            self.paint_line(self._last_tile, (col, row))
            self._last_tile = (col, row)
            self.rebuild_overlay()
        self._hover = (col, row)
        self.update_status(col, row)
        self.schedule()

    def on_release(self, e):
        if self.state is None:
            return
        col, row = self.screen_to_tile(e.x, e.y)
        if self.v_tool.get() == "rect" and self._rect_start:
            (c0, r0) = self._rect_start
            self.push_undo()
            self.paint_box(min(c0, col), min(r0, row), max(c0, col) + 1, max(r0, row) + 1)
            self.rebuild_overlay()
        self._rect_start = None
        self._last_tile = None
        self.schedule()

    def on_motion(self, e):
        if self.state is None:
            return
        col, row = self.screen_to_tile(e.x, e.y)
        self._hover = (col, row)
        self.update_status(col, row)
        self.schedule()

    def update_status(self, col, row):
        if not self.inside(col, row):
            return
        st = CLASS_NAMES[int(self.state[row, col])]
        pi = int(self.pal_idx[row, col])
        cor = hexcolor(self.palette[pi]) if pi >= 0 else "transparente"
        g = self.game_of(col, row)
        gtxt = f" | jogo: {g[0]}, {g[1]}, {g[2]}" if g else ""
        self.v_status.set(f"Tile ({col}, {row}) | {st} | cor {cor}{gtxt}")

    # -- salvar ----------------------------------------------------------------------
    def save_as(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")],
                                         initialfile=os.path.basename(self.out_path))
        if p:
            self.out_path = p
            self.save()

    def save(self):
        if self.state is None:
            return
        data = {
            "map_file": os.path.relpath(self.map_path, os.path.dirname(self.out_path))
            if self._same_drive(self.map_path, self.out_path) else self.map_path,
            "map_width": self.W, "map_height": self.H,
            "tile": self.tile, "offset_x": self.ox, "offset_y": self.oy,
            "cols": self.cols, "rows": self.rows,
            "legend_note": "0 = desconhecido, 1 = andavel, 2 = bloqueado",
            "anchor": self.anchor,
            "legend": {hexcolor(self.palette[k]): int(v) for k, v in self.legend.items()},
            "grid": ["".join(str(int(v)) for v in r) for r in self.state],
            "manual_grid": ["".join("1" if v else "0" for v in r) for r in self.manual],
        }
        try:
            with open(self.out_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            self.save_preview(os.path.splitext(self.out_path)[0] + "_preview.png")
        except Exception as e:
            messagebox.showerror("Erro ao salvar", str(e))
            return
        self.dirty = False
        self.v_status.set(f"Salvo em {self.out_path}")

    @staticmethod
    def _same_drive(a, b):
        return os.path.splitdrive(os.path.abspath(a))[0] == os.path.splitdrive(os.path.abspath(b))[0]

    def save_preview(self, path):
        a = self.ov[..., 3:4].astype(np.float32) / 255.0
        out = (self.base_rgb * (1 - a) + self.ov[..., :3] * a).astype(np.uint8)
        img = Image.fromarray(out).resize((self.W * 2, self.H * 2), Image.NEAREST)
        img.save(path)

    def on_close(self):
        if self.dirty and not messagebox.askyesno("Sair", "Há alterações não salvas. Sair mesmo assim?"):
            return
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Editor(root)
    root.mainloop()
