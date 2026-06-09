"""Generate signature_overlay.ico — a hexagon mining node with a scan dot."""
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # supersample, then downscale into the .ico

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Dark rounded tile with a faint blue edge.
pad = int(S * 0.05)
d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.20),
                    fill=(16, 26, 40, 255), outline=(78, 163, 255, 110),
                    width=int(S * 0.012))

# Flat-top hexagon (mining node) in accent blue.
cx, cy, r = S / 2, S / 2, S * 0.30
pts = [(cx + r * math.cos(math.radians(60 * i - 30)),
        cy + r * math.sin(math.radians(60 * i - 30))) for i in range(6)]
d.line(pts + [pts[0]], fill=(78, 163, 255, 255), width=int(S * 0.045), joint="curve")

# Center scan dot in match-green.
rr = S * 0.085
d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(90, 242, 163, 255))

base = img.resize((256, 256), Image.LANCZOS)
out = os.path.join(HERE, "signature_overlay.ico")
base.save(out, format="ICO", sizes=[(256, 256), (128, 128), (64, 64),
                                    (48, 48), (32, 32), (16, 16)])
print("saved", out)
