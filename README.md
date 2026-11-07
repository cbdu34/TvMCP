<div align="center">

# 📺 TvMCP — Télécommande TV & serveur MCP

**Pilote ta TV Philips depuis ton terminal, ta voix ou n'importe quel client MCP.**

[![MCP](https://img.shields.io/badge/MCP-2.0-8A2BE2)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)](https://www.python.org)
[![tkinter](https://img.shields.io/badge/tkinter-GUI-0F9D58)](https://docs.python.org/3/library/tkinter.html)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![OpenCode](https://img.shields.io/badge/OpenCode-ready-111111)](https://opencode.ai)

*Un vrai remote-control sur ton écran + 22 outils MCP pour piloter la TV en langage naturel.*

</div>

---

## ✨ Pourquoi ?

Parce que dire **« baisse le son »** à une IA qui appuie réellement sur les boutons de ta télé,
c'est quand même plus classe qu'un clavier magique.

Ce dépôt contient **deux façons de piloter la même TV**, qui coexistent sans conflit :

| | 🖥️ `remote.py` | 🤖 `server.py` |
|---|---|---|
| **Type** | Fenêtre tkinter (télécommande visuelle) | Serveur MCP stdio |
| **Lancement** | `tec` dans le terminal | déclaré dans OpenCode / Antigravity |
| **Contrôle** | souris + clavier | langage naturel (« éteins la télé ») |
| **État** | barre d'état rafraîchie 1×/s | `tv_status` à la demande |
| **Connexion** | thread asyncio dédié | connexion paresseuse + reconnexion |

Les deux utilisent **le même certificat d'appairage** et peuvent tourner en même temps.

---

## 🎯 Fonctionnalités

### 🎮 La télécommande (`remote.py`)
- 🔴 **Touche POWER** + sources `HDMI 1/2/3` + `SOURCE`
- 🔊 **Volume** : boutons avec *appui long répétitif* + **curseur 0-60** (relâche = niveau envoyé d'un coup)
- 🧭 **Croix de navigation** avec OK central, `RETOUR`, `ACCUEIL`, `MENU`, `INFO`, `GUIDE`, `DERNIÈRE`
- 🔢 **Pavé numérique** + `CH −` / `CH +` (appui long = zapping continu)
- ▶️ **Lecture** : ⏪ ⏮ ⏯ ⏭ ⏩ ⏹ ⏺
- 🟨 **Touches couleur** ROUGE / VERT / JAUNE / BLEU
- ⌨️ **Clavier complet** : flèches, `Entrée`, `Échap`, `+`/`-`, `m`, `espace`, `0-9`…
- 🟢 **État en direct** : allumée/éteinte, source, volume, mute — pollé toutes les 1,2 s
- 📜 **Journal** des dernières actions + messages d'erreur en français
- 🔄 **Reconnexion automatique** si la TV est éteinte au lancement (2,5 s entre essais)

### 🤖 Le serveur MCP (`server.py`) — 22 outils

<details>
<summary><b>Outils TV (16)</b> — clique pour déplier</summary>

| Outil | Description |
|---|---|
| `tv_status` | Allumée, volume, source, modèle, firmare |
| `tv_keys` | Liste des 305 touches Android TV disponibles |
| `tv_key` | Envoie **une** touche (`key`, `direction` = SHORT / START_LONG / END_LONG) |
| `tv_keys_send` | Envoie une **série** (ex. zapping numérique `["KEYCODE_1","KEYCODE_4"]`) |
| `tv_power` | `on` / `off` / `toggle` |
| `tv_volume` | `up` / `down` / `set` (niveau exact) / `mute` / `unmute` |
| `tv_nav` | `up` `down` `left` `right` `ok` `back` `home` `menu` `info` `guide` + couleurs |
| `tv_channel` | `up` `down` `last` ou numéro (`"14"`) |
| `tv_input` | `tv` `hdmi1..4` `stb` `composite` `vga` `next` |
| `tv_media` | `play` `pause` `playpause` `stop` `next` `previous` `rewind` `forward` `record`… |
| `tv_text` | Saisie de texte sur le clavier virtuel |
| `tv_open` | Ouvre une URL / deep-link |
| `tv_launch` | Lance une app par nom (`youtube`, `netflix`, `mycanal`…) ⚠️ ignoré par certaines TV |
| `tv_apps` | Liste des applis connues + appli courante |
| `tv_pair` | Relance l'appairage TLS (si la TV a oublié) |
| `tv_pair_finish` | Valide le code 6 caractères affiché à l'écran |

</details>

<details>
<summary><b>Outils Freebox — lecture seule (6)</b> — clique pour déplier</summary>

| Outil | Description |
|---|---|
| `freebox_system` | Uptime, températures, versions |
| `freebox_player` | État du/des décodeurs (`reachable`, `api_available`) |
| `freebox_tv_channels` | Liste des chaînes (EPG) |
| `freebox_tv_status` | Tuner, verrouillage parent, source |
| `freebox_devices` | **104 appareils** du LAN : IP, MAC, type, fabricant |
| `freebox_wifi_status` | SSID, bandes (2.4 / 5 / 6 GHz), clients |

> L'API Freebox est appelée en **local** (`192.168.1.254`) avec l'auth `X-Fbx-App-Auth`.
> **Aucune donnée ne transite par Internet.**

</details>

---

## 🏗️ Architecture

```
┌──────────────────────────┐        ┌──────────────────────────┐
│  Ton terminal : `tec`    │        │  OpenCode / Antigravity  │
│  ┌────────────────────┐  │        │  (ou tout client MCP)    │
│  │   remote.py        │  │        │    tools.tv.tv_status()  │
│  │   fenêtre tkinter  │  │        │    tools.tv.tv_volume()  │
│  └─────────┬──────────┘  │        └────────────┬─────────────┘
│            │ thread asyncio                    │ stdio
│            └───────────────┬───────────────────┘
│                            ▼
│                 ┌─────────────────────┐
│                 │  androidtvremote2   │  protocole Android TV
│                 │  TLS :6466 / :6467  │  Remote v2
│                 │  cert : ~/.config/  │
│                 │        tvremote/    │
│                 └──────────┬──────────┘
│                            │ 192.168.1.36  (LAN, chiffré)
│                            ▼
│                 ┌─────────────────────┐
│                 │  Philips 55OLED854  │
│                 │  Android TV / S580  │
│                 └─────────────────────┘
│                                                            │
│   server.py ── aussi ──► Freebox Server 192.168.1.254/api/v4 │
│                          (lecture seule, X-Fbx-App-Auth)     │
└──────────────────────────────────────────────────────────────┘
```

---

## 🚀 Démarrage rapide

### 1. Prérequis
- Python **3.11+** avec `tkinter` (`sudo apt install python3-tk` sur Debian/Ubuntu)
- Une TV Android TV / Google TV **sur le même réseau** que la machine
- (Optionnel) une Freebox pour les outils de lecture d'état

### 2. Installation
```bash
git clone https://github.com/cbdu34/TvMCP.git
cd TvMCP

python3 -m venv .venv
.venv/bin/pip install mcp==2.3.0 androidtvremote2
```

### 3. Appairage (une seule fois)
```bash
.venv/bin/python remote.py --pair
```
> Un **code à 6 caractères** s'affiche sur la TV → tape-le dans le terminal → `Entrée`.
> Le certificat est stocké dans `~/.config/tvremote/` (permissions `600`).

Vérifie la connexion :
```bash
.venv/bin/python remote.py --check
# OK — état : {'allumee': True, 'volume': {'level': 26, 'max': 60, ...}}
```

### 4. La télécommande
```bash
alias tec="$PWD/.venv/bin/python $PWD/remote.py"
echo "alias tec=\"$PWD/.venv/bin/python $PWD/remote.py\"" >> ~/.bashrc
source ~/.bashrc

tec              # 🖥️ ouvre la télécommande
tec --check      # 🔍 diagnostique la connexion
tec --pair       # 🔗 (ré)appairage
```

---

## ⚙️ Intégration MCP

### OpenCode (`~/.config/opencode/opencode.jsonc`)
```jsonc
{
  "mcp": {
    "tv": {
      "type": "local",
      "command": ["/chemin/vers/.venv/bin/python", "/chemin/vers/TvMCP/server.py"],
      "environment": {
        "TV_HOST": "192.168.1.36",
        "FBX_BASE": "http://192.168.1.254/api/v4"
      }
    }
  }
}
```
La configuration se recharge **à la sauvegarde**, sans redémarrage.

### Google Antigravity (`~/.antigravity/mcp_config.json`)
```jsonc
{
  "mcpServers": {
    "tv": {
      "command": "/chemin/vers/.venv/bin/python",
      "args": ["/chemin/vers/TvMCP/server.py"],
      "cwd": "/chemin/vers/TvMCP",
      "env": { "TV_HOST": "192.168.1.36" }
    }
  }
}
```
> Redémarre Antigravity après modification.

### Gemini CLI (`~/.gemini/config/mcp_config.json`)
Même forme que ci-dessus (clés `command` / `args` / `cwd` / `env`).

Vérification :
```bash
opencode mcp list
# ✓ tv    connected
```

### En usage réel
> **Toi** : « monte le son à 25 »
> **L'IA** : `tv_volume({action:"set", pas:25})` → `{"ok":true,"volume":{"level":25,...}}`

---

## ⌨️ Raccourcis clavier (`remote.py`)

| Touche | Action | Touche | Action |
|---|---|---|---|
| `↑ ↓ ← →` | Navigation | `+` / `=` | Volume + |
| `Entrée` | OK | `-` | Volume − |
| `Échap` | Retour | `m` | Silence |
| `Espace` | Lecture / Pause | `i` | Info |
| `h` | Accueil | `g` | Guide |
| `0` – `9` | Chiffres (zapping) | | |

---

## 📁 Structure du dépôt

```
TvMCP/
├── README.md        # ce fichier
├── LICENSE          # MIT
├── .gitignore       # __pycache__, *.pem, .venv, .env
├── server.py        # 🤖 serveur MCP (22 outils)
└── remote.py        # 🎮 télécommande tkinter + CLI (--check / --pair)
```

**Aucun secret dans le dépôt** : certificats TLS, token Freebox et identifiants restent
dans `~/.config/` (jamais versionnés).

---

## 🩺 Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `Connexion ... échouée` en boucle | TV éteinte ou IP changée | Allume la TV ; vérifie l'IP (`TV_HOST`) dans le DHCP Freebox |
| `InvalidAuth` | Certificat perdu / TV réinitialisée | `tec --pair` |
| La touche ne fait rien | TV sur une source HDMI sans récepteur | Passe en `SOURCE` / `TV` |
| `launch` sans effet | Certaines Philips ignorent l'intent d'app | Navigation manuelle (flèches + OK) |
| `ConnectionClosed` répété | Le protocole n'accepte qu'un client actif | Ferme l'autre instance (MCP ou GUI) |
| Émoji gris / cassé | Police sans emoji | Le GUI utilise maintenant du texte et de vraies couleurs |

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `TV_HOST` | `192.168.1.36` | IP de la TV |
| `TV_API_PORT` | `6466` | Port de contrôle TLS |
| `TV_PAIR_PORT` | `6467` | Port d'appairage |
| `TV_CLIENT_NAME` | `OpenCode` | Nom affiché pendant l'appairage |
| `TV_CERT_DIR` | `~/.config/tvremote` | Dossier des certificats |
| `FBX_BASE` | `http://192.168.1.254/api/v4` | API Freebox locale |

---

## 📜 Notes techniques

- **Protocole** : [Android TV Remote Protocol v2](https://android.googlesource.com/platform/external/google-tv-pairing-protocol/) (le même que l'app Google TV) — pas d'ADB, pas de mode développeur.
- **Bibliothèque** : [`androidtvremote2`](https://github.com/tronikos/androidtvremote2) (tronikos) — TLS mutuel avec certificat auto-signé généré localement.
- **MCP** : SDK officiel [`mcp`](https://github.com/modelcontextprotocol/python-sdk) 2.3.0, transport `stdio`, réponses en JSON.
- **Testé sur** : Philips 55OLED854/12 (firmware `7.00.999235495`, `TPM191E`), Freebox Server v9 (API v16.1).
- **Compatibilité** : toute TV exposant le service `androidtvremote2` (Sony, Philips récentes, TCL, Xiaomi…). Pense à vérifier via `avahi-browse _androidtvremote2._tcp`.

---

## 📄 Licence

Distribué sous licence [MIT](LICENSE).

---

<div align="center">

**Fabriqué avec** 🐍 Python · tkinter · asyncio · `androidtvremote2` · MCP

*Une issue, une idée d'amélioration ? [Ouvre-la](https://github.com/cbdu34/TvMCP/issues) !*

</div>
