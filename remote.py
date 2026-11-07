#!/usr/bin/env python3
"""Télécommande virtuelle (tkinter) pour la TV Philips 55OLED854/12.

Connexion directe via le protocole Android TV Remote v2 (TLS, port 6466),
certificat d'appairage déjà stocké dans ~/.config/tvremote/.

Usage :
    tec                 (via l'alias bash)
    remote.py           (fenêtre graphique)
    remote.py --check   (test de connexion sans fenêtre, pour diagnostiquer)

Touche clavier pendant l'utilisation : flèches = navigation, Entrée = OK,
Échap = retour, +/- = volume, m = silence, espace = lecture/pause, 0-9 = chiffres.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from androidtvremote2 import AndroidTVRemote, ConnectionClosed, InvalidAuth

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
TV_HOST = os.environ.get("TV_HOST", "192.168.1.36")
TV_API_PORT = int(os.environ.get("TV_API_PORT", "6466"))
TV_PAIR_PORT = int(os.environ.get("TV_PAIR_PORT", "6467"))
CLIENT_NAME = os.environ.get("TV_CLIENT_NAME", "OpenCode")
CERT_DIR = os.path.expanduser(os.environ.get("TV_CERT_DIR", "~/.config/tvremote"))
CERTFILE = os.path.join(CERT_DIR, "phil_cert.pem")
KEYFILE = os.path.join(CERT_DIR, "phil_key.pem")

APP_NAMES = {
    "org.droidtv.playtv": "TNT / Télé",
    "com.google.android.youtube.tv": "YouTube",
    "com.netflix.ninja": "Netflix",
    "com.amazon.bueller.photos": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "fr.canalplus.mycanal": "myCANAL",
    "com.spotify.tv.android": "Spotify",
    "org.xbmc.kodi": "Kodi",
    "com.android.tv.settings": "Paramètres",
    "com.android.vending": "Play Store",
    "com.philips.photoram": "PhotoRAM",
    "com.android.browser": "Navigateur",
}

# Palette
BG = "#15151f"
BODY = "#1f1f2e"
BTN = "#2c2c40"
BTN_HI = "#3a3a55"
FG = "#e8e8f2"
ACCENT = "#4f9dff"
DANGER = "#e5484d"
GOOD = "#30a46c"
GOLD = "#f5a623"

KEY = {
    # navigation
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER",
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "menu": "KEYCODE_MENU",
    "info": "KEYCODE_INFO",
    "guide": "KEYCODE_GUIDE",
    "exit": "KEYCODE_MEDIA_CLOSE",
    # volume / son
    "vol+": "VOLUME_UP",
    "vol-": "VOLUME_DOWN",
    "mute": "VOLUME_MUTE",
    # puissance / sources
    "power": "KEYCODE_POWER",
    "source": "KEYCODE_TV_INPUT",
    "hdmi1": "KEYCODE_TV_INPUT_HDMI_1",
    "hdmi2": "KEYCODE_TV_INPUT_HDMI_2",
    "hdmi3": "KEYCODE_TV_INPUT_HDMI_3",
    # chaînes
    "ch+": "CHANNEL_UP",
    "ch-": "CHANNEL_DOWN",
    "last": "KEYCODE_LAST_CHANNEL",
    # lecture
    "playpause": "MEDIA_PLAY_PAUSE",
    "play": "MEDIA_PLAY",
    "pause": "MEDIA_PAUSE",
    "stop": "MEDIA_STOP",
    "prev": "MEDIA_PREVIOUS",
    "next": "MEDIA_NEXT",
    "rew": "MEDIA_REWIND",
    "ff": "MEDIA_FAST_FORWARD",
    "record": "MEDIA_RECORD",
    # couleurs
    "red": "KEYCODE_PROG_RED",
    "green": "KEYCODE_PROG_GREEN",
    "yellow": "KEYCODE_PROG_YELLOW",
    "blue": "KEYCODE_PROG_BLUE",
}


# --------------------------------------------------------------------------
# Pont asynchrone : le réseau vit dans un thread, l'interface dans le main
# --------------------------------------------------------------------------
class Bridge:
    """Envoie des touches à la TV depuis un thread asyncio dédié."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.remote: AndroidTVRemote | None = None
        self.events: queue.Queue = queue.Queue()
        self.connected = False
        self.status: dict = {"on": None, "volume": None, "app": ""}
        self._thread = threading.Thread(target=self._run, daemon=True, name="tv-bridge")
        self._thread.start()

    # ---- cycle de vie -----------------------------------------------------
    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001
            self.events.put(("log", f"Erreur du pont : {type(exc).__name__}: {exc}"))

    async def _main(self) -> None:
        # 1) connexion (avec plusieurs essais : la TV peut être éteinte)
        attempt = 0
        while True:
            attempt += 1
            try:
                remote = AndroidTVRemote(
                    CLIENT_NAME, CERTFILE, KEYFILE, TV_HOST,
                    api_port=TV_API_PORT, pair_port=TV_PAIR_PORT,
                )
                await remote.async_generate_cert_if_missing()
                await remote.async_connect()
                remote.keep_reconnecting()
                self.remote = remote
                break
            except InvalidAuth:
                self.events.put(("log", "Certificat non appairé : relancez 'tec --pair'."))
                return
            except Exception as exc:  # noqa: BLE001
                self.events.put(("log", f"Connexion {TV_HOST} échouée ({type(exc).__name__}), nouvel essai…"))
                await asyncio.sleep(2.5)
        self.connected = True
        self.events.put(("connected", True))
        self.events.put(("log", f"Connectée à la TV {TV_HOST}"))

        # 2) rafraîchissement régulier de l'état (volume, puissance, source)
        while True:
            try:
                r = self.remote
                if r is not None:
                    self.status = {
                        "on": r.is_on,
                        "volume": r.volume_info,
                        "app": r.current_app or "",
                    }
                    self.events.put(("status", dict(self.status)))
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1.2)

    # ---- envoi ------------------------------------------------------------
    async def _ensure(self):
        if self.remote is None:
            raise ConnectionClosed("pas de connexion")
        return self.remote

    async def _send(self, code: str) -> None:
        for essai in range(3):
            try:
                remote = await self._ensure()
                remote.send_key_command(code, "SHORT")
                return
            except ConnectionClosed:
                # la télécommande de la TV n'accepte qu'un client à la fois :
                # on laisse la reconnexion automatique faire son travail
                await asyncio.sleep(1.0)
                if essai == 2:
                    self.events.put(("log", "Connexion perdue à la TV — vérifiez qu'elle est allumée."))
            except ValueError as exc:
                self.events.put(("log", f"Touche inconnue {code} : {exc}"))
                return

    def send(self, code: str, label: str = "") -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send(code), self.loop)
        self.events.put(("sent", label or code))

    def set_volume(self, niveau: int) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._set_volume(niveau), self.loop)

    async def _set_volume(self, cible: int) -> None:
        vol = (self.status.get("volume") or {}).get("level") or 0
        delta = int(cible) - int(vol)
        code = "VOLUME_UP" if delta > 0 else "VOLUME_DOWN"
        for _ in range(abs(delta)):
            try:
                remote = await self._ensure()
                remote.send_key_command(code, "SHORT")
            except Exception:  # noqa: BLE001
                break
            await asyncio.sleep(0.12)
        self.events.put(("sent", f"volume → {cible}"))

    def stop(self) -> None:
        try:
            if self.remote is not None:
                self.remote.disconnect()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Interface graphique
# --------------------------------------------------------------------------
class RemoteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Télécommande TV — Philips 55OLED854")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.bridge = Bridge()
        self._repeat_job: str | None = None
        self._dragging = False
        self._buttons: list[tk.Button] = []

        self.font_btn = tkfont.Font(family="Sans", size=11, weight="bold")
        self.font_big = tkfont.Font(family="Sans", size=13, weight="bold")
        self.font_small = tkfont.Font(family="Sans", size=9)

        self._build()
        self._bind_keys()
        self.after(120, self._poll)

    # ---- construction -----------------------------------------------------
    def _btn(self, parent, text, code, *, kind="normal", repeat=False,
             col=None, row=None, colspan=1, width=4, font=None):
        bg = {
            "normal": BTN,
            "power": DANGER,
            "accent": ACCENT,
            "muted": "#5a5a70",
            "color": BTN,
        }[kind]
        b = tk.Button(
            parent,
            text=text,
            font=font or self.font_btn,
            bg=bg,
            fg=FG,
            activebackground=BTN_HI,
            activeforeground=FG,
            relief="flat",
            cursor="hand2",
            width=width,
            bd=0,
            highlightthickness=0,
            takefocus=0,
        )
        b._code = code  # type: ignore[attr-defined]
        b._repeat = repeat  # type: ignore[attr-defined]
        b._label = text  # type: ignore[attr-defined]
        b.bind("<ButtonPress-1>", lambda e, btn=b: self._on_press(btn))
        b.bind("<ButtonRelease-1>", lambda e: self._on_release())
        if col is not None:
            b.grid(row=row, column=col, columnspan=colspan, padx=3, pady=3, sticky="nsew")
        else:
            b.pack(side="left", padx=3, pady=3)
        self._buttons.append(b)
        return b

    def _section(self, parent, title):
        f = tk.Frame(parent, bg=BODY)
        f.pack(fill="x", padx=10, pady=4)
        if title:
            tk.Label(f, text=title, bg=BODY, fg="#8a8aa3", font=self.font_small).pack(anchor="w", padx=4, pady=(4, 0))
        inner = tk.Frame(f, bg=BODY)
        inner.pack(fill="x", padx=4, pady=4)
        return inner

    def _build(self) -> None:
        # --- barre d'état -------------------------------------------------
        top = tk.Frame(self, bg=BODY)
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.dot = tk.Label(top, text="●", bg=BODY, fg="#8a8aa3", font=self.font_big)
        self.dot.pack(side="left", padx=(8, 4))
        self.lbl_state = tk.Label(
            top, text="Connexion…", bg=BODY, fg=FG, font=self.font_btn, anchor="w"
        )
        self.lbl_state.pack(side="left", fill="x", expand=True, pady=8)
        self.lbl_vol = tk.Label(top, text="", bg=BODY, fg=GOLD, font=self.font_btn)
        self.lbl_vol.pack(side="right", padx=8)

        # --- puissance / son ---------------------------------------------
        row = self._section(self, "ALIMENTATION & SOURCES")
        self._btn(row, "⏻", KEY["power"], kind="power", width=4, font=self.font_big)
        self._btn(row, "MUTE", KEY["mute"], width=4, font=self.font_btn)
        self._btn(row, "SOURCE", KEY["source"], kind="accent", width=7)
        self._btn(row, "HDMI 1", KEY["hdmi1"], width=6)
        self._btn(row, "HDMI 2", KEY["hdmi2"], width=6)
        self._btn(row, "HDMI 3", KEY["hdmi3"], width=6)

        # --- volume (curseur + molettes) ---------------------------------
        row = self._section(self, "VOLUME")
        self._btn(row, "VOL +", KEY["vol+"], repeat=True, width=7, font=self.font_big)
        self._btn(row, "VOL −", KEY["vol-"], repeat=True, width=7, font=self.font_big)
        self.scale = tk.Scale(
            row, from_=0, to=60, orient="horizontal", showvalue=True,
            bg=BODY, fg=FG, troughcolor=BTN, sliderlength=18, sliderrelief="flat",
            highlightthickness=0, bd=0, font=self.font_small,
        )
        self.scale.pack(side="left", fill="x", expand=True, padx=8)
        self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "_dragging", True))
        self.scale.bind("<ButtonRelease-1>", self._scale_release)

        # --- navigation ----------------------------------------------------
        row = self._section(self, "NAVIGATION")
        nav = tk.Frame(row, bg=BODY)
        nav.pack(side="left", padx=6)
        grid = {
            (0, 1): ("▲", "up"),
            (1, 0): ("◀", "left"),
            (1, 1): ("OK", "ok"),
            (1, 2): ("▶", "right"),
            (2, 1): ("▼", "down"),
        }
        for r in range(3):
            for c in range(3):
                if (r, c) in grid:
                    txt, k = grid[(r, c)]
                    self._btn(nav, txt, KEY[k], kind="accent" if k == "ok" else "normal",
                              repeat=k != "ok", col=c, row=r, colspan=1,
                              width=5, font=self.font_big if k == "ok" else self.font_btn)
                else:
                    tk.Frame(nav, bg=BODY, width=46, height=44).grid(row=r, column=c, padx=3, pady=3)
        nav.grid_columnconfigure((0, 1, 2), weight=1)

        side = tk.Frame(row, bg=BODY)
        side.pack(side="left", padx=14)
        for txt, k in (("↩ RETOUR", "back"), ("ACCUEIL", "home"), ("MENU", "menu")):
            self._btn(side, txt, KEY[k], width=9, font=self.font_small)
        for txt, k in (("INFO", "info"), ("GUIDE", "guide"), ("DERNIÈRE", "last")):
            self._btn(side, txt, KEY[k], width=9, font=self.font_small)

        # --- chiffres -------------------------------------------------------
        row = self._section(self, "CHIFFRES / CHAÎNES")
        digits = tk.Frame(row, bg=BODY)
        digits.pack(anchor="w", padx=6)
        order = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["ch-", "0", "ch+"]]
        for r, ligne in enumerate(order):
            for c, item in enumerate(ligne):
                if item == "ch-":
                    self._btn(digits, "CH −", KEY["ch-"], repeat=True, col=c, row=r, width=6)
                elif item == "ch+":
                    self._btn(digits, "CH +", KEY["ch+"], repeat=True, col=c, row=r, width=6)
                else:
                    self._btn(digits, item, f"KEYCODE_{item}", col=c, row=r, width=6,
                              font=self.font_big)

        # --- lecture ---------------------------------------------------------
        row = self._section(self, "LECTURE")
        for txt, k in (("⏪", "rew"), ("⏮", "prev"), ("⏯", "playpause"),
                       ("⏭", "next"), ("⏩", "ff"), ("⏹", "stop"), ("⏺", "record")):
            self._btn(row, txt, KEY[k], width=3, font=self.font_big)

        # --- couleurs --------------------------------------------------------
        row = self._section(self, "TOUCHES COULEURS")
        for txt, k, col in (("ROUGE", "red", "#e5484d"), ("VERT", "green", GOOD),
                            ("JAUNE", "yellow", GOLD), ("BLEU", "blue", "#4f9dff")):
            b = self._btn(row, txt, KEY[k], kind="color", width=5, font=self.font_btn)
            b.configure(bg=col, fg="#101018", activebackground=col,
                        activeforeground="#101018")

        # --- journal ---------------------------------------------------------
        bottom = tk.Frame(self, bg=BODY)
        bottom.pack(fill="x", padx=10, pady=(6, 10))
        self.lbl_log = tk.Label(
            bottom, text="Prête. Clique un bouton ou utilise le clavier.",
            bg=BODY, fg="#8a8aa3", font=self.font_small, anchor="w", wraplength=420, justify="left",
        )
        self.lbl_log.pack(fill="x", padx=6, pady=6)

    def _bind_keys(self) -> None:
        m = {
            "<Up>": "up", "<Down>": "down", "<Left>": "left", "<Right>": "right",
            "<Return>": "ok", "<KP_Enter>": "ok", "<Escape>": "back",
            "<space>": "playpause", "<m>": "mute", "<i>": "info", "<g>": "guide",
            "<h>": "home", "<minus>": "vol-", "<plus>": "vol-", "<equal>": "vol+",
        }
        # <plus> et <equal> : on garde vol+ sur equal et vol- sur minus
        m["<plus>"] = "vol+"
        for seq, k in m.items():
            self.bind(seq, lambda e, kk=k: self.press(KEY[kk], k=kk))
        for d in "0123456789":
            self.bind(d, lambda e, dd=d: self.press(f"KEYCODE_{dd}"))

    # ---- interactions ------------------------------------------------------
    def press(self, code: str, *, k: str = "") -> None:
        self.bridge.send(code, label=self._label_for(code))
        if k in ("vol+", "vol-"):
            self._flash_label("VOLUME " + ("+" if k == "vol+" else "−"))

    def _label_for(self, code: str) -> str:
        for nom, valeur in KEY.items():
            if valeur == code:
                return nom
        return code.replace("KEYCODE_", "")

    def _on_press(self, btn: tk.Button) -> None:
        code = btn._code  # type: ignore[attr-defined]
        label = btn._label  # type: ignore[attr-defined]
        self.bridge.send(code, label=label)
        self._flash(btn)
        if btn._repeat:  # type: ignore[attr-defined]
            self._repeat_job = self.after(420, lambda: self._repeat(btn))

    def _repeat(self, btn: tk.Button) -> None:
        self.bridge.send(btn._code, label=btn._label)  # type: ignore[attr-defined]
        self._repeat_job = self.after(150, lambda: self._repeat(btn))

    def _on_release(self) -> None:
        if self._repeat_job is not None:
            self.after_cancel(self._repeat_job)
            self._repeat_job = None

    def _flash(self, btn: tk.Button) -> None:
        original = btn.cget("bg")
        btn.configure(bg=ACCENT, fg="#ffffff")
        self.after(140, lambda: btn.configure(bg=original))

    def _flash_label(self, text: str) -> None:
        self.lbl_log.configure(text=text, fg=FG)

    def _scale_release(self, _event) -> None:
        self._dragging = False
        self.bridge.set_volume(int(self.scale.get()))

    # ---- boucle d'événements ------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.bridge.events.get_nowait()
                if kind == "connected":
                    self.dot.configure(fg=GOOD)
                elif kind == "status":
                    self._render(payload)
                elif kind == "sent":
                    self.lbl_log.configure(text=f"▶ envoyé : {payload}", fg=FG)
                elif kind == "log":
                    self.lbl_log.configure(text=payload, fg=GOLD if "échou" not in payload else DANGER)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _render(self, status: dict) -> None:
        on = status.get("on")
        vol = status.get("volume") or {}
        app = APP_NAMES.get(status.get("app") or "", status.get("app") or "—")

        if on:
            self.dot.configure(fg=GOOD)
            self.lbl_state.configure(text=f"Allumée  ·  {app}")
        else:
            self.dot.configure(fg=DANGER)
            self.lbl_state.configure(text="Éteinte")

        level = vol.get("level")
        muted = vol.get("muted")
        if level is None:
            self.lbl_vol.configure(text="")
        else:
            self.lbl_vol.configure(
                text=f"🔇 coupé {level}/{vol.get('max', 60)}" if muted
                else f"SON {level}/{vol.get('max', 60)}"
            )
            if not self._dragging:
                self.scale.set(level)

    def on_close(self) -> None:
        self.bridge.stop()
        self.destroy()


# --------------------------------------------------------------------------
# Mode diagnostic (sans fenêtre) et appairage
# --------------------------------------------------------------------------
async def _check() -> int:
    remote = AndroidTVRemote(CLIENT_NAME, CERTFILE, KEYFILE, TV_HOST,
                             api_port=TV_API_PORT, pair_port=TV_PAIR_PORT)
    await remote.async_generate_cert_if_missing()
    print(f"Connexion à {TV_HOST}:{TV_API_PORT}…")
    try:
        await remote.async_connect()
    except InvalidAuth:
        print("ERREUR : certificat non appairé. Lancez 'tec --pair'.")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR : {type(exc).__name__}: {exc} (TV éteinte ?)")
        return 1
    for _ in range(20):
        if remote.volume_info is not None:
            break
        await asyncio.sleep(0.15)
    print("OK — état :", {
        "allumee": remote.is_on,
        "volume": remote.volume_info,
        "application": remote.current_app,
        "appareil": dict(remote.device_info) if remote.device_info else None,
    })
    remote.disconnect()
    return 0


async def _pair() -> int:
    remote = AndroidTVRemote(CLIENT_NAME, CERTFILE, KEYFILE, TV_HOST,
                             api_port=TV_API_PORT, pair_port=TV_PAIR_PORT)
    await remote.async_generate_cert_if_missing()
    print("Démarrage de l'appairage : regardez la TV, un code 6 caractères va s'afficher…")
    await remote.async_start_pairing()
    for _ in range(180):
        if os.path.exists(os.path.join(CERT_DIR, ".pair_code")):
            break
        await asyncio.sleep(1)
    code = input("Code affiché sur la TV (6 caractères) : ").strip().upper()
    try:
        await remote.async_finish_pairing(code)
    except InvalidAuth as exc:
        print(f"Code refusé : {exc}")
        return 3
    print("✅ Appairage réussi.")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return asyncio.run(_check())
    if "--pair" in sys.argv:
        return asyncio.run(_pair())
    app = RemoteApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
