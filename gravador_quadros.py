# -*- coding: utf-8 -*-
"""
Gravador de quadros + calibrador de layout.

NÃO controla o jogo. Só observa:
  - salva a tela inteira (monitor principal) várias vezes por segundo em JPEG;
  - lê a coordenada do jogo em cada quadro;
  - registra os SEUS apertos dos atalhos do jogo (revive, atacar, parar, mover,
    pokébola; podem ser teclas OU botões do mouse) e de WASD, com o instante exato
    e a posição do mouse. Só os atalhos configurados, nada além disso.
Com isso dá para estudar um lure inteiro depois: quando a vida caiu, quando
você apertou R, quando os anéis do stun apareceram, quando os monstros chegaram.

1) CALIBRAR: marque na tela o retângulo de cada elemento (coordenada, vida do
   treinador, vida do Ditto, Battle list, área do jogo). Clique em "Salvar layout".
   Isso gera o layout.json e atualiza o config_gravador.json (as outras
   ferramentas passam a usar a nova região da coordenada).
2) GRAVAR: Ctrl+Alt+G inicia/para, Ctrl+Alt+M coloca uma marca no registro.
   Rode como ADMINISTRADOR (o jogo é administrador; senão os atalhos e o registro
   de teclas não funcionam com o jogo em foco).

Saída:  quadros/AAAA-MM-DD_HH-MM-SS/
          frames/000001.jpg ...
          index.jsonl   (um quadro por linha: n, t, coordenada, descartado)
          eventos.jsonl (teclas e marcas, com o instante t)
          meta.json     (layout, fps, tamanho da tela, totais)

Dependências:  pip install opencv-python numpy pillow mss pynput
Arquivos na mesma pasta: leitor_coord.py, atalhos.py
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
from PIL import Image, ImageTk, ImageDraw
import tkinter as tk
from tkinter import ttk, messagebox

from leitor_coord import ler_coord
from atalhos import Vigia, capturar_atalho, descrever_lista

if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    from pynput import keyboard as pk
except Exception:
    pk = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA = os.path.join(APP_DIR, "quadros")
LAYOUT_FILE = os.path.join(APP_DIR, "layout.json")
CFG_GRAVADOR = os.path.join(APP_DIR, "config_gravador.json")

# retângulos (x, y, largura, altura) no monitor principal, 1920x1080, estimados do print do usuário
LAYOUT_PADRAO = {
    "coordenada": (1730, 54, 120, 14),
    "vida_treinador": (545, 934, 255, 18),
    "vida_ditto_pokebar": (8, 52, 195, 45),
    "battle_list": (5, 195, 185, 835),
    "area_jogo": (200, 93, 1519, 795),
}
NOMES = {
    "coordenada": "Coordenada (X, Y, Z)",
    "vida_treinador": "Vida do treinador (número + barra verde)",
    "vida_ditto_pokebar": "Pokebar: linha do Ditto (% e barras)",
    "battle_list": "Battle list (painel inteiro)",
    "area_jogo": "Área do jogo (21x11 tiles)",
}
FUNCOES = {"revive": "Revive", "atacar": "Atacar (R)", "parar_ditto": "Parar o Ditto",
           "mover_ditto": "Mover o Ditto", "pokebola": "Pokébola",
           "defesa": "Skill de defesa (F)", "cura": "Skill de cura (C)",
           "velocidade": "Skill de velocidade (G)", "stun": "Skill de stun (Whirlwind)"}
# usuario = atalhos que VOCÊ aperta (são registrados na gravação);
# bot     = atalhos que o BOT vai apertar (pode ser outro, o jogo aceita vários por função).
ATALHOS_PADRAO = {
    "usuario": {"revive": ["key:e"], "atacar": ["key:r"], "parar_ditto": ["key:p"],
                "mover_ditto": ["key:o"], "pokebola": ["key:space"], "defesa": [], "cura": [],
                "velocidade": [], "stun": []},
    "bot": {"revive": ["key:e"], "atacar": ["key:r"], "parar_ditto": [],
            "mover_ditto": [], "pokebola": ["key:space"], "defesa": [], "cura": [],
            "velocidade": [], "stun": []},
}
TECLAS_MOVIMENTO = ["key:w", "key:a", "key:s", "key:d"]


# ----------------------------------------------------------------------------
# Tela
# ----------------------------------------------------------------------------
def monitor_principal(sct):
    return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]


def selecionar_retangulo(root, titulo):
    """Mostra a tela congelada e deixa arrastar um retângulo. -> (x, y, w, h) ou None."""
    import mss
    root.withdraw()
    root.update()
    time.sleep(0.3)
    with mss.mss() as sct:
        mon = monitor_principal(sct)
        shot = sct.grab(mon)
        img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX")
    root.deiconify()
    W, H = img.size
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    ov.geometry(f"{W}x{H}+{mon['left']}+{mon['top']}")
    ov.attributes("-topmost", True)
    cv = tk.Canvas(ov, width=W, height=H, highlightthickness=0, cursor="crosshair")
    cv.pack()
    photo = ImageTk.PhotoImage(img)
    cv.create_image(0, 0, image=photo, anchor="nw")
    cv.create_text(W // 2, 22, fill="red", font=("Arial", 16, "bold"),
                   text=f"{titulo}: arraste um retângulo. ESC cancela")
    st = {"x": 0, "y": 0, "r": None, "res": None}

    def press(e):
        st["x"], st["y"] = e.x, e.y
        st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

    def drag(e):
        cv.coords(st["r"], st["x"], st["y"], e.x, e.y)

    def release(e):
        x1, x2 = sorted((st["x"], e.x))
        y1, y2 = sorted((st["y"], e.y))
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            st["res"] = (x1, y1, x2 - x1, y2 - y1)
        ov.destroy()

    cv.bind("<ButtonPress-1>", press)
    cv.bind("<B1-Motion>", drag)
    cv.bind("<ButtonRelease-1>", release)
    ov.bind("<Escape>", lambda e: ov.destroy())
    ov.focus_force()
    ov.wait_window()
    return st["res"]


# ----------------------------------------------------------------------------
# Gravação (independe da interface)
# ----------------------------------------------------------------------------
class Gravacao:
    def __init__(self, pasta, fps, qualidade, regiao_coord, max_s, layout, teclas):
        self.pasta = pasta
        self.fps, self.qualidade, self.max_s = fps, qualidade, max_s
        self.rc = regiao_coord
        self.layout, self.teclas = layout, teclas
        os.makedirs(os.path.join(pasta, "frames"), exist_ok=True)
        self.q = queue.Queue(maxsize=40)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.n = 0
        self.salvos = 0
        self.descartados = 0
        self.bytes = 0
        self.ultima_coord = None
        self.t0 = None
        self.f_index = open(os.path.join(pasta, "index.jsonl"), "w", encoding="utf-8", buffering=1)
        self.f_ev = open(os.path.join(pasta, "eventos.jsonl"), "w", encoding="utf-8", buffering=1)
        self.threads = []
        self.tam = None
        self.duracao = 0.0

    def t(self):
        return time.perf_counter() - self.t0

    def evento(self, tipo, **kw):
        if self.t0 is None:
            return
        d = {"t": round(self.t(), 3), "tipo": tipo}
        d.update(kw)
        with self.lock:
            self.f_ev.write(json.dumps(d, ensure_ascii=False) + "\n")

    def iniciar(self):
        self.t0 = time.perf_counter()
        self.threads = [threading.Thread(target=self._captura, daemon=True)]
        self.threads += [threading.Thread(target=self._escritor, daemon=True) for _ in range(2)]
        for t in self.threads:
            t.start()

    def _captura(self):
        import mss
        periodo = 1.0 / self.fps
        with mss.mss() as sct:
            mon = monitor_principal(sct)
            self.tam = (mon["width"], mon["height"])
            x, y, w, h = self.rc
            proximo = time.perf_counter()
            while not self.stop.is_set():
                agora = time.perf_counter()
                if agora - self.t0 >= self.max_s:
                    self.stop.set()
                    break
                arr = np.array(sct.grab(mon))                     # BGRA
                t = agora - self.t0
                coord = ler_coord(arr[y:y + h, x:x + w]) if w > 0 else None
                self.n += 1
                self.ultima_coord = coord
                linha = {"n": self.n, "t": round(t, 3), "coord": list(coord) if coord else None}
                try:
                    self.q.put_nowait((self.n, arr))
                except queue.Full:
                    self.descartados += 1
                    linha["descartado"] = True
                with self.lock:
                    self.f_index.write(json.dumps(linha) + "\n")
                proximo += periodo
                espera = proximo - time.perf_counter()
                if espera > 0:
                    time.sleep(espera)
                elif espera < -2 * periodo:
                    proximo = time.perf_counter()                 # atrasou: não acumula
        self.duracao = self.t()

    def _escritor(self):
        while True:
            try:
                n, arr = self.q.get(timeout=0.2)
            except queue.Empty:
                if self.stop.is_set() and self._captura_acabou():
                    return
                continue
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, self.qualidade])
            if ok:
                buf.tofile(os.path.join(self.pasta, "frames", f"{n:06d}.jpg"))
                with self.lock:
                    self.salvos += 1
                    self.bytes += buf.size

    def _captura_acabou(self):
        return not self.threads[0].is_alive() and self.q.empty()

    def parar(self):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=15)
        self.f_index.close()
        self.f_ev.close()
        meta = {
            "criado_em": os.path.basename(self.pasta),
            "tela": list(self.tam) if self.tam else None,
            "fps_pedido": self.fps, "qualidade_jpeg": self.qualidade,
            "quadros_capturados": self.n, "quadros_salvos": self.salvos,
            "quadros_descartados": self.descartados,
            "duracao_s": round(self.duracao, 2),
            "fps_real": round(self.n / self.duracao, 2) if self.duracao else None,
            "tamanho_mb": round(self.bytes / 1e6, 1),
            "layout": self.layout, "atalhos": self.teclas,
        }
        with open(os.path.join(self.pasta, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)
        return meta


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Gravador de Quadros + Calibrador de Layout")
        root.geometry("820x760")
        self.q = queue.Queue()
        self.grav = None
        self.pasta = None
        self.hk = None
        self.vigia = None
        self.v_reg = {k: [tk.StringVar(value=str(v)) for v in LAYOUT_PADRAO[k]] for k in LAYOUT_PADRAO}
        self.atalhos = {q: {f: list(v) for f, v in d.items()} for q, d in ATALHOS_PADRAO.items()}
        self.v_atalho = {q: {f: tk.StringVar() for f in FUNCOES} for q in ("usuario", "bot")}
        self.v_fps = tk.StringVar(value="10")
        self.v_qual = tk.StringVar(value="90")
        self.v_max = tk.StringVar(value="180")
        self.v_logteclas = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar(value="Parado.")
        self._carregar_layout()

        f = ttk.LabelFrame(root, text="1) Layout: retângulo de cada elemento (monitor principal, em pixels)")
        f.pack(fill="x", padx=8, pady=6)
        for i, k in enumerate(LAYOUT_PADRAO):
            ttk.Label(f, text=NOMES[k], width=40).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            for j, v in enumerate(self.v_reg[k]):
                ttk.Entry(f, textvariable=v, width=6).grid(row=i, column=1 + j, padx=1)
            ttk.Button(f, text="Selecionar", command=lambda kk=k: self.selecionar(kk)).grid(row=i, column=5, padx=4)
        r = ttk.Frame(f)
        r.grid(row=len(LAYOUT_PADRAO), column=0, columnspan=6, sticky="we", pady=4)
        ttk.Button(r, text="Prévia do layout", command=self.previa).pack(side="left", padx=4)
        ttk.Button(r, text="Testar leitura da coordenada", command=self.testar_coord).pack(side="left", padx=4)
        ttk.Button(r, text="Salvar layout", command=self.salvar_layout).pack(side="left", padx=4)

        f = ttk.LabelFrame(root, text="Atalhos do jogo: clique em Gravar, aperte a tecla ou o botão do mouse e confirme")
        f.pack(fill="x", padx=8, pady=4)
        ttk.Label(f, text="Função", font=("Arial", 9, "bold")).grid(row=0, column=0, padx=4, sticky="w")
        ttk.Label(f, text="Os que EU uso (registrados na gravação)", font=("Arial", 9, "bold")).grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Label(f, text="Os que o BOT vai usar", font=("Arial", 9, "bold")).grid(row=0, column=4, columnspan=3, sticky="w")
        for i, (fn, nome) in enumerate(FUNCOES.items(), start=1):
            ttk.Label(f, text=nome).grid(row=i, column=0, padx=4, sticky="w")
            for c0, q in ((1, "usuario"), (4, "bot")):
                ttk.Label(f, textvariable=self.v_atalho[q][fn], width=34, wraplength=250, foreground="#064").grid(row=i, column=c0, padx=2, sticky="w")
                ttk.Button(f, text="+ Gravar", width=9, command=lambda ff=fn, qq=q: self.gravar_atalho(ff, qq)).grid(row=i, column=c0 + 1, padx=1)
                ttk.Button(f, text="Limpar", width=7, command=lambda ff=fn, qq=q: self.limpar_atalho(ff, qq)).grid(row=i, column=c0 + 2, padx=1)
        ttk.Label(f, text="Dá para ter vários atalhos por função. Para o bot, configure no jogo um segundo atalho "
                          "(de teclado) para 'mover' e 'parar'.", foreground="#666").grid(row=len(FUNCOES) + 1, column=0, columnspan=7, sticky="w", padx=4)
        self._atualizar_atalhos()

        f = ttk.LabelFrame(root, text="2) Gravação")
        f.pack(fill="x", padx=8, pady=4)
        g = ttk.Frame(f)
        g.pack(fill="x", padx=4, pady=3)
        ttk.Label(g, text="Quadros/s").pack(side="left")
        ttk.Combobox(g, textvariable=self.v_fps, values=["5", "10", "15", "20"], width=4, state="readonly").pack(side="left", padx=4)
        ttk.Label(g, text="Qualidade JPEG").pack(side="left")
        ttk.Spinbox(g, from_=60, to=100, width=4, textvariable=self.v_qual).pack(side="left", padx=4)
        ttk.Label(g, text="Duração máx. (s)").pack(side="left")
        ttk.Entry(g, textvariable=self.v_max, width=6).pack(side="left", padx=4)
        ttk.Checkbutton(f, text="Registrar meus apertos das teclas do jogo (os atalhos que EU uso + WASD, com a posição do mouse)",
                        variable=self.v_logteclas).pack(anchor="w", padx=6)
        ttk.Label(f, text="Estimativa: ~0,5 MB por quadro (10 quadros/s = ~5 MB/s, ~1 GB a cada 3 min). Mantenha espaço livre no disco.",
                  foreground="#666").pack(anchor="w", padx=6)
        b = ttk.Frame(f)
        b.pack(fill="x", padx=4, pady=4)
        self.btn_rec = ttk.Button(b, text="Gravar  (Ctrl+Alt+G)", command=self.alternar)
        self.btn_rec.pack(side="left", padx=3)
        self.btn_marca = ttk.Button(b, text="Marcar  (Ctrl+Alt+M)", command=self.marcar, state="disabled")
        self.btn_marca.pack(side="left", padx=3)
        ttk.Button(b, text="Abrir pasta", command=self.abrir_pasta).pack(side="left", padx=3)

        ttk.Label(root, textvariable=self.v_status, font=("Consolas", 10)).pack(anchor="w", padx=10)
        if pk is None:
            ttk.Label(root, foreground="#a00", text="pynput não instalado: atalhos e registro de teclas desligados.").pack(anchor="w", padx=10)
        self.txt = tk.Text(root, height=12, font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=8, pady=6)

        root.protocol("WM_DELETE_WINDOW", self.fechar)
        root.after(100, self.poll)
        self._atalhos()

    # -- layout ------------------------------------------------------------------
    def _carregar_layout(self):
        try:
            with open(LAYOUT_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for k, v in d.get("regioes", {}).items():
                if k in self.v_reg:
                    for var, val in zip(self.v_reg[k], v):
                        var.set(str(val))
            for q in ("usuario", "bot"):
                for fn, lista in d.get("atalhos", {}).get(q, {}).items():
                    if fn in FUNCOES:
                        self.atalhos[q][fn] = list(lista)
            for fn, t in d.get("teclas", {}).items():          # formato antigo (uma tecla por função)
                if fn in FUNCOES and "atalhos" not in d:
                    self.atalhos["usuario"][fn] = [f"key:{t}"]
        except Exception:
            pass

    def regioes(self):
        return {k: tuple(int(float(v.get())) for v in vs) for k, vs in self.v_reg.items()}

    def _atualizar_atalhos(self):
        for q in ("usuario", "bot"):
            for fn in FUNCOES:
                self.v_atalho[q][fn].set(descrever_lista(self.atalhos[q][fn]))

    def gravar_atalho(self, fn, q):
        quem = "SEU" if q == "usuario" else "do BOT"
        s = capturar_atalho(self.root, f"Atalho {quem} para: {FUNCOES[fn]}")
        if s and s not in self.atalhos[q][fn]:
            self.atalhos[q][fn].append(s)
            self._atualizar_atalhos()
            self.log(f"Atalho adicionado ({q}, {FUNCOES[fn]}): {s}. Clique em 'Salvar layout' para guardar.")

    def limpar_atalho(self, fn, q):
        self.atalhos[q][fn] = []
        self._atualizar_atalhos()

    def log(self, s):
        self.txt.insert("end", s + "\n")
        self.txt.see("end")

    def selecionar(self, k):
        r = selecionar_retangulo(self.root, NOMES[k])
        if r:
            for var, val in zip(self.v_reg[k], r):
                var.set(str(val))
            if k == "coordenada":
                self.testar_coord()

    def testar_coord(self):
        import mss
        try:
            x, y, w, h = self.regioes()["coordenada"]
            with mss.mss() as sct:
                mon = monitor_principal(sct)
                t = time.perf_counter()
                arr = np.array(sct.grab({"left": mon["left"] + x, "top": mon["top"] + y, "width": w, "height": h}))
                c = ler_coord(arr)
                ms = (time.perf_counter() - t) * 1000
        except Exception as e:
            self.log(f"Erro: {e}")
            return
        self.log(f"Coordenada: {c}  ({ms:.1f} ms)" if c else
                 "Não consegui ler a coordenada. Confira o retângulo (o texto X, Y, Z inteiro, sem painel Cam por cima).")

    def previa(self):
        import mss
        with mss.mss() as sct:
            mon = monitor_principal(sct)
            shot = sct.grab(mon)
            img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX")
        d = ImageDraw.Draw(img)
        cores = ["#ff3030", "#30ff30", "#30a0ff", "#ffff30", "#ff30ff"]
        for (k, (x, y, w, h)), cor in zip(self.regioes().items(), cores):
            d.rectangle((x, y, x + w, y + h), outline=cor, width=3)
            d.text((x + 4, y + 4), k, fill=cor)
        esc = min(1.0, 1280 / img.width, 760 / img.height)
        img = img.resize((int(img.width * esc), int(img.height * esc)), Image.LANCZOS)
        top = tk.Toplevel(self.root)
        top.title("Prévia do layout")
        ph = ImageTk.PhotoImage(img)
        lab = tk.Label(top, image=ph)
        lab.image = ph
        lab.pack()

    def salvar_layout(self):
        try:
            reg = self.regioes()
        except ValueError:
            messagebox.showerror("Erro", "Há valores inválidos nos retângulos.")
            return
        with open(LAYOUT_FILE, "w", encoding="utf-8") as fh:
            json.dump({"tela": [1920, 1080], "regioes": {k: list(v) for k, v in reg.items()},
                       "atalhos": self.atalhos}, fh, ensure_ascii=False, indent=1)
        x, y, w, h = reg["coordenada"]
        try:
            cfg = {}
            if os.path.exists(CFG_GRAVADOR):
                with open(CFG_GRAVADOR, "r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
            cfg.update({"x": str(x), "y": str(y), "w": str(w), "h": str(h)})
            with open(CFG_GRAVADOR, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
        except Exception as e:
            self.log(f"Aviso: não consegui atualizar o config_gravador.json ({e})")
        self.log("Layout salvo em layout.json. A região da coordenada também foi atualizada para o gravador de lures e o player.")

    # -- atalhos e registro de teclas ---------------------------------------------------
    def _atalhos(self):
        if pk is None:
            return
        try:
            self.hk = pk.GlobalHotKeys({
                "<ctrl>+<alt>+g": lambda: self.q.put(("alternar",)),
                "<ctrl>+<alt>+m": lambda: self.q.put(("marcar",)),
            })
            self.hk.start()
        except Exception as e:
            self.log(f"Atalhos globais indisponíveis: {e}")

    def _evento_atalho(self, fn, atalho, acao, pos):
        if not self.grav:
            return
        if fn.startswith("mov_"):
            self.grav.evento("tecla", tecla=atalho, acao=acao)
        else:
            self.grav.evento("atalho", funcao=fn, atalho=atalho, acao=acao, mouse=list(pos))

    # -- gravação ----------------------------------------------------------------------------
    def alternar(self):
        self.parar() if self.grav else self.iniciar()

    def iniciar(self):
        try:
            fps, qual, mx = int(self.v_fps.get()), int(self.v_qual.get()), float(self.v_max.get())
            reg = self.regioes()
        except ValueError:
            messagebox.showerror("Erro", "Valor inválido nas opções.")
            return
        self.salvar_layout()
        self.pasta = os.path.join(PASTA, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
        self.grav = Gravacao(self.pasta, fps, qual, reg["coordenada"], mx,
                             {k: list(v) for k, v in reg.items()}, self.atalhos)
        if pk and self.v_logteclas.get():
            mapa = {t: "mov_" + t.split(":")[1] for t in TECLAS_MOVIMENTO}
            for fn, lista in self.atalhos["usuario"].items():
                for t in lista:
                    mapa[t] = fn
            try:
                self.vigia = Vigia(mapa, self._evento_atalho)
                self.vigia.start()
            except Exception as e:
                self.log(f"Registro de atalhos indisponível: {e}")
        self.grav.iniciar()
        self.btn_rec.config(text="Parar  (Ctrl+Alt+G)")
        self.btn_marca.config(state="normal")
        self.log(f"Gravando em {self.pasta}")

    def marcar(self):
        if self.grav:
            self.grav.evento("marca")
            self.log(f"Marca em {self.grav.t():.1f} s")

    def parar(self):
        g, self.grav = self.grav, None
        if not g:
            return
        if self.vigia:
            self.vigia.stop()
            self.vigia = None
        self.btn_rec.config(text="Gravar  (Ctrl+Alt+G)")
        self.btn_marca.config(state="disabled")
        self.v_status.set("Salvando os últimos quadros...")
        self.root.update_idletasks()
        meta = g.parar()
        self.v_status.set("Parado.")
        self.log(f"Fim: {meta['quadros_salvos']} quadros salvos de {meta['quadros_capturados']} "
                 f"({meta['quadros_descartados']} descartados), {meta['duracao_s']} s, "
                 f"{meta['fps_real']} quadros/s reais, {meta['tamanho_mb']} MB.")
        if meta["quadros_descartados"]:
            self.log("Houve quadros descartados: o disco ou o processador não acompanhou. Reduza os quadros/s ou a qualidade.")

    def abrir_pasta(self):
        p = self.pasta if self.pasta and os.path.isdir(self.pasta) else PASTA
        os.makedirs(p, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])

    def poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                self.alternar() if ev[0] == "alternar" else self.marcar()
        except queue.Empty:
            pass
        g = self.grav
        if g and g.t0 is not None:
            if g.stop.is_set():                 # chegou na duração máxima
                self.parar()
            else:
                c = g.ultima_coord
                self.v_status.set(f"GRAVANDO {g.t():5.1f} s | quadros {g.n} | descartados {g.descartados} | "
                                  f"fila {g.q.qsize()} | {g.bytes / 1e6:.0f} MB | coord {c[:2] if c else '?'}")
        self.root.after(100, self.poll)

    def fechar(self):
        if self.grav:
            self.parar()
        for o in (self.hk,):
            try:
                if o:
                    o.stop()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
