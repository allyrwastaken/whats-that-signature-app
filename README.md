# What's That Signature

Automatically tells you which object you're looking at in **Star Citizen**. Aim
at a signature in scan mode and it names what you're scanning — the mineral and
rock count, or the object — right above the signature number on your HUD. No
typing, no alt-tabbing.

## The idea

This was built to be **deliberately unobtrusive**. It only ever reads the
small box *you* draw over the signature number — nothing else on your screen,
and never the game itself. No full-screen scanning, no reading the game's
memory, no clutter: just a small label, showing only what you point it at, on
your terms.

## Install

1. Download **WhatsThatSignature-Setup.zip** from the
   [Releases page](https://github.com/allyrwastaken/whats-that-signature-app/releases/latest).
2. **Extract it** (right-click → Extract All), then run the
   **WhatsThatSignature-Setup.exe** inside.
3. If Windows shows a blue "Windows protected your PC" box, click
   **More info → Run anyway**.
4. Open **What's That Signature** from the Start Menu.

Needs **Windows 10 or 11**, and Star Citizen set to **Borderless** display mode
(in the in-game Graphics settings).

> Running the app needs **no admin rights**. The one exception: if you launch
> Star Citizen *as administrator*, the in-game **Ctrl + S** hotkey won't fire
> unless this app is elevated too — set `"elevate": true` in `config.json` (or
> run it as administrator) for that case.

## Is it safe?

- **It's open source** (AGPL-3.0). Every line is in this repo — read it, or have
  someone you trust check it. It's small.
- **The installer is built by GitHub Actions from this public code**, not
  compiled on a private machine — see the [Actions tab](https://github.com/allyrwastaken/whats-that-signature-app/actions).
  Each release also carries a **build-provenance attestation** cryptographically
  tying the `.exe` to the exact source commit and build. Verify it with:
  `gh attestation verify WhatsThatSignature-Setup-<ver>.exe --repo allyrwastaken/whats-that-signature-app`
- **A `SHA256SUMS.txt`** is attached to every release so you can confirm your
  download wasn't tampered with.
- **It only reads pixels** from the box you draw — no full-screen scanning, no
  reading the game's memory, and no network access except checking GitHub for
  updates.
- **Antivirus false positives:** the installer is packaged with PyInstaller,
  which some scanners occasionally flag by mistake. It's been reviewed and
  cleared by Microsoft Defender; you can always confirm your download is genuine
  with the checksum and provenance attestation above. A flag from another
  scanner is a known false positive, not a real threat.

If Windows SmartScreen warns you, that's because the app isn't code-signed (a
certificate is costly for a free, one-person project) — not because anything's
wrong. The checks above let you verify it yourself. Use **More info → Run
anyway**.

## First-time setup (takes 10 seconds)

Press **Ctrl + S**, then drag a small box around the **signature number** on
your HUD — the number that shows up when you scan a rock. That's it; it
remembers the spot.

## Using it

It runs quietly in the background. Just point at signatures — the mineral name and rock
count pop up above the number and disappear when you look away.

The label **only appears while Star Citizen is the focused window**, so it
won't show up over screenshots, your browser, or anything else. (To change the
matched process, or show it everywhere, edit `game_process` in `config.json` —
set it to `""` to disable the restriction.)

- 🟢 **green** — exact match
- 🟠 **amber** — close match
- 🔴 **red** — unsure, press **Ctrl + S** and draw the box tighter

| Hotkey | Does |
|--------|------|
| **Ctrl + S** | Capture the signature area |

### Tray icon

Look for the hexagon icon in your system tray (bottom-right, by the clock).
**Double-click it** (or right-click → **Show App**) to open the app window;
right-click → **Quit** to close.

### The app window

Opening the app lets you change the capture hotkey, pick the font, and set how
long the label stays on screen — no file editing. To change the hotkey, click
it and press the keys you want.

### Updating

In the app window, click **Check for updates** (it reads **Update now** when a
new version is out). It downloads and installs the update for you and restarts
the app — no need to grab the installer manually. The app also gives you a quiet
heads-up at launch when an update is available.

## Not working?

- **Nothing appears:** make sure Star Citizen is in **Borderless** mode (not
  Fullscreen), and that you're actually focused on the game (the label only
  shows while SC is the active window).
- **Ctrl + S does nothing in-game:** you're likely running Star Citizen as
  administrator — set `"elevate": true` in `config.json` (or run this app as
  administrator).
- **Wrong or blank mineral:** press **Ctrl + S** and draw the box more tightly
  around just the number.

---

It only reads pixels from the box you draw — it never touches the game.
Made by **BunnyBlue**.
