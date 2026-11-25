# WoWs-Mini-Relpay-Ru

[简体中文](../README.md) | [繁體中文](README.zh-TW.md) | [Русский](README.ru.md) | **English**

A flat-style minimap replay renderer built specifically for the Russian **Mir Korabley (Lesta)** client (`.korablireplay`).  
Inspired by [WoWs-Builder-Team/minimap_renderer](https://github.com/WoWs-Builder-Team/minimap_renderer), tailored strictly to Lesta's protocol and game files with a few quality-of-life features for reviewing matches:

- **Smoke Screens on the Minimap**: Tracks `SmokeScreen` creation, puff updates, and dissipation as clean unified translucent clouds.
- **Last-Known Ship Names**: When ships go dark, their last-known hollow marker stays on the minimap together with a collision-avoided ship name label.
- **Native Client Assets & Mod Localization**: Extracts official ship/plane/kill icons, maps, entity definitions, and installed localization mods straight from your local Mir Korabley folder — no manual ship tables to maintain.
- **Flat HP Bars & Battle Chat**: Tactical-Tech inspired side panels with flat continuous HP bars (100–70% green, 70–30% yellow, 30–0% red — no ship silhouettes), full-width team HP header bars, automatic sinking of destroyed ships to the bottom, and an in-game battle chat panel below the minimap.
- **Zero-Setup Desktop App**: Ships as a portable Windows EXE with FFmpeg bundled. Auto-detects both Live and Public Test (PT) installs, supports drag-and-drop batch rendering of `.korablireplay` files, custom resolution/FPS, and opens the timestamped output folder when done.
- **Multi-Language & Follow-System Theme**: Both the GUI and the rendered video support **English / Русский / 简体中文 / 繁體中文**, defaulting to your OS language (falling back to English for other locales) and OS light/dark theme.

![Render Preview](preview.gif)

---

## Quick Start (Windows Portable EXE — No Python Needed)

1. Download `WoWs-Mini-Relpay-Ru-win64.zip` from [Releases](https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru/releases).
2. **Extract** the archive anywhere and double-click `WoWs-Mini-Relpay-Ru.exe`.
3. The app automatically finds your Live / Public Test Mir Korabley folder and lists all `.korablireplay` files from `replays/`.
4. Check the battles you want to render — or simply drag and drop `.korablireplay` files into the window.
5. Pick your resolution (`1920×1080` default) and FPS (`30` default), then hit **Render**.
6. When finished, a timestamped folder containing your `.mp4` videos opens automatically.

> When Mir Korabley drops a new game patch, open **Settings → Resync Client Assets** (takes a few seconds) and you're ready for the new version. See [UPDATING.md](../UPDATING.md) for details.

---

## Running from Source / CLI

```powershell
git clone https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru.git
cd WoWs-Mini-Relpay-Ru
uv sync --extra dev

# Launch desktop GUI
.\.venv\Scripts\korabli-minimap.exe gui

# Sync local client assets
.\.venv\Scripts\korabli-minimap.exe sync --game-dir "D:\Games\Korabli"

# Render a replay via CLI in English
.\.venv\Scripts\korabli-minimap.exe render "D:\Games\Korabli\replays\xxx.korablireplay" `
  -o "output\battle.mp4" --resolution 1920x1080 --fps 30 --lang en

# Build portable EXE & release ZIP
.\.venv\Scripts\python.exe scripts\build_release.py
```
