"""What's That Signature — automatic scan-signature reader for Star Citizen.

Continuously reads the signature number from a region you pick and floats the
matching mineral or category (with rock count) just above it. Screen-reading
only — it never touches the game process or memory.

Runs in the background and detects automatically. One hotkey (configurable in
config.json): the capture hotkey (default Ctrl+S) (re)draws the box over the
signature number. Open the app window (tray icon -> Show App, or double-click
it) for settings and updates; quit from the tray.

Usage
-----
  overlay.py                  # run the auto-detecting overlay
  overlay.py --calibrate      # just (re)draw the capture box and exit
  overlay.py --probe          # capture the saved box once and OCR it
  overlay.py --test-image shot.png   # OCR a saved screenshot
"""

import argparse
import json
import os
import queue
import re
import sys
import threading

import minerals
import updater

APP_NAME = "What's That Signature"     # display name
APP_SLUG = "WhatsThatSignature"        # identifier (exe, data folder, etc.)
APP_VERSION = "1.0.0"
KOFI_URL = "https://ko-fi.com/allyrwastaken"
HERE = os.path.dirname(os.path.abspath(__file__))
# Bundled resources live under sys._MEIPASS when frozen, next to the script otherwise.
ICON_PATH = os.path.join(getattr(sys, "_MEIPASS", HERE), "assets", "signature_overlay.ico")


def ensure_elevated():
    """Relaunch elevated if not already admin, then signal the caller to exit.

    Opt-in (config "elevate"). Only needed when Star Citizen itself runs as
    admin — the keyboard hook can't fire over a higher-privilege window
    otherwise. The exe ships as asInvoker (so it launches fine from the
    installer / Start Menu) and elevates itself here with one UAC prompt.
    Returns True if we should keep running, False if an elevated copy was
    launched and this instance should quit.
    """
    import ctypes
    import subprocess

    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
    except Exception:
        return True  # can't determine — carry on
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_ssize_t
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, subprocess.list2cmdline(sys.argv[1:])
        else:
            exe = sys.executable
            params = subprocess.list2cmdline([os.path.abspath(__file__)] + sys.argv[1:])
        rc = shell32.ShellExecuteW(None, "runas", exe, params, None, 1)  # SW_SHOWNORMAL
        if rc > 32:
            return False  # elevated instance launched; quit this one
    except Exception:
        pass
    return True  # elevation declined/failed — run as-is (best effort)


def _data_dir():
    """Writable folder for config/logs. Next to the script in dev, but under
    %LOCALAPPDATA% when frozen (the install dir may not be writable)."""
    if not getattr(sys, "frozen", False):
        return HERE
    root = os.environ.get("LOCALAPPDATA", HERE)
    base = os.path.join(root, APP_SLUG)
    try:
        os.makedirs(base, exist_ok=True)
        # Carry over settings from the old "Signature Overlay" name on upgrade.
        old = os.path.join(root, "SignatureOverlay", "config.json")
        new = os.path.join(base, "config.json")
        if os.path.exists(old) and not os.path.exists(new):
            import shutil
            shutil.copyfile(old, new)
        return base
    except Exception:
        return HERE


DATA_DIR = _data_dir()
DEFAULT_CONFIG = os.path.join(DATA_DIR, "config.json")


def setup_logging():
    """In a frozen windowed build there's no console, so print() would fail
    (stdout is None). Redirect output to a log file in the data dir."""
    if not getattr(sys, "frozen", False):
        return
    try:
        f = open(os.path.join(DATA_DIR, "overlay.log"), "w", buffering=1,
                 encoding="utf-8", errors="replace")
    except Exception:
        import io
        f = io.StringIO()
    sys.stdout = sys.stderr = f


COLORKEY = "#010101"  # near-black key color, turned fully transparent — chosen
                      # so anti-aliased text edges fringe dark (invisible), not pink.

# Plausible signature range: smallest is Salvage x1 (2000), largest is
# ROC Mineables x20 (80000). Margins absorb OCR noise; wilder reads are junk.
MIN_PLAUSIBLE, MAX_PLAUSIBLE = 1800, 81000

DEFAULTS = {
    "region": None,                       # {left, top, width, height} in physical px
    "hotkey_snip": "ctrl+s",              # capture the signature area
    "font": "Cascadia Mono",              # overlay label font (any installed font)
    "upscale": 4,                         # enlarge the crop this many times before OCR
    "match_tolerance": 40,                # |delta| above this = "uncertain"
    "poll_ms": 300,                       # how often to read the box (milliseconds)
    "confirm_reads": 2,                   # identical reads before showing a value
    "linger_ms": 5000,                    # keep the label up this long after the number is gone
    "game_process": "StarCitizen.exe",    # only detect when this is the focused app ("" = always)
    "elevate": False,                     # self-elevate on launch (only if SC runs as admin)
}

SNIP_ID, SETTINGS_ID, UPDATE_ID, QUIT_ID = 1, 2, 3, 4  # action ids


# --------------------------------------------------------------------------- #
# DPI awareness — must run before any capture or Tk window is created.
# --------------------------------------------------------------------------- #
def set_dpi_aware():
    import ctypes

    try:  # Windows 10 1703+: per-monitor-aware v2 (best). Returns BOOL.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:  # Windows 8.1+: per-monitor aware. Returns HRESULT (0 == S_OK).
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:  # Vista+: system DPI aware
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "none"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            # A hand-edited config with a syntax error shouldn't crash the app.
            print(f"Warning: could not read {path} ({e}); using defaults.",
                  file=sys.stderr)
    return cfg


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# --------------------------------------------------------------------------- #
# OCR (Windows.Media.Ocr via winsdk)
# --------------------------------------------------------------------------- #
class WindowsOCR:
    """Wraps the built-in Windows OCR engine (Windows.Media.Ocr via winsdk)."""

    def __init__(self):
        import asyncio
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            raise RuntimeError(
                "Windows OCR engine unavailable. Add an OCR language pack via "
                "Settings > Time & language > Language > English > Optional features."
            )
        self._engine = engine
        self._loop = asyncio.new_event_loop()

    def read(self, pil_image):
        from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
        from winsdk.windows.security.cryptography import CryptographicBuffer

        img = pil_image.convert("RGBA")
        data = img.tobytes("raw", "BGRA")  # Windows wants BGRA byte order
        buf = CryptographicBuffer.create_from_byte_array(data)
        bmp = SoftwareBitmap.create_copy_from_buffer(
            buf, BitmapPixelFormat.BGRA8, img.width, img.height
        )
        result = self._loop.run_until_complete(self._engine.recognize_async(bmp))
        return result.text or ""


def parse_number(text):
    """Pull the most likely number from OCR text: the longest run of digits
    (commas/periods stripped), or None if there are none."""
    groups = re.findall(r"\d+", text.replace(",", "").replace(".", ""))
    if not groups:
        return None
    try:
        return int(max(groups, key=len))
    except ValueError:
        return None


_TARGET_TEXT_HEIGHT = 150  # upscale small captures to about this many px tall


def _ocr_variants(pil_rgb, upscale):
    """Yield several image treatments; OCR is finicky about contrast/polarity
    and struggles with small/low-resolution text, so we enlarge generously and
    sharpen before binarizing. We also include colour-channel variants because
    light text on a coloured background (e.g. white/yellow signature text over a
    yellow moon surface) has poor *luminance* contrast that plain grayscale
    discards — read_value keeps whichever variant reads cleanest."""
    from PIL import Image, ImageOps, ImageFilter

    rgb = pil_rgb.convert("RGB")
    # Scale by the configured multiplier, but always enlarge enough that the
    # text reaches a comfortable size for the OCR engine — this is what makes
    # detection work on smaller / lower-resolution monitors. LANCZOS keeps
    # edges crisp; the factor is capped so the image never gets absurdly large.
    factor = max(upscale or 1, _TARGET_TEXT_HEIGHT / max(1, rgb.height))
    factor = min(factor, 12)
    if factor > 1:
        rgb = rgb.resize((max(1, round(rgb.width * factor)),
                          max(1, round(rgb.height * factor))), Image.LANCZOS)

    g = rgb.convert("L")
    base = ImageOps.autocontrast(g)
    sharp = base.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
    thr = sharp.point(lambda p: 255 if p > 128 else 0)
    yield base                       # light or dark, as-is
    yield ImageOps.invert(base)      # opposite polarity
    yield sharp                      # edge-sharpened (helps blurry small text)
    yield thr                        # hard black/white
    yield ImageOps.invert(thr)

    # Colour-aware variants for low-luminance-contrast cases. On a yellow
    # background the blue channel makes white/light text bright and the yellow
    # surface dark; the (inverted) saturation channel does the same, since white
    # text is unsaturated while yellow is highly saturated.
    blue = ImageOps.autocontrast(rgb.getchannel("B"))
    desat = ImageOps.autocontrast(ImageOps.invert(rgb.convert("HSV").getchannel("S")))
    for ch in (blue, desat):
        yield ch
        yield ch.point(lambda p: 255 if p > 128 else 0)


def read_value(ocr, pil_rgb, upscale):
    """OCR an image several ways; return (value, raw_text) for the cleanest read.

    "Cleanest" = the candidate whose value lands closest to a real signature in
    the table, which lets the known data correct minor OCR noise.
    """
    candidates = []
    for variant in _ocr_variants(pil_rgb, upscale):
        text = ocr.read(variant)
        v = parse_number(text)
        if v is not None and MIN_PLAUSIBLE <= v <= MAX_PLAUSIBLE:
            candidates.append((minerals.best_match(v)["abs_delta"], v, text))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1], candidates[0][2]


# --------------------------------------------------------------------------- #
# Global hotkey — a WH_KEYBOARD_LL low-level keyboard hook (see HotkeyThread),
# which fires even while Star Citizen has focus.
# --------------------------------------------------------------------------- #
_MODS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
_SPECIAL = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D,
            "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
            "`": 0xC0, "-": 0xBD, "=": 0xBB}


def parse_hotkey(spec):
    """'ctrl+alt+s' -> (modifier_mask, virtual_key) or (None, None) if invalid."""
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x70 + int(part[1:]) - 1          # F1..F24
        elif part in _SPECIAL:
            vk = _SPECIAL[part]
    return (mods, vk) if vk else (None, None)


class HotkeyThread(threading.Thread):
    """Global hotkeys via a WH_KEYBOARD_LL low-level keyboard hook.

    A low-level hook intercepts keys earlier than RegisterHotKey and wins
    against games (Star Citizen included) that swallow the normal hotkey path —
    the same mechanism AutoHotkey uses. Modifier state is checked with
    GetAsyncKeyState at the moment the trigger key goes down.
    """

    WM_QUIT = 0x0012

    def __init__(self, bindings, on_fire):
        super().__init__(daemon=True)
        self.bindings = bindings          # list of (id, mods, vk, label)
        self.on_fire = on_fire            # called(id) from the hook thread
        self._tid = None
        self._proc = None                 # keep the C callback alive

    def run(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
        VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
        VK_LWIN, VK_RWIN = 0x5B, 0x5C

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)
        # Correct restypes/argtypes so 64-bit handles aren't truncated.
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                             wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        triggers = {vk: (hid, mods, label) for hid, mods, vk, label in self.bindings}
        held = set()  # trigger vks currently down (debounce one fire per press)

        def down(vk):
            return user32.GetAsyncKeyState(vk) & 0x8000

        def mods_match(mask):
            return (bool(down(VK_CONTROL)) == bool(mask & 0x2)
                    and bool(down(VK_MENU)) == bool(mask & 0x1)
                    and bool(down(VK_SHIFT)) == bool(mask & 0x4)
                    and bool(down(VK_LWIN) or down(VK_RWIN)) == bool(mask & 0x8))

        def proc(nCode, wParam, lParam):
            if nCode == 0:
                vk = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if vk in triggers and vk not in held:
                        held.add(vk)
                        hid, mask, _ = triggers[vk]
                        if mods_match(mask):
                            self.on_fire(hid)
                elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                    held.discard(vk)
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(proc)
        self._tid = kernel32.GetCurrentThreadId()
        hhook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hhook:
            print(f"WARNING: keyboard hook failed (error {ctypes.get_last_error()})",
                  file=sys.stderr)
            return
        for _, _, _, label in self.bindings:
            print(f"Listening for hotkey: {label}")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hhook)

    def stop(self):
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)


# --------------------------------------------------------------------------- #
# Snip selector — drag a box on a translucent full-screen overlay.
# --------------------------------------------------------------------------- #
def cursor_pos():
    """Current mouse position in true physical screen pixels (DPI-aware)."""
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground_exe():
    """Lowercased basename of the foreground window's executable, or '' if it
    can't be determined (so callers can fail open)."""
    import ctypes
    from ctypes import wintypes

    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        kernel32.OpenProcess.restype = wintypes.HANDLE  # 64-bit handle; don't truncate
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def snip_region(root):
    """Drag a box over the number on a translucent full-screen overlay.

    The box is taken from GetCursorPos (true physical pixels in our DPI-aware
    process), which is exactly the coordinate space mss captures from — so it's
    1:1 with no scaling math, correct on any resolution / DPI / monitor layout.
    Returns {left, top, width, height} or None if cancelled.
    """
    import tkinter as tk

    top = tk.Toplevel(root)
    top.attributes("-fullscreen", True)
    top.attributes("-alpha", 0.25)
    top.attributes("-topmost", True)
    top.configure(bg="black")
    canvas = tk.Canvas(top, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(top.winfo_screenwidth() // 2, 40, fill="#4ea3ff",
                       font=("Segoe UI Semibold", 16),
                       text="Drag a box around the SIGNATURE NUMBER, then release."
                            "   (Esc to cancel)")
    st = {"cx0": 0, "cy0": 0, "px0": 0, "py0": 0,
          "rect": None, "readout": None, "region": None}

    def on_press(e):
        st["cx0"], st["cy0"] = e.x, e.y
        st["px0"], st["py0"] = cursor_pos()
        st["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                             outline="#5af2a3", width=2)

    def on_drag(e):
        canvas.coords(st["rect"], st["cx0"], st["cy0"], e.x, e.y)
        if st["readout"]:
            canvas.delete(st["readout"])
        px, py = cursor_pos()
        st["readout"] = canvas.create_text(
            e.x + 12, e.y + 12, anchor="w", fill="#5af2a3", font=("Segoe UI", 11),
            text=f"{abs(px - st['px0'])} × {abs(py - st['py0'])} px")

    def on_release(e):
        px, py = cursor_pos()
        w, h = abs(px - st["px0"]), abs(py - st["py0"])
        if w < 6 or h < 6:
            return
        st["region"] = {"left": min(st["px0"], px), "top": min(st["py0"], py),
                        "width": w, "height": h}
        top.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    top.bind("<Escape>", lambda e: top.destroy())
    top.grab_set()
    top.focus_force()
    _force_foreground(top)  # ensure the selector sits above a borderless game
    root.wait_window(top)
    return st["region"]


def _force_foreground(win):
    """Push a window above a borderless fullscreen game and give it focus."""
    try:
        import ctypes

        win.update_idletasks()
        win.lift()
        win.attributes("-topmost", True)
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE = -1, 0x2, 0x1
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Capture helper
# --------------------------------------------------------------------------- #
def grab_region(region):
    import mss
    from PIL import Image

    monitor = {"left": region["left"], "top": region["top"],
               "width": region["width"], "height": region["height"]}
    with mss.MSS() as sct:
        shot = sct.grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.rgb)


# --------------------------------------------------------------------------- #
# Overlay window
# --------------------------------------------------------------------------- #
class Overlay:
    """A compact, click-through label that floats just above the capture box
    and shows the current mineral, or nothing when no number is detected."""

    W, H = 160, 40   # W is the *minimum* width; it grows to fit the label

    def __init__(self, root, tolerance, font="Cascadia Mono"):
        import tkinter as tk

        self.root = root
        self.tolerance = tolerance
        self.set_font(font)
        self.hwnd = None
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", COLORKEY)
        root.configure(bg=COLORKEY)
        root.geometry(f"{self.W}x{self.H}+0+0")
        self.canvas = tk.Canvas(root, width=self.W, height=self.H,
                                bg=COLORKEY, highlightthickness=0)
        self.canvas.pack()
        self._make_click_through()
        self.hide()

    def _make_click_through(self):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
            WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x8000000, 0x80
            LWA_COLORKEY = 0x1
            # Realize the window first so the real top-level frame exists —
            # otherwise GetParent() returns 0 and the styles land on the child,
            # leaving the frame (which Windows hit-tests) NOT click-through.
            self.root.update_idletasks()
            child = self.root.winfo_id()
            self.hwnd = user32.GetParent(child) or child   # top-level frame
            # Apply click-through to the frame (and child) so the mouse passes
            # straight through to the game.
            for h in {self.hwnd, child}:
                style = user32.GetWindowLongW(h, GWL_EXSTYLE)
                user32.SetWindowLongW(
                    h, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                    | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
                # Re-establish the color key — changing the ex-style clears the
                # layered attributes Tk set, which would leave the window fully
                # transparent (invisible). COLORREF 0x00BBGGRR == #010101.
                user32.SetLayeredWindowAttributes(h, 0x00010101, 0, LWA_COLORKEY)
        except Exception:
            pass

    def _reassert_topmost(self):
        # Keep the label above a borderless game window.
        try:
            import ctypes

            HWND_TOPMOST = -1
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
            if self.hwnd:
                ctypes.windll.user32.SetWindowPos(
                    self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def _text(self, x, y, s, fill, font, anchor="center"):
        """Draw text with an even dark outline (no drop shadow) so it stays
        legible over any scene."""
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            self.canvas.create_text(x + dx, y + dy, text=s, fill="#000000",
                                    font=font, anchor=anchor)
        self.canvas.create_text(x, y, text=s, fill=fill, font=font, anchor=anchor)

    def hide(self):
        self.canvas.delete("all")

    def set_font(self, font):
        import tkinter.font as tkfont
        self.name_font = (font, 17, "bold")
        self._measure_font = tkfont.Font(root=self.root, family=font,
                                         size=17, weight="bold")

    def _render(self, text, color, region):
        """Size the window to the text, center it above the capture box, draw."""
        self.canvas.delete("all")
        w = max(self.W, self._measure_font.measure(text) + 40)
        self.canvas.configure(width=w, height=self.H)
        cx = region["left"] + region["width"] // 2
        sw = self.root.winfo_screenwidth()
        x = max(0, min(cx - w // 2, sw - w))
        y = max(0, region["top"] - self.H - 4)
        self.root.geometry(f"{w}x{self.H}+{x}+{y}")
        self._text(w // 2, self.H // 2, text, color, self.name_font)
        self._reassert_topmost()

    def show_message(self, text, region, color="#4ea3ff"):
        """Flash a short status line above the box (used for confirmations)."""
        self._render(text, color, region)

    def show_value(self, value, region):
        cands = minerals.matches(value)
        if not cands:
            return
        if len(cands) == 1:
            e = cands[0]
            ad = e["abs_delta"]
            color = "#5af2a3" if ad == 0 else ("#ffb86c" if ad <= self.tolerance else "#ff6b6b")
            text = minerals.label_for(e)
        else:
            # Genuine round-number tie (Salvage / FPS / ROC) — list them all.
            color = "#ffb86c"
            text = "  /  ".join(
                f"{minerals.short_name(e['name'])} ×{e['count']}"
                if minerals.shows_count(e["name"])
                else minerals.short_name(e["name"])
                for e in cands)
        self._render(text, color, region)


# --------------------------------------------------------------------------- #
# System-tray icon image
# --------------------------------------------------------------------------- #
def tray_image():
    import math
    from PIL import Image, ImageDraw

    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = s / 2, s / 2, s * 0.36
    pts = [(cx + r * math.cos(math.radians(60 * i - 30)),
            cy + r * math.sin(math.radians(60 * i - 30))) for i in range(6)]
    d.line(pts + [pts[0]], fill=(78, 163, 255, 255), width=4, joint="curve")
    rr = s * 0.12
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(90, 242, 163, 255))
    return img


# --------------------------------------------------------------------------- #
# Settings window — themed like the whats-that-signature website
# --------------------------------------------------------------------------- #
# Frontier Consolidated palette (dark navy, blue/green accents).
_TH = {
    "bg": "#0d1620", "panel": "#142031", "field": "#1a2a3e",
    "accent": "#4ea3ff", "green": "#5af2a3", "text": "#d8e4f2",
    "muted": "#6a8aac",
}
_DISPLAY_FONT = "Bahnschrift"
_MONO_FONT = "Cascadia Mono"
_MOD_KEYSYMS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L",
                "Shift_R", "Super_L", "Super_R", "Win_L", "Win_R"}


def _keysym_to_token(ks):
    if len(ks) == 1 and ks.isalnum():
        return ks.lower()
    if ks.startswith("F") and ks[1:].isdigit():
        return "f" + ks[1:]
    return {"space": "space", "Return": "enter", "Tab": "tab", "Insert": "insert",
            "Home": "home", "End": "end", "Prior": "pageup",
            "Next": "pagedown"}.get(ks)


def _held_mods():
    import ctypes
    u = ctypes.windll.user32

    def down(vk):
        return u.GetAsyncKeyState(vk) & 0x8000

    mods = []
    if down(0x11):
        mods.append("ctrl")
    if down(0x12):
        mods.append("alt")
    if down(0x10):
        mods.append("shift")
    if down(0x5B) or down(0x5C):
        mods.append("win")
    return mods


def _decorate_window(win):
    """Give a Tk window the app icon and a dark, themed title bar. Dark mode
    works on Windows 10 1809+; the accent caption/text/border colors apply on
    Windows 11. All calls fail gracefully on older systems."""
    try:
        win.iconbitmap(ICON_PATH)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

        def attr(code, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, code, ctypes.byref(v), ctypes.sizeof(v))

        attr(20, 1)             # DWMWA_USE_IMMERSIVE_DARK_MODE — dark title bar
        attr(35, 0x00312014)    # DWMWA_CAPTION_COLOR -> #142031 (Win11)
        attr(36, 0x00FFA34E)    # DWMWA_TEXT_COLOR    -> #4ea3ff (Win11)
        attr(34, 0x00FFA34E)    # DWMWA_BORDER_COLOR  -> #4ea3ff accent (Win11)
    except Exception:
        pass


def open_settings(root, cfg, config_path, apply_cb, update_tag=None, on_update=None):
    """Open the themed app window and return it. apply_cb(cfg) runs on save;
    on_update() runs when the update button is clicked; update_tag (if set)
    shows an 'update available' notice."""
    import tkinter as tk
    import tkinter.font as tkfont
    import webbrowser

    T = _TH
    win = tk.Toplevel(root)
    win.title(f"{APP_NAME} {APP_VERSION}")
    win.configure(bg=T["bg"])
    # A normal window (not an overlay): comes to the front when opened, but can
    # be covered by Star Citizen afterwards instead of staying on top.
    win.resizable(False, False)
    win.columnconfigure(0, weight=1)  # let rows stretch to the window width
    _decorate_window(win)

    # Header band.
    head = tk.Frame(win, bg=T["panel"])
    head.grid(row=0, column=0, sticky="ew")
    tk.Label(head, text="WHAT'S THAT SIGNATURE", bg=T["panel"], fg=T["accent"],
             font=(_DISPLAY_FONT, 18, "bold")).pack(anchor="w", padx=18, pady=(12, 0))
    tk.Label(head, text=f"v{APP_VERSION}", bg=T["panel"], fg=T["muted"],
             font=(_MONO_FONT, 9)).pack(anchor="w", padx=18, pady=(0, 4))

    last_pad = (0, 12)
    if cfg.get("region"):
        tk.Label(head, text="● Overlay running", bg=T["panel"], fg=T["green"],
                 font=(_MONO_FONT, 9)).pack(anchor="w", padx=18,
                                            pady=(0, 2 if update_tag else 12))
    else:
        snip = cfg.get("hotkey_snip", "ctrl+s").upper()
        tk.Label(head, text=f"○ No capture box yet — press {snip} to set one",
                 bg=T["panel"], fg="#ffb86c", font=(_MONO_FONT, 9)).pack(
                 anchor="w", padx=18, pady=(0, 2 if update_tag else 12))
    if update_tag:
        tk.Label(head, text=f"Update available ({update_tag}) — consider updating",
                 bg=T["panel"], fg="#ffb86c", font=(_MONO_FONT, 9)
                 ).pack(anchor="w", padx=18, pady=last_pad)

    body = tk.Frame(win, bg=T["bg"])
    body.grid(row=1, column=0, sticky="ew", padx=18, pady=14)
    body.columnconfigure(1, weight=1)  # control column fills to the right margin

    status = tk.Label(win, text="", bg=T["bg"], fg=T["green"], font=(_MONO_FONT, 9))
    status.grid(row=2, column=0, sticky="w", padx=18)

    def label(parent, text, r):
        tk.Label(parent, text=text, bg=T["bg"], fg=T["text"],
                 font=(_DISPLAY_FONT, 12)).grid(row=r, column=0, sticky="w", pady=7)

    # --- Hotkey capture rows --------------------------------------------- #
    spec_vars = {}

    def add_hotkey(text, key, r):
        label(body, text, r)
        var = tk.StringVar(value=cfg.get(key, "").upper())
        spec_vars[key] = var
        btn = tk.Button(body, textvariable=var, width=16, relief="flat",
                        bg=T["field"], fg=T["accent"], activebackground=T["panel"],
                        activeforeground=T["green"], font=(_MONO_FONT, 10),
                        cursor="hand2")
        btn.grid(row=r, column=1, sticky="e", padx=(20, 0))

        def capture():
            var.set("press keys…")

            def on_key(e):
                # Swallow every key while capturing so e.g. Tab doesn't also
                # move focus and Ctrl+Tab is captured cleanly.
                if e.keysym in _MOD_KEYSYMS:
                    return "break"
                tok = _keysym_to_token(e.keysym)
                if tok:
                    var.set("+".join(_held_mods() + [tok]).upper())
                    done()
                return "break"

            def cancel(e=None):
                var.set(cfg.get(key, "").upper())
                done()

            def done():
                win.unbind("<KeyPress>")
                win.unbind("<Escape>")
                try:
                    win.grab_release()
                except Exception:
                    pass

            win.grab_set()
            win.bind("<KeyPress>", on_key)
            win.bind("<Escape>", cancel)

        btn.configure(command=capture)

    add_hotkey("Capture area hotkey", "hotkey_snip", 0)

    # --- Font ------------------------------------------------------------- #
    label(body, "Overlay font", 1)
    installed = set(tkfont.families())
    fonts = [f for f in ("Cascadia Mono", "Consolas", "Bahnschrift",
                         "Bahnschrift SemiBold", "Lucida Console", "Segoe UI")
             if f in installed] or ["Segoe UI"]
    font_var = tk.StringVar(value=cfg.get("font", fonts[0]))
    fmenu = tk.OptionMenu(body, font_var, *fonts)
    fmenu.configure(bg=T["field"], fg=T["text"], activebackground=T["panel"],
                    activeforeground=T["green"], relief="flat", highlightthickness=0,
                    font=(_MONO_FONT, 10), width=15)
    fmenu["menu"].configure(bg=T["field"], fg=T["text"], font=(_MONO_FONT, 10))
    fmenu.grid(row=1, column=1, sticky="e", padx=(20, 0))

    # --- Linger seconds --------------------------------------------------- #
    label(body, "Keep label on screen (sec)", 2)
    linger_var = tk.StringVar(value=str(round(cfg.get("linger_ms", 5000) / 1000)))
    spin = tk.Spinbox(body, from_=1, to=20, textvariable=linger_var, width=6,
                      bg=T["field"], fg=T["text"], buttonbackground=T["panel"],
                      relief="flat", justify="center", font=(_MONO_FONT, 10),
                      insertbackground=T["text"])
    spin.grid(row=2, column=1, sticky="e", padx=(20, 0))

    # --- Buttons ---------------------------------------------------------- #
    btns = tk.Frame(win, bg=T["bg"])
    btns.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 16))

    def save():
        for key, var in spec_vars.items():
            spec = var.get().strip().lower()
            if not parse_hotkey(spec)[1]:
                status.configure(text=f"Invalid hotkey for {key}.", fg=T["accent"])
                return
            cfg[key] = spec
        cfg["font"] = font_var.get()
        try:
            cfg["linger_ms"] = max(1, int(float(linger_var.get()))) * 1000
        except ValueError:
            pass
        save_config(config_path, cfg)
        apply_cb(cfg)
        # Stay open so you can keep tweaking (e.g. trying fonts); Exit closes.
        status.configure(text="Settings saved.", fg=T["green"])

    # All four buttons share these so they're identical in size.
    btn_style = dict(relief="flat", width=10, font=(_DISPLAY_FONT, 11, "bold"),
                     cursor="hand2")

    # Utility buttons (left).
    def do_update():
        win.destroy()
        if on_update:
            on_update()

    tk.Button(btns, text="Updates", command=do_update, bg=T["field"],
              fg=("#ffb86c" if update_tag else T["accent"]), activebackground=T["panel"],
              activeforeground=T["green"], **btn_style).pack(side="left")
    tk.Button(btns, text="☕ Ko-fi", command=lambda: webbrowser.open(KOFI_URL),
              bg=T["field"], fg="#ff7b78", activebackground=T["panel"],
              activeforeground="#ff5e5b", **btn_style).pack(side="left", padx=(10, 0))

    # Exit (to tray) / Save (right).
    actions = tk.Frame(btns, bg=T["bg"])
    actions.pack(side="right")
    tk.Button(actions, text="EXIT", command=win.destroy, bg=T["field"], fg=T["muted"],
              activebackground=T["panel"], activeforeground=T["text"],
              **btn_style).pack(side="left", padx=(0, 10))
    tk.Button(actions, text="SAVE", command=save, bg=T["green"], fg=T["bg"],
              activebackground=T["accent"], activeforeground=T["bg"],
              **btn_style).pack(side="left")

    win.update_idletasks()
    # Widen a little so the left buttons and Exit/Save have clear space between
    # them, and center on screen.
    w = max(580, win.winfo_reqwidth())
    h = win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
    win.lift()
    win.focus_force()
    return win


def themed_dialog(root, title, message, yes_no=False, yes_text="OK", no_text="Cancel"):
    """Small always-on-top themed dialog. Returns True if confirmed."""
    import tkinter as tk

    T = _TH
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=T["bg"])
    win.attributes("-topmost", True)
    win.resizable(False, False)
    _decorate_window(win)
    tk.Label(win, text=title, bg=T["bg"], fg=T["accent"],
             font=(_DISPLAY_FONT, 15, "bold")).pack(anchor="w", padx=22, pady=(18, 6))
    tk.Label(win, text=message, bg=T["bg"], fg=T["text"], justify="left",
             font=(_MONO_FONT, 10)).pack(anchor="w", padx=22, pady=(0, 14))
    res = {"ok": False}
    bf = tk.Frame(win, bg=T["bg"])
    bf.pack(anchor="e", padx=22, pady=(0, 18))

    def ok():
        res["ok"] = True
        win.destroy()

    if yes_no:
        tk.Button(bf, text=no_text, command=win.destroy, relief="flat",
                  bg=T["field"], fg=T["muted"], activebackground=T["panel"],
                  activeforeground=T["text"], font=(_DISPLAY_FONT, 11, "bold"),
                  width=10, cursor="hand2").pack(side="left", padx=(0, 10))
        tk.Button(bf, text=yes_text, command=ok, relief="flat", bg=T["green"],
                  fg=T["bg"], activebackground=T["accent"], activeforeground=T["bg"],
                  font=(_DISPLAY_FONT, 11, "bold"), width=10,
                  cursor="hand2").pack(side="left")
    else:
        tk.Button(bf, text="OK", command=win.destroy, relief="flat", bg=T["field"],
                  fg=T["text"], activebackground=T["panel"], activeforeground=T["green"],
                  font=(_DISPLAY_FONT, 11, "bold"), width=10,
                  cursor="hand2").pack()
    win.update_idletasks()
    # Center on screen so the prompt is never missed in a corner.
    ww, wh = win.winfo_width(), win.winfo_height()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 3}")
    win.lift()
    win.focus_force()
    win.grab_set()
    root.wait_window(win)
    return res["ok"]


def update_dialog(root, current, latest, notes):
    """'Update available' window showing the new version's changelog.
    Returns True if the user chooses to update."""
    import tkinter as tk

    T = _TH
    win = tk.Toplevel(root)
    win.title("Update available")
    win.configure(bg=T["bg"])
    win.attributes("-topmost", True)
    win.resizable(False, False)
    _decorate_window(win)

    tk.Label(win, text="Update available", bg=T["bg"], fg=T["accent"],
             font=(_DISPLAY_FONT, 16, "bold")).pack(anchor="w", padx=22, pady=(18, 2))
    tk.Label(win, text=f"v{current}  →  {latest}", bg=T["bg"], fg=T["muted"],
             font=(_MONO_FONT, 10)).pack(anchor="w", padx=22, pady=(0, 8))
    tk.Label(win, text="What's new", bg=T["bg"], fg=T["text"],
             font=(_DISPLAY_FONT, 11, "bold")).pack(anchor="w", padx=22)

    # Scrollable, read-only changelog.
    frame = tk.Frame(win, bg=T["field"], highlightthickness=0)
    frame.pack(fill="both", expand=True, padx=22, pady=(4, 8))
    scroll = tk.Scrollbar(frame)
    scroll.pack(side="right", fill="y")
    text = tk.Text(frame, width=52, height=10, wrap="word", bd=0, relief="flat",
                   bg=T["field"], fg=T["text"], font=(_MONO_FONT, 10),
                   padx=10, pady=8, yscrollcommand=scroll.set)
    text.pack(side="left", fill="both", expand=True)
    scroll.config(command=text.yview)
    text.insert("1.0", notes or "No changelog provided.")
    text.config(state="disabled")

    tk.Label(win, text="The app will close while a short installer runs, then "
             "reopen.", bg=T["bg"], fg=T["muted"], font=(_MONO_FONT, 9),
             justify="left").pack(anchor="w", padx=22, pady=(0, 8))

    res = {"ok": False}
    bf = tk.Frame(win, bg=T["bg"])
    bf.pack(anchor="e", padx=22, pady=(0, 18))

    def ok():
        res["ok"] = True
        win.destroy()

    tk.Button(bf, text="LATER", command=win.destroy, relief="flat", bg=T["field"],
              fg=T["muted"], activebackground=T["panel"], activeforeground=T["text"],
              font=(_DISPLAY_FONT, 11, "bold"), width=10,
              cursor="hand2").pack(side="left", padx=(0, 10))
    tk.Button(bf, text="UPDATE", command=ok, relief="flat", bg=T["green"], fg=T["bg"],
              activebackground=T["accent"], activeforeground=T["bg"],
              font=(_DISPLAY_FONT, 11, "bold"), width=10,
              cursor="hand2").pack(side="left")

    win.update_idletasks()
    ww, wh = win.winfo_width(), win.winfo_height()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 3}")
    win.lift()
    win.focus_force()
    win.grab_set()
    root.wait_window(win)
    return res["ok"]


# --------------------------------------------------------------------------- #
# Run modes
# --------------------------------------------------------------------------- #
def run_overlay(cfg, config_path, debug=False):
    """Continuously read the capture box and float the mineral name above it."""
    import tkinter as tk
    import mss

    snip_spec = cfg.get("hotkey_snip") or "ctrl+s"
    if not parse_hotkey(snip_spec)[1]:
        sys.exit("Invalid hotkey in config.json (e.g. 'ctrl+s').")

    ocr = WindowsOCR()
    sct = mss.MSS()
    root = tk.Tk()
    overlay = Overlay(root, cfg["match_tolerance"], cfg.get("font", "Cascadia Mono"))

    def grab(region):
        from PIL import Image
        shot = sct.grab({"left": region["left"], "top": region["top"],
                         "width": region["width"], "height": region["height"]})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    events = queue.Queue()
    # Always-on background reader; capture the area with the snip hotkey.
    state = {"shown": None, "pending": None, "pcount": 0, "empty": 0,
             "printed": "__init__", "hotkeys": None, "tray": None,
             "update_available": None, "settings_win": None}

    def build_bindings():
        spec = cfg.get("hotkey_snip") or "ctrl+s"
        mods, vk = parse_hotkey(spec)
        return [(SNIP_ID, mods, vk, spec)] if vk else []

    def start_hotkeys():
        if state["hotkeys"]:
            state["hotkeys"].stop()
        hk = HotkeyThread(build_bindings(), on_fire=events.put)
        hk.start()
        state["hotkeys"] = hk

    def apply_settings(newcfg):
        start_hotkeys()                       # re-register with the new keys
        overlay.set_font(newcfg.get("font", "Cascadia Mono"))
        overlay.tolerance = newcfg.get("match_tolerance", 40)
        state["shown"] = None                 # force a redraw with the new font
        print("Settings applied.")

    start_hotkeys()

    def in_game():
        # Only run over Star Citizen, not over screenshots / other windows.
        gp = (cfg.get("game_process") or "").strip().lower()
        if not gp:
            return True                       # restriction disabled
        fg = foreground_exe()
        return fg == gp or fg == ""           # fail open if we can't tell

    def detect():
        try:
            if cfg.get("region") and in_game():
                region = cfg["region"]
                pil = grab(region)
                if debug:
                    pil.save(os.path.join(DATA_DIR, "_last_capture.png"))
                value, _ = read_value(ocr, pil, cfg["upscale"])
                if debug and value != state["printed"]:
                    print(f"read -> {value}", file=sys.stderr)
                    state["printed"] = value
                if value is None:
                    state["pending"], state["pcount"] = None, 0
                    state["empty"] += 1
                    # Keep the label up for linger_ms after the number vanishes —
                    # SC clears a mineral's signature a second or two after the
                    # scan ping, but you still want to read what it was.
                    linger_reads = max(1, round(cfg["linger_ms"] / cfg["poll_ms"]))
                    if state["empty"] >= linger_reads and state["shown"] is not None:
                        overlay.hide()
                        state["shown"] = None
                else:
                    state["empty"] = 0
                    if value == state["pending"]:
                        state["pcount"] += 1
                    else:
                        state["pending"], state["pcount"] = value, 1
                    if state["pcount"] >= cfg["confirm_reads"] and value != state["shown"]:
                        overlay.show_value(value, region)
                        state["shown"] = value
                overlay._reassert_topmost()  # keep fighting to stay above the game
            else:
                # No box, or the game isn't focused — clear the label and reset
                # so detection starts fresh when you return to Star Citizen.
                if state["shown"] is not None:
                    overlay.hide()
                    state["shown"] = None
                state["pending"], state["pcount"], state["empty"] = None, 0, 0
        except Exception as e:
            print("detect error:", e, file=sys.stderr)
        root.after(cfg["poll_ms"], detect)

    def do_snip():
        region = snip_region(root)
        if region:
            cfg["region"] = region
            save_config(config_path, cfg)
            state["shown"] = None
            overlay.show_message("Box set", region, "#5af2a3")
            root.after(1600, lambda: overlay.hide() if state["shown"] is None else None)
            print(f"Capture box set: {region}")

    def do_quit():
        if state["tray"]:
            try:
                state["tray"].stop()
            except Exception:
                pass
        try:
            root.destroy()
        except Exception:
            pass

    def startup_update_check():
        """Quietly check for a newer release at launch; if found, remember it
        and flash a passive notice (the tray menu does the actual update)."""
        def worker():
            try:
                tag, url, _ = updater.check_latest()
            except Exception:
                return
            if url and updater.is_newer(tag, APP_VERSION):
                state["update_available"] = tag
                # Surface it off the overlay: the app window shows the notice,
                # and the tray tooltip hints at it on hover.
                if state["tray"] is not None:
                    try:
                        state["tray"].title = f"{APP_NAME} — update available ({tag})"
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    def check_updates():
        """Check GitHub for a newer release; offer to download + install it."""
        def worker():
            try:
                tag, url, notes = updater.check_latest()
            except Exception as e:
                root.after(0, lambda e=e: themed_dialog(
                    root, "Update", f"Couldn't check for updates.\n\n{e}"))
                return
            if not url or not updater.is_newer(tag, APP_VERSION):
                root.after(0, lambda: themed_dialog(
                    root, "Up to date",
                    f"You're running the latest version (v{APP_VERSION})."))
                return

            def prompt():
                if update_dialog(root, APP_VERSION, tag, notes):
                    install_update(tag, url)
            root.after(0, prompt)

        threading.Thread(target=worker, daemon=True).start()

    def install_update(tag, url):
        def worker():
            try:
                path = updater.download(url, tag)
                updater.run_installer(path)
            except Exception as e:
                root.after(0, lambda e=e: themed_dialog(
                    root, "Update failed",
                    f"Could not download or start the update.\n\n{e}"))
                return
            # Quit so the installer can replace files; it relaunches the app.
            root.after(500, do_quit)

        threading.Thread(target=worker, daemon=True).start()

    def pump():
        try:
            hid = events.get_nowait()
        except queue.Empty:
            hid = None
        if hid == SNIP_ID:
            do_snip()
        elif hid == SETTINGS_ID:
            w = state.get("settings_win")
            if w is not None and w.winfo_exists():
                w.lift()
                w.focus_force()
            else:
                state["settings_win"] = open_settings(
                    root, cfg, config_path, apply_settings,
                    state["update_available"], lambda: events.put(UPDATE_ID))
        elif hid == UPDATE_ID:
            check_updates()
        elif hid == QUIT_ID:
            print("Quitting.")
            do_quit()
            return
        root.after(60, pump)

    def setup_tray():
        try:
            import pystray
            from pystray import Menu, MenuItem as Item
        except Exception as e:
            print("Tray unavailable:", e, file=sys.stderr)
            return None
        menu = Menu(
            # default=True -> also triggered by double-clicking the tray icon.
            Item("Show App", lambda *a: events.put(SETTINGS_ID), default=True),
            Menu.SEPARATOR,
            Item("Quit", lambda *a: events.put(QUIT_ID)),
        )
        icon = pystray.Icon(APP_SLUG, tray_image(), APP_NAME, menu)
        threading.Thread(target=icon.run, daemon=True).start()
        return icon

    state["tray"] = setup_tray()

    # No startup overlay flash — running status is shown in the app window.
    root.after(cfg["poll_ms"], detect)
    root.after(60, pump)
    root.after(2500, startup_update_check)
    print(f"Running in background.  {snip_spec.upper()} capture area  ·  "
          f"tray icon: Show App / Quit")
    if not cfg.get("region"):
        print(f"No capture box yet — press {cfg['hotkey_snip'].upper()} "
              f"and draw one over the signature number.")
    try:
        root.mainloop()
    finally:
        if state["hotkeys"]:
            state["hotkeys"].stop()
        if state["tray"]:
            try:
                state["tray"].stop()
            except Exception:
                pass


def run_calibrate(cfg, config_path):
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    region = snip_region(root)
    if region:
        cfg["region"] = region
        save_config(config_path, cfg)
        print(f"Saved region {region} to {config_path}")
        print("Verify with:  python overlay.py --probe")
    else:
        print("Cancelled — nothing saved.")
    root.destroy()


def run_probe(cfg):
    if not cfg.get("region"):
        sys.exit("No capture box yet. Run:  python overlay.py --calibrate")
    pil = grab_region(cfg["region"])
    out = os.path.join(DATA_DIR, "_probe.png")
    pil.save(out)
    value, text = read_value(WindowsOCR(), pil, cfg["upscale"])
    print(f"region       : {cfg['region']}")
    print(f"saved        : {out}  ({pil.width}x{pil.height} px)")
    print(f"OCR value    : {value}  (text {text!r})")
    if value is not None:
        entry = minerals.best_match(value)
        print(f"best match   : {minerals.label_for(entry)} (delta {entry['delta']:+d})")
    print("\n→ Open _probe.png. If it isn't the number, re-run --calibrate.")


def run_test_image(path, cfg):
    from PIL import Image

    if not os.path.exists(path):
        sys.exit(f"No such file: {path}")
    pil = Image.open(path).convert("RGB")
    value, text = read_value(WindowsOCR(), pil, cfg["upscale"])
    print(f"OCR value : {value}  (text {text!r})")
    if value is not None:
        entry = minerals.best_match(value)
        print(f"best match: {minerals.label_for(entry)} (delta {entry['delta']:+d})")


# --------------------------------------------------------------------------- #
def main():
    setup_logging()  # must run before any print() in a frozen windowed build
    ap = argparse.ArgumentParser(description="Star Citizen scan-signature reader.")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("--calibrate", action="store_true",
                    help="Draw the capture box on a screenshot, save it, and exit.")
    ap.add_argument("--probe", action="store_true",
                    help="Capture the saved box to _probe.png and OCR it once.")
    ap.add_argument("--test-image", metavar="PATH",
                    help="Run OCR against a saved screenshot.")
    ap.add_argument("--check-update", action="store_true",
                    help="Print whether a newer release is available, then exit.")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config.json.")
    ap.add_argument("--debug", action="store_true",
                    help="Print OCR reads and save the last capture.")
    args = ap.parse_args()

    awareness = set_dpi_aware()
    if args.probe or args.debug:
        print(f"DPI awareness: {awareness}")

    if args.check_update:
        try:
            tag, url, _ = updater.check_latest()
            avail = bool(url) and updater.is_newer(tag, APP_VERSION)
            print(f"current v{APP_VERSION}  ·  latest {tag}  ·  "
                  f"{'update available' if avail else 'up to date'}")
        except Exception as e:
            print("update check failed:", e)
        return

    cfg = load_config(args.config)
    if args.calibrate:
        run_calibrate(cfg, args.config)
    elif args.probe:
        run_probe(cfg)
    elif args.test_image:
        run_test_image(args.test_image, cfg)
    else:
        # Elevation is only needed if Star Citizen itself runs as admin (so the
        # keyboard hook can fire over it). Off by default — opt in via config.
        if cfg.get("elevate") and not ensure_elevated():
            return  # an elevated copy is taking over
        run_overlay(cfg, args.config, debug=args.debug)


if __name__ == "__main__":
    main()
