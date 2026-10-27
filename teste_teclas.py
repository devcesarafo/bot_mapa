# -*- coding: utf-8 -*-
"""
Diagnóstico de envio de teclas para o jogo (Windows).

Descobre QUAL método de envio o seu jogo aceita, usando a coordenada do jogo
para confirmar se o personagem andou de verdade.

Como usar:
  1. Coloque o personagem num lugar seguro com 2 tiles livres para cima e para baixo.
  2. Rode:  python teste_teclas.py
  3. Nos 5 segundos de contagem, clique na janela do jogo.
  4. Para cada método ele aperta W (sobe 1 tile), depois S (volta) e diz se moveu.

Também mostra se este programa e o jogo estão como administrador: se o jogo
roda como administrador e o Python não, o Windows BLOQUEIA as teclas enviadas.
(Solução: abrir o terminal/Prompt como administrador e rodar de lá.)

Dependências:  pip install numpy mss pillow pynput pydirectinput
Região da coordenada: lida do config_gravador.json (a mesma do gravador).
"""
import os
import sys
import json
import time

import numpy as np

from leitor_coord import ler_coord, REGIAO_PADRAO

if sys.platform != "win32":
    print("Este teste só faz sentido no Windows (onde o jogo roda).")
    sys.exit(1)

import ctypes
from ctypes import wintypes

APP_DIR = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

SCAN = {"w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20}
VK = {"w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44}


# ---------------------------------------------------------------- leitura da coordenada
def regiao():
    try:
        with open(os.path.join(APP_DIR, "config_gravador.json"), "r", encoding="utf-8") as fh:
            c = json.load(fh)
        return tuple(int(float(c[k])) for k in "xywh")
    except Exception:
        return tuple(REGIAO_PADRAO)


class Leitor:
    def __init__(self):
        import mss
        self.sct = mss.mss()
        x, y, w, h = regiao()
        self.mon = {"left": x, "top": y, "width": w, "height": h}

    def pos(self):
        c = ler_coord(np.array(self.sct.grab(self.mon)))
        return c[:2] if c else None


# ---------------------------------------------------------------- SendInput
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _send(vk, scan, flags):
    i = INPUT(type=1, u=_UNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)))
    return user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def sendinput_scan(k, up):
    return _send(0, SCAN[k], 0x0008 | (0x0002 if up else 0))          # KEYEVENTF_SCANCODE


def sendinput_vk(k, up):
    return _send(VK[k], SCAN[k], 0x0002 if up else 0)


# ---------------------------------------------------------------- janela do jogo
def achar_janela(trecho="pokealliance"):
    achadas = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if trecho in buf.value.lower():
                    achadas.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return achadas[0] if achadas else (None, None)


def postmessage_factory(hwnd):
    def f(k, up):
        scan = SCAN[k]
        if up:
            return user32.PostMessageW(hwnd, 0x0101, VK[k], 1 | (scan << 16) | (1 << 30) | (1 << 31))
        return user32.PostMessageW(hwnd, 0x0100, VK[k], 1 | (scan << 16))
    return f


# ---------------------------------------------------------------- elevação (administrador)
def sou_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def processo_elevado(hwnd):
    """True / False / None (None = não consegui abrir: provável que esteja elevado)."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    tok = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):   # TOKEN_QUERY
        kernel32.CloseHandle(h)
        return None

    class ELEV(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    e, ret = ELEV(), wintypes.DWORD()
    ok = advapi32.GetTokenInformation(tok, 20, ctypes.byref(e), ctypes.sizeof(e), ctypes.byref(ret))
    kernel32.CloseHandle(tok)
    kernel32.CloseHandle(h)
    return bool(e.TokenIsElevated) if ok else None


# ---------------------------------------------------------------- teste
def tentar(leitor, nome, acao, tecla, esperado_mudar=True, max_s=1.2):
    """acao(k, up). Segura a tecla até a coordenada mudar (ou max_s). Retorna True se mudou."""
    antes = leitor.pos()
    if antes is None:
        print(f"   [{nome}] não consegui ler a coordenada. Confira a região no gravador.")
        return None
    try:
        acao(tecla, False)
        t0 = time.perf_counter()
        depois = antes
        while time.perf_counter() - t0 < max_s:
            depois = leitor.pos() or depois
            if depois != antes:
                break
            time.sleep(0.01)
    finally:
        acao(tecla, True)
    time.sleep(0.8)
    return depois != antes


def main():
    print("== Diagnóstico de teclas ==")
    print(f"Este programa roda como administrador? {sou_admin()}")
    hwnd, titulo = achar_janela()
    if hwnd:
        el = processo_elevado(hwnd)
        print(f"Janela do jogo encontrada: {titulo!r}")
        if el is None:
            print("  O jogo NÃO pôde ser inspecionado: quase certamente roda como ADMINISTRADOR.")
        else:
            print(f"  O jogo roda como administrador? {el}")
        if (el or el is None) and not sou_admin():
            print("  >>> PROVÁVEL CAUSA: o jogo é administrador e este programa não é.")
            print("      O Windows bloqueia teclas enviadas de um programa comum para um elevado.")
            print("      Feche tudo, abra o Prompt de Comando/PowerShell como ADMINISTRADOR e rode de lá.")
    else:
        print("Não achei a janela com 'PokeAlliance' no título (o método PostMessage será pulado).")

    leitor = Leitor()
    p = leitor.pos()
    print(f"Coordenada lida agora: {p}")
    if p is None:
        print("Não consegui ler a coordenada. Ajuste a região no gravador_lures.py primeiro.")
        return
    print("\nO personagem vai andar 1 tile para cima e voltar, uma vez por método.")
    print("Clique na janela do jogo. Começando em 5 s...")
    time.sleep(5)

    metodos = []
    try:
        from pynput.keyboard import Controller
        c = Controller()
        metodos.append(("pynput", lambda k, up: c.release(k) if up else c.press(k)))
    except Exception as e:
        print(f"(pynput indisponível: {e})")
    try:
        import pydirectinput
        pydirectinput.PAUSE = 0.0
        metodos.append(("pydirectinput", lambda k, up: pydirectinput.keyUp(k) if up else pydirectinput.keyDown(k)))
    except Exception as e:
        print(f"(pydirectinput indisponível: {e})")
    metodos.append(("SendInput scancode", sendinput_scan))
    metodos.append(("SendInput VK", sendinput_vk))
    if hwnd:
        metodos.append(("PostMessage na janela", postmessage_factory(hwnd)))

    res = {}
    for nome, acao in metodos:
        print(f"\n-> {nome}: apertando W...")
        r = tentar(leitor, nome, acao, "w")
        res[nome] = r
        print(f"   {'MOVEU' if r else 'não moveu'}")
        if r:
            tentar(leitor, nome, acao, "s")         # volta
        time.sleep(0.5)

    print("\n== Resumo ==")
    for nome, r in res.items():
        print(f"  {nome:24s} {'FUNCIONA' if r else ('?' if r is None else 'não funciona')}")
    if not any(res.values()):
        print("\nNenhum método moveu o personagem.")
        print(" 1) Rode como administrador (veja acima) e repita.")
        print(" 2) Se continuar assim, o jogo provavelmente ignora teclas 'injetadas' por software.")
        print("    Nesse caso as saídas são mais pesadas (driver de teclado virtual ou um")
        print("    microcontrolador USB que age como teclado). Me mande este resultado.")
    else:
        bons = [n for n, r in res.items() if r]
        print(f"\nUse: {bons[0]}.  Me diga qual foi, para eu deixar como padrão no player.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
