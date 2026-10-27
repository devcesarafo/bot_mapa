# -*- coding: utf-8 -*-
"""
Gravador de lures.

Você anda pela hunt normalmente. O programa lê a coordenada do jogo (X, Y, Z)
dezenas de vezes por segundo, guarda cada passo e, ao parar, converte o trajeto
em trechos retos (N, S, L, O e diagonais) com as teclas WASD correspondentes.
Não aperta nenhuma tecla: só LÊ a tela.

Marcação dos lures (a hunt é um laço):
    - A gravação começa no INÍCIO do primeiro lure.
    - Cada "Marcar parada" encerra um lure (o ponto onde o combate acontece).
    - O lure seguinte começa naquele ponto.
    - O que for andado depois da última parada é ignorado.

Atalhos globais (funcionam com o jogo em foco; precisam do pynput):
    Ctrl+Alt+G = iniciar / parar a gravação
    Ctrl+Alt+P = marcar parada

Dependências:
    pip install opencv-python numpy pillow mss pynput

Arquivos necessários na mesma pasta: leitor_coord.py, editor_mapa.py
Saída: gravacoes/AAAA-MM-DD_HH-MM-SS/lures.json  e  lures_preview.png
"""
import os
import sys
import json
import time
import queue
import threading
import subprocess
from datetime import datetime
from statistics import median

import numpy as np
import cv2
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from leitor_coord import ler_coord, REGIAO_PADRAO
from editor_mapa import ObstacleMap

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
PASTA_GRAVACOES = os.path.join(APP_DIR, "gravacoes")
CONFIG_FILE = os.path.join(APP_DIR, "config_gravador.json")
MAPA_OBST_PADRAO = os.path.join(APP_DIR, "mapa_obstaculos.json")
PASSOS_PARA_SAIR_DA_TELA = 6          # 5 tiles visíveis acima/abaixo + 1

# direção (dx, dy) -> (nome, teclas WASD). y cresce para baixo (sul).
DIRECOES = {
    (0, -1): ("N", ["W"]), (0, 1): ("S", ["S"]),
    (1, 0): ("L", ["D"]), (-1, 0): ("O", ["A"]),
    (1, -1): ("NE", ["W", "D"]), (-1, -1): ("NO", ["W", "A"]),
    (1, 1): ("SE", ["S", "D"]), (-1, 1): ("SO", ["S", "A"]),
}


# ----------------------------------------------------------------------------
# Processamento do trajeto (independe da interface)
# ----------------------------------------------------------------------------
def segmentar(pontos):
    """pontos: [(t, x, y, z), ...] sem repetição. -> lista de segmentos."""
    segs = []
    for (t0, x0, y0, z0), (t1, x1, y1, z1) in zip(pontos, pontos[1:]):
        dx, dy = x1 - x0, y1 - y0
        if z1 != z0 or abs(dx) > 1 or abs(dy) > 1:
            segs.append({"dir": "SALTO", "teclas": [], "passos": 1,
                         "de": [x0, y0, z0], "para": [x1, y1, z1]})
            continue
        nome, teclas = DIRECOES[(dx, dy)]
        if segs and segs[-1]["dir"] == nome:
            segs[-1]["passos"] += 1
            segs[-1]["para"] = [x1, y1, z1]
        else:
            segs.append({"dir": nome, "teclas": teclas, "passos": 1,
                         "de": [x0, y0, z0], "para": [x1, y1, z1]})
    return segs


def tempos_por_passo(pontos, pausa_max=1.5):
    """ms que cada tipo de passo ocupa (reto x diagonal).
    O intervalo até o PRÓXIMO passo é a duração do passo que acabou de ocorrer
    (o personagem só anda de novo quando o passo anterior termina)."""
    ret, dia = [], []
    for k in range(1, len(pontos) - 1):
        _, x0, y0, _ = pontos[k - 1]
        t1, x1, y1, _ = pontos[k]
        t2 = pontos[k + 1][0]
        dx, dy, dt = x1 - x0, y1 - y0, t2 - t1
        if (dx, dy) == (0, 0) or abs(dx) > 1 or abs(dy) > 1 or dt > pausa_max:
            continue
        (dia if dx and dy else ret).append(dt * 1000)

    def res(v):
        return {"amostras": len(v), "mediana_ms": round(median(v)) if v else None}
    return {"reto": res(ret), "diagonal": res(dia)}


def saida_vertical(obst, x, y, z):
    """Quantos tiles andáveis em linha reta para cima e para baixo, a partir de (x, y)."""
    if obst is None or not obst.anchor:
        return None
    col, row = obst.tile_of_game(x, y)
    res = {}
    for nome, dr in (("cima", -1), ("baixo", 1)):
        n = 0
        while n < 30 and obst.walkable(col, row + dr * (n + 1)):
            n += 1
        res[nome] = n
    res["ok"] = max(res["cima"], res["baixo"]) >= PASSOS_PARA_SAIR_DA_TELA
    return res


def montar_lures(pontos, marcas_t, obst=None):
    """Divide o trajeto em lures pelos instantes das marcas de parada.
    Cada marca corta na posição em que o personagem estava naquele instante.
    O que vier depois da última marca é ignorado; sem marcas, tudo é um lure."""
    if len(pontos) < 2:
        return []
    tempos = [p[0] for p in pontos]
    cortes = [0]
    for tm in sorted(marcas_t):
        i = max([k for k, t in enumerate(tempos) if t <= tm] or [0])
        if i > cortes[-1]:
            cortes.append(i)
    if not marcas_t and cortes[-1] != len(pontos) - 1:
        cortes.append(len(pontos) - 1)
    lures = []
    for n, (a, b) in enumerate(zip(cortes, cortes[1:]), start=1):
        trecho = pontos[a:b + 1]
        segs = segmentar(trecho)
        ini, fim = trecho[0], trecho[-1]
        lure = {
            "nome": f"lure {n}",
            "inicio": list(ini[1:]), "parada": list(fim[1:]),
            "duracao_s": round(fim[0] - ini[0], 2),
            "passos": sum(s["passos"] for s in segs if s["dir"] != "SALTO"),
            "segmentos": segs,
            "pontos": [[p[1], p[2]] for p in trecho],
        }
        sv = saida_vertical(obst, fim[1], fim[2], fim[3])
        if sv:
            lure["saida_vertical"] = sv
        lures.append(lure)
    return lures


def validar_no_mapa(lures, obst):
    """Lista passos em tiles que o mapa de obstáculos diz que NÃO são andáveis."""
    if obst is None or not obst.anchor:
        return None
    ruins = []
    for lu in lures:
        for x, y in lu["pontos"]:
            c, r = obst.tile_of_game(x, y)
            if not obst.walkable(c, r):
                ruins.append({"lure": lu["nome"], "x": x, "y": y, "tile": [c, r]})
    return ruins


def desenhar_preview(lures, obst, caminho_mapa, destino, escala=2):
    if obst is None or not obst.anchor or not os.path.exists(caminho_mapa):
        return False
    data = np.fromfile(caminho_mapa, dtype=np.uint8)
    im = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if im.ndim == 3 and im.shape[2] == 4:
        a = im[:, :, 3:4] / 255.0
        im = (im[:, :, :3] * a + 255 * (1 - a)).astype(np.uint8)
    elif im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    im = cv2.resize(im, None, fx=escala, fy=escala, interpolation=cv2.INTER_NEAREST)
    t = obst.tile
    cores = [(0, 0, 255), (255, 0, 255), (0, 200, 255), (255, 128, 0), (0, 255, 255), (255, 255, 0)]
    for i, lu in enumerate(lures):
        cor = cores[i % len(cores)]
        pts = []
        for x, y in lu["pontos"]:
            c, r = obst.tile_of_game(x, y)
            pts.append((int((obst.ox + (c + 0.5) * t) * escala), int((obst.oy + (r + 0.5) * t) * escala)))
        if len(pts) > 1:
            cv2.polylines(im, [np.array(pts, np.int32)], False, cor, 2, cv2.LINE_AA)
        cv2.circle(im, pts[0], 7, (0, 255, 0), -1)
        cv2.rectangle(im, (pts[-1][0] - 7, pts[-1][1] - 7), (pts[-1][0] + 7, pts[-1][1] + 7), (0, 0, 0), -1)
        cv2.putText(im, str(i + 1), (pts[-1][0] + 10, pts[-1][1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor, 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", im)
    if ok:
        buf.tofile(destino)
    return ok


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Gravador de Lures")
        root.geometry("760x720")
        self.q = queue.Queue()
        self.gravando = False
        self.stop_evt = threading.Event()
        self.pontos = []            # [(t, x, y, z)]
        self.marcas = []            # instantes (t) das paradas
        self.falhas = 0
        self.t0 = 0.0
        self.ultima = None
        self.pasta_atual = None
        self.photo = None
        self.obst = None
        self.hk = None

        self.v = {k: tk.StringVar(value=str(val)) for k, val in
                  zip("xywh", REGIAO_PADRAO)}
        self.v_obst = tk.StringVar(value=MAPA_OBST_PADRAO)
        self.v_status = tk.StringVar(value="Parado.")
        self.v_coord = tk.StringVar(value="—")
        self._carregar_config()

        pad = {"padx": 4, "pady": 3}
        f = ttk.LabelFrame(root, text="Onde está a coordenada na tela")
        f.pack(fill="x", padx=8, pady=6)
        for i, (lab, k) in enumerate((("X", "x"), ("Y", "y"), ("Larg.", "w"), ("Alt.", "h"))):
            ttk.Label(f, text=lab).grid(row=0, column=2 * i, **pad)
            ttk.Entry(f, textvariable=self.v[k], width=6).grid(row=0, column=2 * i + 1, **pad)
        ttk.Button(f, text="Selecionar região", command=self.selecionar_regiao).grid(row=0, column=8, **pad)
        ttk.Button(f, text="Testar leitura", command=self.testar_leitura).grid(row=0, column=9, **pad)

        f = ttk.LabelFrame(root, text="Mapa de obstáculos (opcional, mas recomendado)")
        f.pack(fill="x", padx=8, pady=3)
        ttk.Entry(f, textvariable=self.v_obst, width=70).pack(side="left", padx=4, pady=4, fill="x", expand=True)
        ttk.Button(f, text="...", width=3, command=self.escolher_obst).pack(side="left", padx=4)

        b = ttk.Frame(root)
        b.pack(fill="x", padx=8, pady=4)
        self.btn_rec = ttk.Button(b, text="Gravar  (Ctrl+Alt+G)", command=self.alternar)
        self.btn_rec.pack(side="left", padx=3)
        self.btn_marca = ttk.Button(b, text="Marcar parada  (Ctrl+Alt+P)", command=self.marcar, state="disabled")
        self.btn_marca.pack(side="left", padx=3)
        ttk.Button(b, text="Abrir pasta", command=self.abrir_pasta).pack(side="left", padx=3)

        ttk.Label(root, textvariable=self.v_coord, font=("Consolas", 16, "bold")).pack(pady=2)
        ttk.Label(root, textvariable=self.v_status).pack()
        if pk is None:
            ttk.Label(root, foreground="#a00",
                      text="pynput não instalado: atalhos globais desligados (pip install pynput).").pack()

        self.txt = tk.Text(root, height=12, font=("Consolas", 9))
        self.txt.pack(fill="x", padx=8, pady=4)
        self.prev = ttk.Label(root)
        self.prev.pack(fill="both", expand=True, padx=8, pady=4)

        root.protocol("WM_DELETE_WINDOW", self.fechar)
        root.after(60, self.poll)
        self._iniciar_atalhos()

    # -- config / util --------------------------------------------------------
    def _carregar_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                c = json.load(fh)
            for k in "xywh":
                if k in c:
                    self.v[k].set(str(c[k]))
            if "obst" in c:
                self.v_obst.set(c["obst"])
        except Exception:
            pass

    def _salvar_config(self):
        try:
            c = {k: self.v[k].get() for k in "xywh"}
            c["obst"] = self.v_obst.get()
            with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump(c, fh)
        except Exception:
            pass

    def log(self, s):
        self.txt.insert("end", s + "\n")
        self.txt.see("end")

    def regiao(self):
        return tuple(int(float(self.v[k].get())) for k in "xywh")

    def escolher_obst(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            self.v_obst.set(p)

    def carregar_obst(self):
        p = self.v_obst.get().strip()
        self.obst, self.mapa_img = None, None
        if p and os.path.exists(p):
            try:
                self.obst = ObstacleMap.load(p)
                with open(p, "r", encoding="utf-8") as fh:
                    mf = json.load(fh).get("map_file", "")
                for c in (mf, os.path.join(os.path.dirname(p), mf), os.path.join(os.path.dirname(p), os.path.basename(mf))):
                    if c and os.path.exists(c):
                        self.mapa_img = c
                        break
                if not self.obst.anchor:
                    self.log("AVISO: o mapa de obstáculos não tem âncora; validação e preview desligados.")
            except Exception as e:
                self.log(f"AVISO: não consegui abrir o mapa de obstáculos: {e}")

    # -- seleção de região ------------------------------------------------------
    def selecionar_regiao(self):
        import mss
        self.root.withdraw(); self.root.update(); time.sleep(0.3)
        with mss.mss() as sct:
            mon = sct.monitors[0]
            shot = sct.grab(mon)
            img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX")
        W, H = img.size
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{W}x{H}+{mon['left']}+{mon['top']}")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, width=W, height=H, highlightthickness=0, cursor="crosshair")
        cv.pack()
        photo = ImageTk.PhotoImage(img)
        cv.create_image(0, 0, image=photo, anchor="nw")
        cv.create_text(W // 2, 20, fill="red", font=("Arial", 16, "bold"),
                       text="Arraste um retângulo em volta do texto da coordenada (X, Y, Z). ESC cancela")
        st = {"x": 0, "y": 0, "r": None}

        def press(e):
            st["x"], st["y"] = e.x, e.y
            st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

        def drag(e):
            cv.coords(st["r"], st["x"], st["y"], e.x, e.y)

        def release(e):
            x1, x2 = sorted((st["x"], e.x)); y1, y2 = sorted((st["y"], e.y))
            ov.destroy()
            if x2 - x1 >= 20 and y2 - y1 >= 8:
                for k, v in zip("xywh", (mon["left"] + x1, mon["top"] + y1, x2 - x1, y2 - y1)):
                    self.v[k].set(str(v))

        cv.bind("<ButtonPress-1>", press); cv.bind("<B1-Motion>", drag)
        cv.bind("<ButtonRelease-1>", release)
        ov.bind("<Escape>", lambda e: ov.destroy())
        ov.focus_force(); ov.wait_window()
        self.root.deiconify()
        self.testar_leitura()

    def _grab(self, sct, x, y, w, h):
        return np.array(sct.grab({"left": x, "top": y, "width": w, "height": h}))

    def testar_leitura(self):
        import mss
        try:
            x, y, w, h = self.regiao()
            with mss.mss() as sct:
                t = time.perf_counter()
                c = ler_coord(self._grab(sct, x, y, w, h))
                ms = (time.perf_counter() - t) * 1000
        except Exception as e:
            messagebox.showerror("Erro", str(e)); return
        if c:
            self.v_coord.set(f"{c[0]}, {c[1]}, {c[2]}")
            self.log(f"Leitura OK: {c}  ({ms:.1f} ms incluindo a captura)")
        else:
            self.v_coord.set("não li")
            self.log("Não consegui ler a coordenada. Confira a região (o texto X, Y, Z precisa estar inteiro "
                     "e visível; painel Cam aberto esconde a coordenada).")

    # -- atalhos globais ----------------------------------------------------------
    def _iniciar_atalhos(self):
        if pk is None:
            return
        try:
            self.hk = pk.GlobalHotKeys({
                "<ctrl>+<alt>+g": lambda: self.q.put(("alternar",)),
                "<ctrl>+<alt>+p": lambda: self.q.put(("marcar", time.perf_counter())),
            })
            self.hk.start()
        except Exception as e:
            self.log(f"Atalhos globais indisponíveis: {e}")

    # -- gravação -------------------------------------------------------------------
    def alternar(self):
        if self.gravando:
            self.parar()
        else:
            self.iniciar()

    def iniciar(self):
        try:
            x, y, w, h = self.regiao()
            assert w > 10 and h > 7
        except Exception:
            messagebox.showerror("Erro", "Região inválida."); return
        self._salvar_config()
        self.carregar_obst()
        self.pontos, self.marcas, self.falhas, self.ultima = [], [], 0, None
        self.stop_evt.clear()
        self.gravando = True
        self.t0 = time.perf_counter()
        threading.Thread(target=self.laco, args=(x, y, w, h), daemon=True).start()
        self.btn_rec.config(text="Parar  (Ctrl+Alt+G)")
        self.btn_marca.config(state="normal")
        self.txt.delete("1.0", "end")
        self.log("Gravando... ande pela hunt. Marque cada parada.")
        self.v_status.set("GRAVANDO")

    def laco(self, x, y, w, h):
        import mss
        falhas_seguidas = 0
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                c = ler_coord(self._grab(sct, x, y, w, h))
                agora = time.perf_counter() - self.t0
                if c is None:
                    self.falhas += 1
                    falhas_seguidas += 1
                    if falhas_seguidas == 200:
                        self.q.put(("aviso", "Não estou conseguindo ler a coordenada há alguns segundos."))
                else:
                    falhas_seguidas = 0
                    if c != self.ultima:
                        self.pontos.append((agora,) + c)
                        self.ultima = c
                time.sleep(0.002)

    def marcar(self, quando=None):
        """quando = instante (perf_counter) em que o atalho foi apertado."""
        if not self.gravando:
            return
        t = (quando if quando is not None else time.perf_counter()) - self.t0
        self.marcas.append(t)
        self.log(f"Parada {len(self.marcas)} marcada em {self.ultima}")

    def parar(self):
        self.stop_evt.set()
        self.gravando = False
        self.btn_rec.config(text="Gravar  (Ctrl+Alt+G)")
        self.btn_marca.config(state="disabled")
        self.v_status.set("Parado.")
        time.sleep(0.05)
        self.finalizar()

    def finalizar(self):
        pontos = list(self.pontos)
        if len(pontos) < 2:
            self.log("Gravação vazia (menos de 2 posições). Nada salvo.")
            return
        lures = montar_lures(pontos, self.marcas, self.obst)
        ruins = validar_no_mapa(lures, self.obst)
        vel = tempos_por_passo(pontos)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.pasta_atual = os.path.join(PASTA_GRAVACOES, stamp)
        os.makedirs(self.pasta_atual, exist_ok=True)
        dados = {
            "versao": 1, "criado_em": stamp,
            "mapa_obstaculos": self.v_obst.get(),
            "velocidade_medida": vel,
            "lures": lures,
            "passos_fora_do_andavel": ruins,
            "bruto": [[round(p[0], 3), p[1], p[2], p[3]] for p in pontos],
        }
        with open(os.path.join(self.pasta_atual, "lures.json"), "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=1)

        self.log(f"\nSalvo em {self.pasta_atual}")
        self.log(f"{len(pontos)} posições lidas, {self.falhas} leituras falharam.")
        for lu in lures:
            seg = " ".join(f"{s['dir']}×{s['passos']}" if s["dir"] != "SALTO" else "SALTO" for s in lu["segmentos"])
            self.log(f"- {lu['nome']}: {lu['inicio'][:2]} -> {lu['parada'][:2]}, {lu['passos']} passos, {lu['duracao_s']} s")
            self.log(f"    {seg[:110]}{'...' if len(seg) > 110 else ''}")
            sv = lu.get("saida_vertical")
            if sv:
                self.log(f"    saída vertical livre: cima {sv['cima']}, baixo {sv['baixo']}"
                         + ("" if sv["ok"] else f"  << MENOS DE {PASSOS_PARA_SAIR_DA_TELA} TILES, fuga insegura"))
        self.log(f"Velocidade: reto {vel['reto']['mediana_ms']} ms/passo ({vel['reto']['amostras']} amostras), "
                 f"diagonal {vel['diagonal']['mediana_ms']} ms/passo ({vel['diagonal']['amostras']} amostras)")
        if ruins is not None:
            if ruins:
                self.log(f"ATENÇÃO: {len(ruins)} passos em tiles que o mapa marca como NÃO andáveis "
                         "(erro no mapa ou na âncora). Ex.: " + str(ruins[0]))
            else:
                self.log("Todos os passos caem em tiles andáveis do mapa.")
        destino = os.path.join(self.pasta_atual, "lures_preview.png")
        if desenhar_preview(lures, self.obst, getattr(self, "mapa_img", None) or "", destino):
            im = Image.open(destino)
            im.thumbnail((720, 420))
            self.photo = ImageTk.PhotoImage(im)
            self.prev.config(image=self.photo)

    def abrir_pasta(self):
        p = self.pasta_atual if self.pasta_atual and os.path.isdir(self.pasta_atual) else PASTA_GRAVACOES
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
                if ev[0] == "alternar":
                    self.alternar()
                elif ev[0] == "marcar":
                    self.marcar(ev[1] if len(ev) > 1 else None)
                elif ev[0] == "aviso":
                    self.log("AVISO: " + ev[1])
        except queue.Empty:
            pass
        if self.gravando:
            u = self.ultima
            if u:
                self.v_coord.set(f"{u[0]}, {u[1]}, {u[2]}")
            self.v_status.set(f"GRAVANDO  |  {len(self.pontos)} posições  |  {len(self.marcas)} paradas  |  "
                              f"{time.perf_counter() - self.t0:.0f} s")
        self.root.after(80, self.poll)

    def fechar(self):
        self.stop_evt.set()
        if self.hk:
            try:
                self.hk.stop()
            except Exception:
                pass
        self._salvar_config()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
