# -*- coding: utf-8 -*-
"""
Bot localizador de posição no mapa.

Dependências:
    pip install opencv-python numpy mss pillow

Uso:
    python bot_mapa.py
"""
import os
import sys
import json
import time
import queue
import threading
import subprocess
from datetime import datetime

import numpy as np
import cv2
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Coordenadas físicas corretas no Windows com escala de DPI
if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_BASE = os.path.join(APP_DIR, "log")
CONFIG_FILE = os.path.join(APP_DIR, "config_bot.json")
DEFAULT_MAP = os.path.join(APP_DIR, "mapa_recortado.png")


# ----------------------------------------------------------------------------
# Utilitários de imagem (compatíveis com caminhos com acento no Windows)
# ----------------------------------------------------------------------------
def imread_bgr(path):
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Não foi possível abrir a imagem: {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        # transparência -> branco (= área desconhecida, ignorada no match)
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3] * a + 255.0 * (1 - a)).astype(np.uint8)
    return img


def imwrite(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(path)


def grab_screen_pil():
    import mss
    with mss.mss() as sct:
        mon = sct.monitors[0]  # tela virtual (todos os monitores)
        shot = sct.grab(mon)
        img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, mon["left"], mon["top"]


# ----------------------------------------------------------------------------
# Localizador: acha onde a captura está dentro do mapa grande
# ----------------------------------------------------------------------------
class MapLocator:
    """
    Template matching com máscara e multi-escala.

    - Pixels pretos da captura (área não explorada) e brancos (cursor '+')
      são ignorados.
    - Pixels brancos do mapa grande (área desconhecida) são ignorados.
    - A captura pode "sair" do mapa grande (overlap mínimo configurável).
    - A escala (mapa / captura) é descoberta sozinha e depois reaproveitada.
    """

    def __init__(self, map_bgr, ds=0.5, min_overlap=0.35):
        self.map_full = map_bgr
        self.ds = float(ds)
        self.min_overlap = min_overlap
        small = cv2.resize(map_bgr, None, fx=self.ds, fy=self.ds,
                           interpolation=cv2.INTER_NEAREST)
        self.map_w = small.astype(np.float32) / 255.0
        white = (small > 240).all(axis=2)
        self.valid = (~white).astype(np.float32)
        self.last_scale = None

    # -- máscara da captura -------------------------------------------------
    @staticmethod
    def _capture_mask(cap):
        black = (cap < 12).all(axis=2)
        white = (cap > 235).all(axis=2)
        bad = (black | white).astype(np.uint8)
        bad = cv2.dilate(bad, np.ones((3, 3), np.uint8))
        return (1 - bad).astype(np.float32)

    # -- match em uma escala ------------------------------------------------
    def _match_scale(self, cap, mask, s):
        f = s * self.ds
        ch, cw = cap.shape[:2]
        tw, th = int(round(cw * f)), int(round(ch * f))
        if tw < 8 or th < 8:
            return None
        T = cv2.resize(cap, (tw, th), interpolation=cv2.INTER_NEAREST)
        T = T.astype(np.float32) / 255.0
        M = cv2.resize(mask, (tw, th), interpolation=cv2.INTER_NEAREST)
        msum = float(M.sum())
        if msum < 50:
            return None

        padx = int(tw * (1 - self.min_overlap)) + 1
        pady = int(th * (1 - self.min_overlap)) + 1
        I = cv2.copyMakeBorder(self.map_w, pady, pady, padx, padx,
                               cv2.BORDER_CONSTANT, value=(0, 0, 0))
        V = cv2.copyMakeBorder(self.valid, pady, pady, padx, padx,
                               cv2.BORDER_CONSTANT, value=0)
        I2 = (I * I).sum(axis=2) * V
        t1 = cv2.matchTemplate(I2, M, cv2.TM_CCORR)
        t2 = 0
        for c in range(3):
            t2 = t2 + cv2.matchTemplate(I[:, :, c] * V, M * T[:, :, c],
                                        cv2.TM_CCORR)
        t3 = cv2.matchTemplate(V, M * (T * T).sum(axis=2), cv2.TM_CCORR)
        cnt = cv2.matchTemplate(V, M, cv2.TM_CCORR)

        sse = np.maximum(t1 - 2 * t2 + t3, 0)
        score = sse / (np.maximum(cnt, 1.0) * 3.0)
        frac = cnt / msum
        adj = score + 0.05 * (1.0 - np.minimum(frac, 1.0))
        adj[frac < self.min_overlap] = np.inf
        idx = np.unravel_index(np.argmin(adj), adj.shape)
        y, x = int(idx[0]), int(idx[1])
        if not np.isfinite(adj[y, x]):
            return None
        return {
            "adj": float(adj[y, x]),
            "score": float(score[y, x]),
            "overlap": float(frac[y, x]),
            "scale": s,
            "tx": (x - padx) / self.ds,   # canto sup. esq. no mapa grande (px)
            "ty": (y - pady) / self.ds,
        }

    def _best(self, cap, mask, scales):
        best = None
        for s in scales:
            r = self._match_scale(cap, mask, float(s))
            if r and (best is None or r["adj"] < best["adj"]):
                best = r
        return best

    # -- API principal ------------------------------------------------------
    def locate(self, cap, point, fixed_scale=None):
        mask = self._capture_mask(cap)
        best = None
        if fixed_scale:
            best = self._best(cap, mask, [fixed_scale])
        else:
            if self.last_scale:
                ls = self.last_scale
                best = self._best(cap, mask, [ls * 0.985, ls, ls * 1.015])
            if best is None or best["adj"] > 0.06:
                # calibração completa (só na 1ª vez ou se perder a referência)
                coarse = self._best(cap, mask, np.arange(1.0, 4.01, 0.1))
                if coarse:
                    s0 = coarse["scale"]
                    fine = self._best(cap, mask,
                                      np.arange(s0 - 0.1, s0 + 0.101, 0.02))
                    best = fine or coarse
            if best:
                self.last_scale = best["scale"]
        if best is None:
            return None
        s = best["scale"]
        px, py = point
        best["cx"] = best["tx"] + (px + 0.5) * s   # personagem no mapa grande
        best["cy"] = best["ty"] + (py + 0.5) * s
        best["cap_w"] = cap.shape[1] * s
        best["cap_h"] = cap.shape[0] * s
        return best


# ----------------------------------------------------------------------------
# Imagem de resultado
# ----------------------------------------------------------------------------
def compose_result(cap, big, point, res, ms, n):
    font = cv2.FONT_HERSHEY_SIMPLEX
    px, py = point
    s = res["scale"] if res else 2.0
    k = max(1, int(round(s)))

    left = cv2.resize(cap, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    lx, ly = int((px + 0.5) * k), int((py + 0.5) * k)
    cv2.circle(left, (lx, ly), max(5, k * 2 + 1), (255, 255, 255), 2)
    cv2.circle(left, (lx, ly), max(3, k * 2 - 1), (0, 0, 255), -1)

    right = big.copy()
    if res:
        x0, y0 = int(round(res["tx"])), int(round(res["ty"]))
        x1, y1 = int(round(res["tx"] + res["cap_w"])), int(round(res["ty"] + res["cap_h"]))
        cv2.rectangle(right, (x0, y0), (x1, y1), (0, 255, 255), 2)
        cx, cy = int(round(res["cx"])), int(round(res["cy"]))
        cv2.circle(right, (cx, cy), 9, (255, 255, 255), 2)
        cv2.circle(right, (cx, cy), 6, (0, 0, 255), -1)

    header = 56
    gap = 10
    H = max(left.shape[0], right.shape[0]) + header
    W = left.shape[1] + right.shape[1] + gap
    out = np.full((H, W, 3), 40, np.uint8)
    out[header:header + left.shape[0], :left.shape[1]] = left
    out[header:header + right.shape[0], left.shape[1] + gap:] = right

    cv2.putText(out, f"Captura  #{n}", (6, 20),
                font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(out, "Mapa grande (vermelho = posicao encontrada)",
                (left.shape[1] + gap + 6, 20), font, 0.5, (255, 255, 255), 1,
                cv2.LINE_AA)
    if res:
        txt = (f"Tempo: {ms:.0f} ms | Escala: {res['scale']:.3f} | "
               f"Erro: {res['score']:.4f} | Pos mapa: ({res['cx']:.0f}, {res['cy']:.0f})")
        color = (0, 255, 0) if res["adj"] < 0.06 else (0, 140, 255)
        if res["adj"] >= 0.06:
            txt += " | BAIXA CONFIANCA"
    else:
        txt = f"Tempo: {ms:.0f} ms | Posicao NAO encontrada"
        color = (0, 0, 255)
    cv2.putText(out, txt, (6, 46), font, 0.55, color, 1, cv2.LINE_AA)
    return out


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Bot Localizador de Mapa")
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.thread = None
        self.last_log = None
        self.photo = None

        self.v_map = tk.StringVar(value=DEFAULT_MAP)
        self.v_x = tk.StringVar(value="0")
        self.v_y = tk.StringVar(value="0")
        self.v_w = tk.StringVar(value="173")
        self.v_h = tk.StringVar(value="537")
        self.v_px = tk.StringVar(value="0")
        self.v_py = tk.StringVar(value="0")
        self.v_scale = tk.StringVar(value="auto")
        self.v_ds = tk.StringVar(value="0.5")
        self.v_status = tk.StringVar(value="Parado.")
        self._load_config()

        pad = {"padx": 4, "pady": 3}
        f = ttk.LabelFrame(root, text="Configuração")
        f.pack(fill="x", padx=8, pady=6)

        ttk.Label(f, text="Mapa grande:").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_map, width=55).grid(row=0, column=1, columnspan=6, sticky="we", **pad)
        ttk.Button(f, text="...", width=3, command=self.pick_map).grid(row=0, column=7, **pad)

        ttk.Label(f, text="Área X,Y,L,A:").grid(row=1, column=0, sticky="e", **pad)
        for i, v in enumerate((self.v_x, self.v_y, self.v_w, self.v_h)):
            ttk.Entry(f, textvariable=v, width=7).grid(row=1, column=1 + i, **pad)
        ttk.Button(f, text="Selecionar área", command=self.select_area).grid(row=1, column=5, columnspan=3, sticky="we", **pad)

        ttk.Label(f, text="Personagem X,Y:").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_px, width=7).grid(row=2, column=1, **pad)
        ttk.Entry(f, textvariable=self.v_py, width=7).grid(row=2, column=2, **pad)
        ttk.Button(f, text="Selecionar personagem", command=self.select_point).grid(row=2, column=5, columnspan=3, sticky="we", **pad)

        ttk.Label(f, text="Escala:").grid(row=3, column=0, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_scale, width=7).grid(row=3, column=1, **pad)
        ttk.Label(f, text="(auto ou número, ex: 2.0)").grid(row=3, column=2, columnspan=2, sticky="w", **pad)
        ttk.Label(f, text="Redução:").grid(row=3, column=4, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_ds, width=6).grid(row=3, column=5, **pad)
        ttk.Label(f, text="(0.5 = rápido)").grid(row=3, column=6, columnspan=2, sticky="w", **pad)

        b = ttk.Frame(root)
        b.pack(fill="x", padx=8)
        self.btn_start = ttk.Button(b, text="Iniciar", command=self.start)
        self.btn_start.pack(side="left", padx=4, pady=4)
        self.btn_stop = ttk.Button(b, text="Desligar", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4, pady=4)
        ttk.Button(b, text="Abrir pasta log", command=self.open_log).pack(side="left", padx=4, pady=4)

        ttk.Label(root, textvariable=self.v_status).pack(fill="x", padx=10)
        self.preview = ttk.Label(root)
        self.preview.pack(padx=8, pady=6)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self.poll)

    # -- config -------------------------------------------------------------
    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            for k, v in c.items():
                var = getattr(self, "v_" + k, None)
                if var is not None:
                    var.set(v)
        except Exception:
            pass

    def _save_config(self):
        keys = ["map", "x", "y", "w", "h", "px", "py", "scale", "ds"]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump({k: getattr(self, "v_" + k).get() for k in keys}, fh)
        except Exception:
            pass

    # -- seleção ------------------------------------------------------------
    def pick_map(self):
        p = filedialog.askopenfilename(filetypes=[("Imagens", "*.png *.jpg *.bmp")])
        if p:
            self.v_map.set(p)

    def _hidden_grab(self):
        self.root.withdraw()
        self.root.update()
        time.sleep(0.3)
        img, left, top = grab_screen_pil()
        return img, left, top

    def select_area(self):
        img, left, top = self._hidden_grab()
        W, H = img.size
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{W}x{H}+{left}+{top}")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, width=W, height=H, highlightthickness=0, cursor="crosshair")
        cv.pack()
        photo = ImageTk.PhotoImage(img)
        cv.create_image(0, 0, image=photo, anchor="nw")
        cv.create_text(W // 2, 20, fill="red", font=("Arial", 16, "bold"),
                       text="Arraste para selecionar a área do mapa  (ESC cancela)")
        st = {"x": 0, "y": 0, "r": None}

        def press(e):
            st["x"], st["y"] = e.x, e.y
            if st["r"]:
                cv.delete(st["r"])
            st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

        def drag(e):
            cv.coords(st["r"], st["x"], st["y"], e.x, e.y)

        def release(e):
            x1, x2 = sorted((st["x"], e.x))
            y1, y2 = sorted((st["y"], e.y))
            ov.destroy()
            if x2 - x1 >= 5 and y2 - y1 >= 5:
                self.v_x.set(str(left + x1))
                self.v_y.set(str(top + y1))
                self.v_w.set(str(x2 - x1))
                self.v_h.set(str(y2 - y1))

        cv.bind("<ButtonPress-1>", press)
        cv.bind("<B1-Motion>", drag)
        cv.bind("<ButtonRelease-1>", release)
        ov.bind("<Escape>", lambda e: ov.destroy())
        ov.focus_force()
        ov.wait_window()
        self.root.deiconify()

    def _get_region(self):
        return (int(float(self.v_x.get())), int(float(self.v_y.get())),
                int(float(self.v_w.get())), int(float(self.v_h.get())))

    def select_point(self):
        try:
            x, y, w, h = self._get_region()
            assert w > 0 and h > 0
        except Exception:
            messagebox.showwarning("Aviso", "Defina a área primeiro.")
            return
        import mss
        self.root.withdraw()
        self.root.update()
        time.sleep(0.3)
        with mss.mss() as sct:
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
            img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX")
        self.root.deiconify()

        z = max(1.0, min(1000.0 / w, 750.0 / h))
        if z >= 2:
            z = float(int(z))
        zw, zh = int(w * z), int(h * z)
        top = tk.Toplevel(self.root)
        top.title("Clique no personagem e confirme")
        cv = tk.Canvas(top, width=zw, height=zh, cursor="crosshair")
        cv.pack()
        photo = ImageTk.PhotoImage(img.resize((zw, zh), Image.NEAREST))
        cv.create_image(0, 0, image=photo, anchor="nw")
        st = {"p": None, "ids": []}

        def mark(px, py):
            for i in st["ids"]:
                cv.delete(i)
            cx, cy = (px + 0.5) * z, (py + 0.5) * z
            st["ids"] = [
                cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, outline="red", width=2),
                cv.create_line(cx - 10, cy, cx + 10, cy, fill="red"),
                cv.create_line(cx, cy - 10, cx, cy + 10, fill="red"),
            ]
            st["p"] = (px, py)

        def click(e):
            mark(min(w - 1, int(e.x / z)), min(h - 1, int(e.y / z)))

        def ok():
            if st["p"]:
                self.v_px.set(str(st["p"][0]))
                self.v_py.set(str(st["p"][1]))
            top.destroy()

        cv.bind("<Button-1>", click)
        ttk.Button(top, text="Confirmar", command=ok).pack(pady=4)
        top.bind("<Return>", lambda e: ok())
        try:
            mark(int(self.v_px.get()), int(self.v_py.get()))
        except Exception:
            pass
        top.transient(self.root)
        top.grab_set()
        top.wait_window()

    # -- execução -----------------------------------------------------------
    def start(self):
        try:
            x, y, w, h = self._get_region()
            px, py = int(float(self.v_px.get())), int(float(self.v_py.get()))
            ds = float(self.v_ds.get())
            assert w > 0 and h > 0 and 0 <= px < w and 0 <= py < h and 0.1 <= ds <= 1.0
            st = self.v_scale.get().strip().lower()
            fixed = None if st in ("", "auto") else float(st.replace(",", "."))
            big = imread_bgr(self.v_map.get())
        except Exception as e:
            messagebox.showerror("Erro", f"Configuração inválida:\n{e}")
            return
        self._save_config()

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.last_log = os.path.join(LOG_BASE, stamp)
        os.makedirs(self.last_log, exist_ok=True)

        locator = MapLocator(big, ds=ds)
        self.stop_evt.clear()
        self.thread = threading.Thread(
            target=self.worker,
            args=(locator, big, {"left": x, "top": y, "width": w, "height": h},
                  (px, py), fixed, self.last_log),
            daemon=True)
        self.thread.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.v_status.set(f"Rodando... log: {self.last_log}")

    def stop(self):
        self.stop_evt.set()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.v_status.set("Parado.")

    def worker(self, locator, big, mon, point, fixed, logdir):
        import mss
        n = 0
        try:
            with mss.mss() as sct:
                while not self.stop_evt.is_set():
                    t0 = time.perf_counter()
                    cap = np.array(sct.grab(mon))[:, :, :3].copy()
                    res = locator.locate(cap, point, fixed)
                    ms = (time.perf_counter() - t0) * 1000.0
                    n += 1
                    out = compose_result(cap, big, point, res, ms, n)
                    imwrite(os.path.join(logdir, f"{n:06d}_{ms:.0f}ms.png"), out)
                    self.q.put((out, ms, res, n))
        except Exception as e:
            self.q.put(("erro", str(e)))

    def poll(self):
        last = None
        try:
            while True:
                last = self.q.get_nowait()
        except queue.Empty:
            pass
        if last is not None:
            if last[0] == "erro":
                self.stop()
                messagebox.showerror("Erro no bot", last[1])
            else:
                out, ms, res, n = last
                if res:
                    self.v_status.set(
                        f"#{n} | {ms:.0f} ms | escala {res['scale']:.3f} | "
                        f"erro {res['score']:.4f} | pos ({res['cx']:.0f}, {res['cy']:.0f})")
                else:
                    self.v_status.set(f"#{n} | {ms:.0f} ms | posição não encontrada")
                im = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
                im.thumbnail((1000, 560))
                self.photo = ImageTk.PhotoImage(im)
                self.preview.config(image=self.photo)
        self.root.after(80, self.poll)

    def open_log(self):
        path = self.last_log if self.last_log and os.path.isdir(self.last_log) else None
        if path is None and os.path.isdir(LOG_BASE):
            subs = sorted(d for d in os.listdir(LOG_BASE)
                          if os.path.isdir(os.path.join(LOG_BASE, d)))
            path = os.path.join(LOG_BASE, subs[-1]) if subs else LOG_BASE
        if path is None:
            messagebox.showinfo("Log", "Nenhuma pasta de log criada ainda.")
            return
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def on_close(self):
        self.stop_evt.set()
        self._save_config()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
