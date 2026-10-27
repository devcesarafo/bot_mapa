# -*- coding: utf-8 -*-
"""
Atalhos: captura, nomes e vigia de teclas e botões do mouse.

Um atalho é um texto simples:
    key:e              tecla E
    key:space          barra de espaço
    key:f5             tecla F5
    ctrl+key:f5        Ctrl + F5
    mouse:x1           botão lateral 1 do mouse (normalmente "voltar")
    mouse:x2           botão lateral 2 do mouse (normalmente "avançar")
    mouse:middle       botão do meio (roda)
    mouse:right        botão direito

Uso nas outras ferramentas:
    capturar_atalho(root, "Mover o Ditto")   # abre a janelinha "aperte o atalho"
    descrever("mouse:x1")                    # -> "botão lateral 1 do mouse (voltar)"
    Vigia({"mouse:x1": "mover_ditto"}, callback).start()

Observação: se o software do seu mouse (Logitech, Razer, etc.) transforma o
botão lateral em uma tecla, a captura mostrará essa tecla, e isso é o que vale.
Rode como ADMINISTRADOR para capturar enquanto o jogo (administrador) está em foco.

Dependência:  pip install pynput
"""
import re
import queue
import tkinter as tk
from tkinter import ttk

try:
    from pynput import keyboard as pk, mouse as pm
except Exception:
    pk = pm = None

ALIAS_BOTAO = {"button8": "x1", "button9": "x2"}
DESC_BOTAO = {"left": "esquerdo", "right": "direito", "middle": "do meio (roda)",
              "x1": "lateral 1 (voltar)", "x2": "lateral 2 (avançar)"}
ORDEM_MODS = ("ctrl", "alt", "shift")
_PADRAO = re.compile(r"^((?:ctrl|alt|shift)\+)*(key|mouse):(.+)$")


# ----------------------------------------------------------------------------
# Nomes
# ----------------------------------------------------------------------------
def _mod_de(k):
    n = getattr(k, "name", "") or ""
    for m in ORDEM_MODS:
        if n.startswith(m):
            return m
    return None


def nome_tecla(k):
    """Nome da tecla sem modificadores; None se for só um modificador."""
    if _mod_de(k):
        return None
    if pk is not None and isinstance(k, pk.Key):
        return k.name
    vk = getattr(k, "vk", None)
    ch = getattr(k, "char", None)
    if vk is not None:
        if 65 <= vk <= 90:
            return chr(vk).lower()
        if 48 <= vk <= 57:
            return chr(vk)
        if 96 <= vk <= 105:
            return f"num{vk - 96}"
    if ch and ch.isprintable():
        return ch.lower()
    return f"vk{vk}" if vk is not None else None


def nome_botao(b):
    n = getattr(b, "name", None) or str(b).split(".")[-1]
    return ALIAS_BOTAO.get(n, n)


def montar(mods, tipo, nome):
    pre = "+".join(m for m in ORDEM_MODS if m in mods)
    base = f"{tipo}:{nome}"
    return f"{pre}+{base}" if pre else base


def valido(s):
    return bool(_PADRAO.match(s or ""))


def descrever(s):
    """Texto amigável para mostrar ao usuário."""
    m = _PADRAO.match(s or "")
    if not m:
        return s or ""
    tipo, nome = m.group(2), m.group(3)
    pre = s[: len(s) - len(tipo) - 1 - len(nome)]          # "ctrl+shift+" ou ""
    mods = [x.capitalize() for x in pre.split("+") if x]
    ini = (" + ".join(mods) + " + ") if mods else ""
    if tipo == "mouse":
        return f"{ini}botão {DESC_BOTAO.get(nome, nome)} do mouse"
    return f"{ini}tecla {nome.upper() if len(nome) == 1 else nome}"


def descrever_lista(lista):
    return ",  ".join(descrever(s) for s in lista) if lista else "(nenhum)"


# ----------------------------------------------------------------------------
# Escuta global de teclado e mouse
# ----------------------------------------------------------------------------
class Escuta:
    """Chama cb(atalho, pressionado, (x, y)) a cada tecla ou botão do mouse."""

    def __init__(self, cb, ignorar_esquerdo=False):
        self.cb, self.ignorar_esquerdo = cb, ignorar_esquerdo
        self.mods = set()
        self.down = {}
        self.kl = self.ml = self.mouse = None

    def start(self):
        if pk is None:
            return False
        self.mouse = pm.Controller()
        self.kl = pk.Listener(on_press=self._kp, on_release=self._kr)
        self.ml = pm.Listener(on_click=self._mc)
        self.kl.start()
        self.ml.start()
        return True

    def stop(self):
        for o in (self.kl, self.ml):
            try:
                if o:
                    o.stop()
            except Exception:
                pass

    def _pos(self):
        try:
            return tuple(self.mouse.position)
        except Exception:
            return (0, 0)

    def _kp(self, k):
        m = _mod_de(k)
        if m:
            self.mods.add(m)
            return
        n = nome_tecla(k)
        if n is None or n in self.down:          # ignora repetição automática
            return
        s = montar(self.mods, "key", n)
        self.down[n] = s
        self.cb(s, True, self._pos())

    def _kr(self, k):
        m = _mod_de(k)
        if m:
            self.mods.discard(m)
            return
        s = self.down.pop(nome_tecla(k), None)
        if s:
            self.cb(s, False, self._pos())

    def _mc(self, x, y, button, pressed):
        n = nome_botao(button)
        if self.ignorar_esquerdo and n == "left":
            return
        if pressed:
            s = montar(self.mods, "mouse", n)
            self.down["m:" + n] = s
            self.cb(s, True, (x, y))
        else:
            s = self.down.pop("m:" + n, None)
            if s:
                self.cb(s, False, (x, y))


class Vigia:
    """Observa os atalhos de interesse. mapa: {atalho: nome_da_funcao}.
    callback(funcao, atalho, 'down'|'up', (x, y)) é chamado de outra thread."""

    def __init__(self, mapa, callback):
        self.mapa, self.callback = dict(mapa), callback
        self.escuta = Escuta(self._cb)

    def _cb(self, s, pressionado, pos):
        f = self.mapa.get(s)
        if f:
            self.callback(f, s, "down" if pressionado else "up", pos)

    def start(self):
        return self.escuta.start()

    def stop(self):
        self.escuta.stop()


# ----------------------------------------------------------------------------
# Janelinha "grave este atalho"
# ----------------------------------------------------------------------------
class DialogoCaptura:
    def __init__(self, root, titulo):
        self.root, self.res, self.cand = root, None, None
        self.q = queue.Queue()
        self.esc = None
        top = self.top = tk.Toplevel(root)
        top.title("Gravar atalho")
        top.transient(root)
        top.geometry("460x230")
        ttk.Label(top, text=titulo, font=("Arial", 12, "bold")).pack(pady=(14, 4))
        self.v_info = tk.StringVar(value="Aperte AGORA o atalho (tecla ou botão do mouse)...")
        ttk.Label(top, textvariable=self.v_info, wraplength=420, justify="center").pack(pady=6)
        self.v_cand = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.v_cand, font=("Consolas", 12, "bold"), foreground="#064",
                  wraplength=420, justify="center").pack(pady=6)
        ttk.Label(top, text="(o clique esquerdo é ignorado, para você poder usar os botões da janela)",
                  foreground="#666").pack()
        b = ttk.Frame(top)
        b.pack(pady=10)
        self.btn_ok = ttk.Button(b, text="Confirmar", command=self.confirmar, state="disabled")
        self.btn_ok.pack(side="left", padx=4)
        ttk.Button(b, text="Tentar de novo", command=self.de_novo).pack(side="left", padx=4)
        ttk.Button(b, text="Cancelar", command=self.fechar).pack(side="left", padx=4)
        top.protocol("WM_DELETE_WINDOW", self.fechar)
        top.after(400, self.iniciar_escuta)      # evita capturar o clique que abriu a janela
        top.after(50, self.poll)

    def iniciar_escuta(self):
        self.parar_escuta()
        self.esc = Escuta(lambda s, p, pos: self.q.put(s) if p else None, ignorar_esquerdo=True)
        if not self.esc.start():
            self.v_info.set("pynput não está instalado: não consigo capturar. Digite o atalho na mão "
                            "(ex.: key:e ou mouse:x1).")

    def parar_escuta(self):
        if self.esc:
            self.esc.stop()
            self.esc = None

    def injetar(self, s):                        # usado em testes
        self.q.put(s)

    def poll(self):
        try:
            s = self.q.get_nowait()
            if self.cand is None:
                self.cand = s
                self.parar_escuta()
                self.v_info.set("Capturado. Está certo?")
                self.v_cand.set(f"{descrever(s)}\n[{s}]")
                self.btn_ok.config(state="normal")
        except queue.Empty:
            pass
        try:
            self.top.after(50, self.poll)
        except tk.TclError:
            pass

    def de_novo(self):
        self.cand = None
        self.v_cand.set("")
        self.v_info.set("Aperte AGORA o atalho (tecla ou botão do mouse)...")
        self.btn_ok.config(state="disabled")
        self.top.after(200, self.iniciar_escuta)

    def confirmar(self):
        self.res = self.cand
        self.fechar()

    def fechar(self):
        self.parar_escuta()
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def capturar_atalho(root, titulo):
    """Abre a janelinha e devolve o atalho (texto) ou None se cancelou."""
    d = DialogoCaptura(root, titulo)
    root.wait_window(d.top)
    return d.res
