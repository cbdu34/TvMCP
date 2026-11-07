#!/usr/bin/env python3
"""Serveur MCP local : télécommande de la TV Philips + état de la Freebox.

Deux familles d'outils :

* ``tv_*``    : télécommande complète de la TV Philips 55OLED854/12 (192.168.1.36)
                via le protocole Android TV Remote v2 (TLS, ports 6466/6467).
                L'appairage est déjà fait, le certificat est dans
                ``~/.config/tvremote/`` (phil_cert.pem / phil_key.pem).
* ``freebox_*`` : lecture seule de l'API locale de la Freebox Server
                (192.168.1.254, auth X-Fbx-App-Auth via ~/.config/freebox/).

Toutes les réponses sont des chaînes JSON (format UTF-8, sans échappement).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
from androidtvremote2.remotemessage_pb2 import RemoteKeyCode as _RemoteKeyCode
from mcp.server.mcpserver import MCPServer

_KEYCODES: frozenset[str] = frozenset(_RemoteKeyCode.keys())

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

FBX_BASE = os.environ.get("FBX_BASE", "http://192.168.1.254/api/v4")
FBX_APP = os.path.expanduser("~/.config/freebox/app.json")
FBX_SESSION = os.path.expanduser("~/.config/freebox/session.json")

mcp = MCPServer(
    name="tv",
    instructions=(
        "Télécommande de la TV Philips (Philips 55OLED854/12) et lecture d'état Freebox. "
        "Outils TV : tv_status (état), tv_key/tv_keys (touches brutes), tv_power, "
        "tv_volume, tv_nav (flèches/OK/retour), tv_channel, tv_input (sources HDMI), "
        "tv_media (lecture), tv_text (saisie), tv_open (app/URL), tv_launch (applications), "
        "tv_pair (réappairage si la TV a été réinitialisée). "
        "Outils Freebox (lecture seule) : freebox_system, freebox_player, "
        "freebox_tv_channels, freebox_tv_status, freebox_devices, freebox_wifi_status. "
        "Les touches sont envoyées en français traduit automatiquement : dis « monte le son », "
        "« éteins la télé », « passe en HDMI 2 », « met les news »."
    ),
)

# --------------------------------------------------------------------------
# Télévision : connexion paresseuse + reconnexion
# --------------------------------------------------------------------------
_tv: AndroidTVRemote | None = None
_tv_lock = asyncio.Lock()
_tv_pairing: AndroidTVRemote | None = None


def _tv_reset() -> None:
    global _tv
    if _tv is not None:
        try:
            _tv.disconnect()
        except Exception:  # noqa: BLE001 - on ne veut jamais planter au reset
            pass
        _tv = None


async def _tv_get() -> AndroidTVRemote:
    """Renvoie une connexion TV établie (créée au premier usage)."""
    global _tv
    async with _tv_lock:
        if _tv is not None:
            return _tv
        remote = AndroidTVRemote(
            CLIENT_NAME, CERTFILE, KEYFILE, TV_HOST,
            api_port=TV_API_PORT, pair_port=TV_PAIR_PORT,
        )
        await remote.async_generate_cert_if_missing()
        try:
            await remote.async_connect()
        except InvalidAuth as exc:
            raise RuntimeError(
                "Certificat non appairé avec la TV (InvalidAuth). "
                "Lancez tv_pair() puis validez le code affiché à l'écran."
            ) from exc
        except (CannotConnect, ConnectionClosed, OSError) as exc:
            raise RuntimeError(
                f"Impossible de joindre la TV {TV_HOST}:{TV_API_PORT} "
                f"({type(exc).__name__}: {exc}). La TV est-elle allumée ?"
            ) from exc
        remote.keep_reconnecting()
        # La TV pousse son état (volume/app) dès la connexion : on attend
        # brièvement pour que le premier tv_status renvoie des valeurs réelles.
        for _ in range(20):
            if remote.volume_info is not None and remote.current_app:
                break
            await asyncio.sleep(0.15)
        _tv = remote
        return _tv


async def _tv_action(fn, *args, **kwargs):
    """Exécute une action sur la TV avec une reconnexion d'essai si le lien tombe."""
    remote = await _tv_get()
    try:
        result = fn(remote, *args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except ConnectionClosed:
        _tv_reset()
        remote = await _tv_get()
        result = fn(remote, *args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return _json({"ok": False, "erreur": message})


def _ok(**kwargs: Any) -> str:
    return _json({"ok": True, **kwargs})

def _status_dict(remote: AndroidTVRemote) -> dict[str, Any]:
    vol = remote.volume_info
    return {
        "allumee": bool(remote.is_on),
        "volume": None if vol is None else dict(vol),
        "application": remote.current_app,
        "appareil": dict(remote.device_info) if remote.device_info else None,
        "hote": f"{TV_HOST}:{TV_API_PORT}",
    }


# --------------------------------------------------------------------------
# Outils TV — état
# --------------------------------------------------------------------------
@mcp.tool()
async def tv_status() -> str:
    """État actuel de la TV (allumée, volume, appli/source en cours)."""
    try:
        remote = await _tv_get()
        return _ok(**_status_dict(remote))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_keys() -> str:
    """Liste les touches envoyables par tv_key/tv_keys (codes Android TV)."""
    from androidtvremote2.remotemessage_pb2 import RemoteKeyCode

    names = sorted(RemoteKeyCode.keys())
    return _ok(nombre=len(names), touches=names)


# --------------------------------------------------------------------------
# Outils TV — touches brutes
# --------------------------------------------------------------------------
@mcp.tool()
async def tv_key(key: str, direction: str = "SHORT") -> str:
    """Envoie UNE touche à la TV.

    :param key: code Android TV (ex. KEYCODE_VOLUME_UP, POWER, DPAD_UP, 1, MUTE…)
                ou texte préfixé ``text:`` (ex. text:bonjour).
    :param direction: SHORT (défaut), START_LONG ou END_LONG (appui long).
    """
    key = key.strip()
    if key.upper() not in _KEYCODES and ("KEYCODE_" + key.upper()) in _KEYCODES:
        key = "KEYCODE_" + key.upper()
    try:
        await _tv_action(lambda r: r.send_key_command(key, direction.upper()))
        return _ok(touche=key, direction=direction.upper(), envoye=True)
    except ValueError as exc:
        return _err(f"Toucher inconnue « {key} » : {exc}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_keys_send(touches: list[str], interval: float = 0.25) -> str:
    """Envoie une SÉRIE de touches dans l'ordre (ex. zapping numérique « 5 », « 8 »).

    :param touches: liste de codes (ex. ["KEYCODE_1", "KEYCODE_4"]).
    :param interval: pause entre chaque touche en secondes (0.15 min, 2 max).
    """
    interval = max(0.15, min(float(interval), 2.0))
    envoyees: list[str] = []
    try:
        for k in touches:
            k = k.strip()
            await _tv_action(lambda r, k=k: r.send_key_command(k, "SHORT"))
            envoyees.append(k)
            await asyncio.sleep(interval)
        return _ok(envoye=envoyees, nombre=len(envoyees))
    except Exception as exc:  # noqa: BLE001
        return _err(f"Échec après {len(envoyees)} touche(s) {envoyees} : {exc}")


# --------------------------------------------------------------------------
# Outils TV — gestes courants
# --------------------------------------------------------------------------
@mcp.tool()
async def tv_power(action: str = "toggle") -> str:
    """Allumage/éteinte de la TV : action = on | off | toggle."""
    a = action.strip().lower()
    table = {"on": "KEYCODE_TV_POWER", "off": "KEYCODE_TV_POWER", "toggle": "KEYCODE_POWER"}
    if a not in table:
        return _err("action doit être on, off ou toggle")
    try:
        await _tv_action(lambda r: r.send_key_command(table[a], "SHORT"))
        await asyncio.sleep(1.0)
        remote = await _tv_get()
        return _ok(action=a, etat=_status_dict(remote))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_volume(action: str = "up", pas: int = 1) -> str:
    """Volume : action = up | down | set | mute | unmute.

    :param action: up/down (monte/descend), set (niveau précis), mute/unmute.
    :param pas: nombre de crans (up/down) ou niveau cible 0-60 (set).
    """
    a = action.strip().lower()
    try:
        if a in ("up", "monter", "+"):
            for _ in range(max(1, min(int(pas), 30))):
                await _tv_action(lambda r: r.send_key_command("VOLUME_UP", "SHORT"))
                await asyncio.sleep(0.12)
        elif a in ("down", "descendre", "-"):
            for _ in range(max(1, min(int(pas), 30))):
                await _tv_action(lambda r: r.send_key_command("VOLUME_DOWN", "SHORT"))
                await asyncio.sleep(0.12)
        elif a == "set":
            cible = max(0, min(int(pas), 60))
            remote = await _tv_get()
            vol = remote.volume_info or {}
            cur = int(vol.get("level", 0))
            while cur < cible:
                await _tv_action(lambda r: r.send_key_command("VOLUME_UP", "SHORT"))
                cur += 1
                await asyncio.sleep(0.1)
            while cur > cible:
                await _tv_action(lambda r: r.send_key_command("VOLUME_DOWN", "SHORT"))
                cur -= 1
                await asyncio.sleep(0.1)
        elif a == "mute":
            await _tv_action(lambda r: r.send_key_command("VOLUME_MUTE", "SHORT"))
        elif a == "unmute":
            remote = await _tv_get()
            if (remote.volume_info or {}).get("muted"):
                await _tv_action(lambda r: r.send_key_command("VOLUME_MUTE", "SHORT"))
            else:
                return _ok(message="déjà non coupé", volume=dict(remote.volume_info or {}))
        else:
            return _err("action doit être up, down, set, mute ou unmute")
        await asyncio.sleep(0.4)
        remote = await _tv_get()
        return _ok(action=a, volume=dict(remote.volume_info or {}))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


NAV_KEYS = {
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER",
    "enter": "DPAD_CENTER",
    "back": "BACK",
    "retour": "BACK",
    "home": "HOME",
    "menu": "MENU",
    "info": "INFO",
    "guide": "GUIDE",
    "fermer": "KEYCODE_MEDIA_CLOSE",
    "captions": "KEYCODE_CAPTIONS",
    "pageup": "PAGE_UP",
    "pagedown": "PAGE_DOWN",
    "rouge": "PROG_RED",
    "vert": "PROG_GREEN",
    "jaune": "PROG_YELLOW",
    "bleu": "PROG_BLUE",
}


@mcp.tool()
async def tv_nav(direction: str, repetition: int = 1) -> str:
    """Navigation : direction parmi up, down, left, right, ok, back, home, menu,
    info, guide, fermer, captions, pageup, pagedown, rouge, vert, jaune, bleu."""
    d = direction.strip().lower()
    key = NAV_KEYS.get(d)
    if key is None:
        return _err(f"direction inconnue « {d} ». Valeurs : {', '.join(sorted(NAV_KEYS))}")
    n = max(1, min(int(repetition), 30))
    try:
        for _ in range(n):
            await _tv_action(lambda r, k=key: r.send_key_command(k, "SHORT"))
            await asyncio.sleep(0.15)
        return _ok(direction=d, touche=key, repetition=n, envoye=True)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_channel(action: str = "up") -> str:
    """Chaînes : action = up | down | last | ou numéro (ex. "14", "5")."""
    a = str(action).strip().lower()
    mapping = {
        "up": ["CHANNEL_UP"],
        "down": ["CHANNEL_DOWN"],
        "last": ["LAST_CHANNEL"],
    }
    if a in mapping:
        keys = mapping[a]
    elif a.isdigit() and len(a) <= 4:
        keys = [f"KEYCODE_{c}" for c in a]
    else:
        return _err("action = up, down, last ou un numéro de chaîne (ex. 14)")
    envoyees = []
    try:
        for k in keys:
            await _tv_action(lambda r, k=k: r.send_key_command(k, "SHORT"))
            envoyees.append(k)
            await asyncio.sleep(0.3)
        await asyncio.sleep(1.5)
        remote = await _tv_get()
        return _ok(envoye=envoyees, application=remote.current_app, volume=dict(remote.volume_info or {}))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


INPUT_KEYS = {
    "tv": "KEYCODE_TV_INPUT",
    "auto": "KEYCODE_TV_INPUT",
    "hdmi1": "KEYCODE_TV_INPUT_HDMI_1",
    "hdmi2": "KEYCODE_TV_INPUT_HDMI_2",
    "hdmi3": "KEYCODE_TV_INPUT_HDMI_3",
    "hdmi4": "KEYCODE_TV_INPUT_HDMI_4",
    "hdmi": "KEYCODE_TV_INPUT_HDMI_1",
    "stb": "KEYCODE_STB_INPUT",
    "composite": "KEYCODE_TV_INPUT_COMPOSITE_1",
    "vga": "KEYCODE_TV_INPUT_VGA_1",
}


@mcp.tool()
async def tv_input(source: str) -> str:
    """Change la source/entrée : tv, hdmi1..hdmi4, stb, composite, vga,
    ou « next » (balaie l'entrée suivante via TV_INPUT)."""
    s = source.strip().lower()
    key = INPUT_KEYS.get(s, "KEYCODE_TV_INPUT" if s == "next" else None)
    if key is None:
        return _err(f"source inconnue « {source} ». Valeurs : {', '.join(sorted(INPUT_KEYS))}, next")
    try:
        await _tv_action(lambda r: r.send_key_command(key, "SHORT"))
        await asyncio.sleep(1.2)
        remote = await _tv_get()
        return _ok(source=s, touche=key, application=remote.current_app, allumee=bool(remote.is_on))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


MEDIA_KEYS = {
    "play": "MEDIA_PLAY",
    "pause": "MEDIA_PAUSE",
    "playpause": "MEDIA_PLAY_PAUSE",
    "stop": "MEDIA_STOP",
    "next": "MEDIA_NEXT",
    "previous": "MEDIA_PREVIOUS",
    "rewind": "MEDIA_REWIND",
    "forward": "MEDIA_FAST_FORWARD",
    "record": "MEDIA_RECORD",
    "audio": "MEDIA_AUDIO_TRACK",
    "captions": "KEYCODE_CAPTIONS",
}


@mcp.tool()
async def tv_media(action: str = "playpause") -> str:
    """Commandes lecture : play, pause, playpause, stop, next, previous,
    rewind, forward, record, audio (piste audio), captions (sous-titres)."""
    a = action.strip().lower()
    key = MEDIA_KEYS.get(a)
    if key is None:
        return _err(f"action inconnue « {action} ». Valeurs : {', '.join(MEDIA_KEYS)}")
    try:
        await _tv_action(lambda r: r.send_key_command(key, "SHORT"))
        return _ok(action=a, touche=key, envoye=True)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_text(texte: str) -> str:
    """Saisit du texte sur la TV (clavier virtuel ouvert)."""
    try:
        await _tv_action(lambda r: r.send_text(texte))
        return _ok(envoye=texte, longueur=len(texte))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_open(cible: str) -> str:
    """Ouvre une URL ou une application par son deep-link/app-id (ex. youtube.com,
    netflix, https://www.youtube.com/watch?v=...)."""
    try:
        await _tv_action(lambda r: r.send_launch_app_command(cible))
        await asyncio.sleep(1.5)
        remote = await _tv_get()
        return _ok(cible=cible, application=remote.current_app)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_launch(nom: str) -> str:
    """Lance une application par nom connu : youtube, netflix, primevideo,
    disney, mycanal, rtlplay, vimeo, spotify, kodi, parametres, galerie…"""
    apps = {
        "youtube": "com.google.android.youtube.tv",
        "netflix": "com.netflix.ninja",
        "primevideo": "com.amazon.bueller.photos",
        "disney": "com.disney.disneyplus",
        "mycanal": "fr.canalplus.mycanal",
        "rtlplay": "be.tv.v1",
        "spotify": "com.spotify.tv.android",
        "kodi": "org.xbmc.kodi",
        "parametres": "com.android.tv.settings",
        "playstore": "com.android.vending",
        "tv": "org.droidtv.playtv",
        "tnt": "org.droidtv.playtv",
        "photoram": "com.philips.photoram",
        "browser": "com.android.browser",
    }
    key = nom.strip().lower()
    app_id = apps.get(key)
    if app_id is None:
        return _err(f"appli inconnue « {nom} ». Valeurs : {', '.join(apps)}")
    try:
        await _tv_action(lambda r: r.send_launch_app_command(app_id))
        await asyncio.sleep(2.0)
        remote = await _tv_get()
        return _ok(appli=key, app_id=app_id, application=remote.current_app)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def tv_apps() -> str:
    """Liste les applications connues par tv_launch + l'état de l'appli courante."""
    try:
        remote = await _tv_get()
        courante = remote.current_app
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    return _ok(
        application=courante,
        applis={
            "youtube": "com.google.android.youtube.tv",
            "netflix": "com.netflix.ninja",
            "primevideo": "com.amazon.bueller.photos",
            "disney": "com.disney.disneyplus",
            "mycanal": "fr.canalplus.mycanal",
            "rtlplay": "be.tv.v1",
            "spotify": "com.spotify.tv.android",
            "kodi": "org.xbmc.kodi",
            "parametres": "com.android.tv.settings",
            "tv": "org.droidtv.playtv",
        },
    )


@mcp.tool()
async def tv_pair() -> str:
    """Relance l'appairage TLS avec la TV (à utiliser seulement si la TV a oublié
    l'association : tv_status renvoie InvalidAuth). Affiche un code 6 caractères
    à confirmer avec tv_pair_finish(code)."""
    global _tv, _tv_pairing
    async with _tv_lock:
        _tv_reset()
        remote = AndroidTVRemote(
            CLIENT_NAME, CERTFILE, KEYFILE, TV_HOST,
            api_port=TV_API_PORT, pair_port=TV_PAIR_PORT,
        )
        await remote.async_generate_cert_if_missing()
        try:
            await remote.async_start_pairing()
        except (CannotConnect, ConnectionClosed) as exc:
            return _err(f"Impossible de démarrer l'appairage : {exc}")
        _tv_pairing = remote
        return _ok(message="Appairage démarré : un code à 6 caractères s'affiche sur la TV.", host=TV_HOST)


@mcp.tool()
async def tv_pair_finish(code: str) -> str:
    """Valide le code d'appairage affiché par la TV (6 caractères hex, ex. 87479F)."""
    global _tv, _tv_pairing
    remote = _tv_pairing
    if remote is None:
        return _err("Aucun appairage en cours : lancez d'abord tv_pair().")
    try:
        await remote.async_finish_pairing(code.strip().upper())
    except InvalidAuth as exc:
        return _err(f"Code refusé par la TV : {exc}")
    except ConnectionClosed as exc:
        return _err(f"Connexion perdue pendant l'appairage : {exc}")
    finally:
        _tv_pairing = None
    try:
        await remote.async_connect()
        remote.keep_reconnecting()
        _tv = remote
        return _ok(appairage="ok", etat=_status_dict(remote))
    except Exception as exc:  # noqa: BLE001
        return _ok(appairage="ok", message=f"Appairage validé, connexion à refaire : {exc}")


# --------------------------------------------------------------------------
# Freebox — lecture seule (API locale, X-Fbx-App-Auth)
# --------------------------------------------------------------------------
def _fbx_call(method: str, path: str, payload: Any = None, token: str | None = None) -> tuple[dict, int]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(FBX_BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Fbx-App-Auth", token)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode()), exc.code
        except Exception:  # noqa: BLE001
            return {"success": False, "msg": str(exc)}, exc.code
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "msg": f"{type(exc).__name__}: {exc}"}, 0


def _fbx_login() -> str:
    app = json.load(open(FBX_APP, encoding="utf-8"))
    challenge = _fbx_call("GET", "/login/")[0]["result"]["challenge"]
    pw = hmac.new(app["app_token"].encode(), challenge.encode(), hashlib.sha1).hexdigest()
    resp, code = _fbx_call("POST", "/login/session/", {"app_id": app["app_id"], "password": pw})
    if not resp.get("success"):
        raise RuntimeError(f"connexion Freebox refusée (HTTP {code}) : {resp}")
    token = resp["result"]["session_token"]
    with open(FBX_SESSION, "w", encoding="utf-8") as fh:
        json.dump({"session_token": token}, fh)
    os.chmod(FBX_SESSION, 0o600)
    return token


def _fbx_get(path: str) -> dict:
    with open(FBX_SESSION, encoding="utf-8") as fh:
        token = json.load(fh)["session_token"]
    resp, code = _fbx_call("GET", path, token=token)
    if code in (401, 403) and resp.get("error_code") in (
        "auth_required",
        "invalid_token",
        "pending_token",
        "throttled",
    ):
        token = _fbx_login()
        resp, code = _fbx_call("GET", path, token=token)
    if code != 200:
        raise RuntimeError(f"GET {path} → HTTP {code} : {json.dumps(resp, ensure_ascii=False)[:300]}")
    return resp.get("result")


def _fbx_safe(path: str) -> str:
    try:
        return _ok(result=_fbx_get(path))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def freebox_system() -> str:
    """État de la Freebox Server : uptime, températures, CPU, version, DWiFi."""
    return _fbx_safe("/system/")


@mcp.tool()
async def freebox_player() -> str:
    """État du/des décodeur(s) Freebox (reachable, api_available, modèle)."""
    return _fbx_safe("/player/")


@mcp.tool()
async def freebox_tv_channels() -> str:
    """Liste des chaînes TNT disponible via la Freebox (EPG)."""
    return _fbx_safe("/tv/channels/")


@mcp.tool()
async def freebox_tv_status() -> str:
    """État TV de la Freebox (tuner actif, verrouillage parent, source)."""
    out: dict[str, Any] = {}
    for p in ("/tv/status/", "/tv/epg/now/"):
        try:
            out[p] = _fbx_get(p)
        except Exception as exc:  # noqa: BLE001
            out[p] = f"erreur : {exc}"
    return _json({"ok": True, "resultat": out})


@mcp.tool()
async def freebox_devices() -> str:
    """Appareils connectés au réseau LAN (IP, MAC, noms, type)."""
    try:
        hosts = _fbx_get("/lan/browser/pub/") or []
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    out = []
    for h in hosts:
        names = [n.get("name") for n in h.get("names", [])]
        addrs = [
            c.get("addr")
            for c in h.get("l3connectivities", [])
            if c.get("active") and c.get("af") == "ipv4"
        ]
        out.append(
            {
                "noms": sorted({n for n in names if n}),
                "ip": addrs[0] if addrs else None,
                "mac": (h.get("l2ident") or {}).get("id"),
                "type": h.get("host_type"),
                "actif": h.get("active"),
                "fournisseur": h.get("vendor_name"),
            }
        )
    out.sort(key=lambda d: (d["ip"] or "zzz"))
    return _ok(nombre=len(out), appareils=out)


@mcp.tool()
async def freebox_wifi_status() -> str:
    """État du Wi-Fi Freebox : SSID, canaux, nombre de clients."""
    out: dict[str, Any] = {}
    for p in ("/wifi/config/", "/wifi/status/", "/wifi/stations/"):
        try:
            out[p] = _fbx_get(p)
        except Exception as exc:  # noqa: BLE001
            out[p] = f"erreur : {exc}"
    return _json({"ok": True, "resultat": out})


if __name__ == "__main__":
    mcp.run(transport="stdio")
