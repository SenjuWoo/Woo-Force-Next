"""English startup splash: the pack's keyboard slide plus an English controls legend.

The MUGEN pack's logo storyboard (data/logo.def, Logo.sff) is four slides; three are Portuguese text
("Teclas do Jogo", "Especiais", ...). Slide 0,0 is a language-neutral keyboard diagram ("Player 1" /
"Player 2"). This keeps that slide, adds an English legend matching save/config.ini (the MUGEN key layout,
ported 2026-10-10) and Ikemen's debug hotkeys (external/script/debug.lua), and writes data/Logo_en.sff +
data/logo_en.def. setup_ikemen.py points the motif's logo.storyboard at it. Originals stay untouched.

usage: python logo_en.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import char_cache  # noqa: E402  (SFF v2 reader/writer)

FONT = ROOT / "font" / "Open_Sans"
LINES = [
    ("PLAYER 1", (70, 110, 255), "Move  W A S D     Attack  P  [  ]  ;  '  \\     Start  -"),
    ("PLAYER 2", (235, 40, 40), "Move  Arrow keys     Attack  U  I  O  J  K  L     Start  Enter"),
]
HOTKEYS = ("Esc pause   F1 KO player 2   F3 max power   F4 restart round   Space full life + power   "
           "F7 brain overlay   F8 brain on/off")


def build():
    b = (ROOT / "data" / "Logo.sff").read_bytes()
    slide = char_cache.read_v2(b, {(0, 0)})[0][(0, 0)]
    img = slide.img.convert("RGBA")
    band = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.rounded_rectangle((24, 590, img.width - 24, 708), radius=14, fill=(5, 10, 30, 205))
    img = Image.alpha_composite(img, band)
    d = ImageDraw.Draw(img)
    label = ImageFont.truetype(str(FONT / "OpenSans-ExtraBold.ttf"), 28)
    text = ImageFont.truetype(str(FONT / "OpenSans-Bold.ttf"), 26)
    small = ImageFont.truetype(str(FONT / "OpenSans-SemiBold.ttf"), 18)
    for i, (name, col, line) in enumerate(LINES):
        y = 600 + 36 * i
        d.text((50, y), name, font=label, fill=col)
        d.text((210, y + 2), line, font=text, fill=(240, 240, 240))
    d.text((50, 674), HOTKEYS, font=small, fill=(170, 185, 210))
    out = char_cache.Spr(img, slide.ax, slide.ay)
    char_cache.write_sff(ROOT / "data" / "Logo_en.sff", {(0, 0): out}, [], None)
    (ROOT / "data" / "logo_en.def").write_text(
        "; English controls splash (woo/tools/logo_en.py) - replaces the Portuguese slides of logo.def\n"
        "[Info]\nname = \"Logo (English)\"\nlocalcoord = 1280,720\n\n"
        "[SceneDef]\nspr = Logo_en.sff\nstartscene = 0\n\n"
        "[Scene 0]\nfadein.time = 30\nfadeout.time = 30\nend.time = 360\nbg.name = BG0\n\n"
        "[BG0Def]\n\n[BG0]\ntype = normal\nspriteno = 0,0\nstart = 0,0\n", encoding="utf-8")
    return img


if __name__ == "__main__":
    build().convert("RGB").save(ROOT / "woo" / "test" / "logo_en_preview.png")
    print("data/Logo_en.sff + data/logo_en.def written; preview woo/test/logo_en_preview.png")
