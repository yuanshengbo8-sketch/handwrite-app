r"""Render plain text or a Word .docx as handwritten pages on ruled paper.

Every letter is drawn with its own slight slant, rotation, proportions and gently bent strokes,
and the ink gets uneven pressure, so repeated letters never look stamped.

Usage:
  python handwrite.py homework.docx -o homework.pdf --font fonts/ReenieBeanie.ttf --ink blue
  python handwrite.py notes.txt -o notes.pdf --paper fourline
  python handwrite.py --compare fonts -o font_compare.png

Text files: one paragraph per line, "# " starts a heading, a blank line leaves a row empty,
leading spaces indent the first line, x^2 or x^{10} is a superscript, H_{2}O a subscript,
\hat{x} and \bar{x} put a hat or bar over one character.
Word files: equations, superscript and subscript runs are written out; images are skipped.
"""
import argparse
import math
import random
import re
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

DPI = 200
MM = DPI / 25.4
PAGE_W, PAGE_H = round(210 * MM), round(297 * MM)
SS = 3  # glyphs and pen strokes are drawn at 3x and scaled down for smooth edges

PAPER_BG = (251, 250, 244)
RULE = (152, 186, 222)
MARGIN_RED = (226, 128, 128)
INKS = {
    "blue": (30, 52, 135),
    "black": (34, 34, 40),
    "darkblue": (22, 32, 85),
    "blueblack": (28, 38, 72),
    "gel": (18, 18, 22),
    "red": (186, 42, 42),
    "green": (28, 96, 60),
    "purple": (88, 44, 130),
    "brown": (96, 62, 38),
    "pencil": (92, 92, 98),
}
# Paper stock: base colour, strength of mottling, number of fibre specks per page
TEXTURES = {
    "white": ((254, 254, 253), 0.0, 0),
    "cream": ((251, 250, 244), 0.012, 0),
    "ivory": ((248, 243, 226), 0.018, 0),
    "yellow": ((250, 240, 190), 0.012, 0),
    "aged": ((238, 226, 196), 0.02, 40),
    "parchment": ((232, 214, 176), 0.035, 25),
    "recycled": ((233, 230, 220), 0.015, 350),
    "kraft": ((214, 186, 142), 0.025, 500),
    "gray": ((226, 226, 224), 0.012, 120),
    "bluish": ((238, 243, 250), 0.01, 0),
}
# Paper layouts: left margin (mm), x-height (mm), and how the sheet is ruled
PAPERS = ("lined", "narrow", "wide", "looseleaf", "legal", "grid", "dotted", "fourline", "squared", "plain")
PAPER_SPECS = {
    "lined": (25, 0.36 * 7.1), "narrow": (22, 0.36 * 6.0), "wide": (25, 0.36 * 8.7),
    "looseleaf": (32, 0.36 * 7.1), "legal": (32, 0.36 * 7.1), "grid": (20, 2.9),
    "dotted": (20, 2.9), "fourline": (15, 2.4), "squared": (20, 3.6), "plain": (20, 2.8),
}
ASCII_PUNCT = str.maketrans({
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2013": "-",
    "\u2014": "-", "\u2026": "...", "\u00a0": " ", "\t": " ",
})
# A mark before a character makes it a small raised (superscript) or lowered (subscript) glyph,
# or draws a hat or bar over it.
SUP, SUB, HAT, BAR = "\x01", "\x02", "\x03", "\x04"
MARKS = (SUP, SUB, HAT, BAR)
SLIP = "\x05"  # a word starting with this is a false start: written, then scribbled out
# Stacked objects inside a word: FRAC num SEP den END, or SUM lower SEP upper END
FRAC, SUM, SEP, END = "\x06", "\x07", "\x0e", "\x0f"
SUB_SCALE = 0.72  # size of superscripts and subscripts
# Letters that end with a stroke on the right in a print hand, which may run into the next one
JOIN_FROM = set("adehiklmnut")
SUP_MAP = dict(zip("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207c\u207d\u207e\u207f\u2071",
                   "0123456789+-=()ni"))
SUB_MAP = dict(zip("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u208a\u208b\u208c\u208d\u208e",
                   "0123456789+-=()"))
FALLBACK_FONTS = ("PatrickHand.ttf", "Kalam.ttf")
SYSTEM_FALLBACKS = ("C:/Windows/Fonts/segoepr.ttf",  # Segoe Print has Greek letters
                    "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS: plain but complete
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

SAMPLE = [
    ("p", "Q1. Describe a person who has influenced you.", 0, 0),
    ("p", "My grandmother has influenced me more than anyone else. She taught me how to cook, "
          "how to save money, and why it matters to keep a promise. Even when she was busy, "
          "she always found time to listen to me.", 0, 0),
]


def mark(text, kind):
    return "".join(kind + c if kind and not c.isspace() else c for c in text)


def normalize(text):
    text = text.translate(ASCII_PUNCT)
    return "".join(SUP + SUP_MAP[c] if c in SUP_MAP else SUB + SUB_MAP[c] if c in SUB_MAP else c
                   for c in text)


class Face:
    """A font scaled so its lowercase x is xheight pixels tall, with glyphs cached as 3x masks."""

    def __init__(self, path, xheight):
        self.path = str(path)
        self.cmap = set(TTFont(self.path).getBestCmap())
        probe = ImageFont.truetype(self.path, 200)
        self.em = xheight / -probe.getbbox("x", anchor="ls")[1] * 200
        self.fonts = {}
        self.cache = {}

    def font(self, scale):
        if scale not in self.fonts:
            self.fonts[scale] = ImageFont.truetype(self.path, max(4, round(self.em * scale * SS)))
        return self.fonts[scale]

    def glyph(self, ch, scale=1.0):
        """Returns (mask, origin_x, origin_y, advance, ink_box). The mask and its origin are in
        3x pixels; the advance and ink_box (left, top, right, bottom around the origin) in 1x."""
        key = (ch, scale)
        if key not in self.cache:
            font = self.font(scale)
            l, t, r, b = font.getbbox(ch, anchor="ls")
            mask = None
            if r > l and b > t:
                mask = Image.new("L", (r - l + 4, b - t + 4), 0)
                ImageDraw.Draw(mask).text((2 - l, 2 - t), ch, font=font, fill=255, anchor="ls")
            self.cache[key] = (mask, 2 - l, 2 - t, font.getlength(ch) / SS,
                               (l / SS, t / SS, r / SS, b / SS))
        return self.cache[key]

    def advance(self, ch, scale=1.0):
        return self.glyph(ch, scale)[3]


class Ink:
    """Collects everything the pen draws on one page as a coverage mask, so the ink texture can be
    applied to all strokes at once."""

    def __init__(self, size):
        self.mask = Image.new("L", size, 0)

    def add(self, alpha, xy):
        box = (xy[0], xy[1], xy[0] + alpha.width, xy[1] + alpha.height)
        self.mask.paste(ImageChops.lighter(self.mask.crop(box), alpha), box)

    def stroke(self, points, width, value):
        """Adds a smooth pen line through points given in 1x pixels."""
        pad = width + 2
        x0, y0 = math.floor(min(p[0] for p in points) - pad), math.floor(min(p[1] for p in points) - pad)
        x1, y1 = math.ceil(max(p[0] for p in points) + pad), math.ceil(max(p[1] for p in points) + pad)
        tile = Image.new("L", ((x1 - x0) * SS, (y1 - y0) * SS), 0)
        ImageDraw.Draw(tile).line([((px - x0) * SS, (py - y0) * SS) for px, py in points],
                                  fill=value, width=round(width * SS), joint="curve")
        self.add(tile.resize((x1 - x0, y1 - y0), Image.Resampling.BOX), (x0, y0))


def overlap(a, ax, ay, b, bx, by, grow=1):
    """How much two masks touch when their top-left corners are at (ax, ay) and (bx, by).

    Both are thickened by grow pixels first, so strokes that cross or nearly touch count too.
    """
    x0, y0 = max(ax, bx) - grow, max(ay, by) - grow
    x1, y1 = min(ax + a.width, bx + b.width) + grow, min(ay + a.height, by + b.height) + grow
    if x0 >= x1 or y0 >= y1:
        return 0
    thicken = ImageFilter.MaxFilter(2 * grow + 1)
    shared = ImageChops.multiply(a.crop((x0 - ax, y0 - ay, x1 - ax, y1 - ay)).filter(thicken),
                                 b.crop((x0 - bx, y0 - by, x1 - bx, y1 - by)).filter(thicken))
    return sum(i * n for i, n in enumerate(shared.histogram()))


_designed = {}


def designed_overlap(face_a, ch_a, scale_a, face_b, ch_b, scale_b, gap=0):
    """How much two letters touch in the font itself when set side by side (1x pixels)."""
    key = (id(face_a), ch_a, scale_a, id(face_b), ch_b, scale_b, gap)
    if key not in _designed:
        mask_a, ox_a, oy_a, adv_a, _ = face_a.glyph(ch_a, scale_a)
        mask_b, ox_b, oy_b, _, _ = face_b.glyph(ch_b, scale_b)
        if mask_a is None or mask_b is None:
            _designed[key] = 0
        else:
            shrink = lambda m: m.resize((max(1, round(m.width / SS)), max(1, round(m.height / SS))),
                                        Image.Resampling.BOX)
            _designed[key] = overlap(shrink(mask_a), round(-ox_a / SS), round(-oy_a / SS),
                                     shrink(mask_b), round(adv_a + gap - ox_b / SS), round(-oy_b / SS))
    return _designed[key]


class Writer:
    """Writes words letter by letter. Each letter gets its own size, proportions, slant, rotation,
    bent strokes, position and pen pressure; the slant also drifts slowly from word to word.

    Characters missing from the main font are drawn with the first fallback font that has them.
    """

    def __init__(self, font_path, xheight, rng, mess=1.0, fallbacks=(), habit_seed=None,
                 slant=0.0, pressure=0, letter_gap=1.0, word_gap=1.0):
        self.faces = [Face(p, xheight) for p in (font_path, *fallbacks)]
        self.space = self.faces[0].advance(" ")
        self.xh, self.rng, self.mess = xheight, rng, mess
        self.slant = 0.0
        # Standing choices of this writer: overall lean (shear, + leans right), how hard they
        # press (-1 light, 0 normal, 1 heavy) and how tightly they pack letters and words.
        self.slant_bias, self.pressure = slant, pressure
        self.letter_gap, self.word_gap = letter_gap, word_gap
        # This writer's fixed habits: every character gets its own lean, size, height and tilt
        # that it keeps through the whole document, with only small variation on top.
        self._habit_rng = random.Random(rng.random() if habit_seed is None else habit_seed)
        self.habits = {}

    def habit(self, ch):
        if ch not in self.habits:
            h = self._habit_rng
            self.habits[ch] = (h.gauss(0, 0.045), h.gauss(0, 0.03), h.gauss(0, 0.04), h.gauss(0, 1.3))
        return self.habits[ch]

    def face_for(self, ch):
        return next((f for f in self.faces if ord(ch) in f.cmap), self.faces[0])

    def glyphs(self, word):
        """Yields items: ("g", char, face, shift, accent, gap) for a letter, where shift is 1 for
        superscript and -1 for subscript, accent is HAT, BAR or None and gap is extra space before
        a superscript or subscript run; ("frac", num, den) and ("sum", lower, upper) for
        stacked objects."""
        word = word.lstrip(SLIP)
        i, prev_shift = 0, 0
        while i < len(word):
            if word[i] in (FRAC, SUM) and END in word[i:]:
                end = word.index(END, i)
                parts = word[i + 1:end].split(SEP, 1)
                yield ("frac" if word[i] == FRAC else "sum", parts[0], parts[1] if len(parts) > 1 else "")
                i, prev_shift = end + 1, 0
                continue
            shift, accent = 0, None
            while word[i] in MARKS and i + 1 < len(word):
                if word[i] in (SUP, SUB):
                    shift = 1 if word[i] == SUP else -1
                else:
                    accent = word[i]
                i += 1
            ch = word[i]
            gap = 0.22 * self.xh if shift and not prev_shift and i > 1 else 0
            yield "g", ch, self.face_for(ch), shift, accent, gap
            prev_shift = shift
            i += 1

    def missing(self, text):
        return {c for c in text if c not in MARKS + (SLIP, FRAC, SUM, SEP, END) and not c.isspace()
                and all(ord(c) not in f.cmap for f in self.faces)}

    def item_width(self, item):
        xh = self.xh
        if item[0] == "g":
            _, ch, face, shift, _, gap = item
            return face.advance(ch, SUB_SCALE if shift else 1.0) + gap
        if item[0] == "frac":
            return 0.8 * max(self.width(item[1]), self.width(item[2])) + 0.5 * xh
        sigma = self.face_for("Σ").advance("Σ", 1.3)
        return max(sigma, 0.6 * self.width(item[1]), 0.6 * self.width(item[2])) + 0.3 * xh

    def width(self, word):
        return sum(self.item_width(it) for it in self.glyphs(word))

    def wrap(self, words, first_width, width):
        """Breaks words into lines. A line may run a little past the margin: the writer then
        squeezes the last words in rather than starting a new line."""
        lines, cur, used = [], [], 0
        limit = first_width + self.rng.uniform(-0.8, 2.5) * self.xh
        for word in words:
            w = self.width(word)
            grown = w if not cur else used + self.space * 1.1 + w
            if cur and grown > limit:
                lines.append(cur)
                cur, used = [word], w
                limit = width + self.rng.uniform(-0.8, 2.5) * self.xh
            else:
                cur.append(word)
                used = grown
        if cur:
            lines.append(cur)
        return lines

    def _stamp(self, ink, face, ch, scale, x, baseline, shear, pressure, guard=None,
               squeeze=1.0, weight=0, lazy=0.0):
        """Draws one letter with its own proportions, slant, rotation and slightly bent strokes.

        guard is (previous letter's mask, its x, its y, ink overlap allowed): the letter is moved
        right until it overlaps the previous one no more than that, so letters never run into each
        other more than the font itself has them. lazy (0..1) shrinks and loosens the letter, for
        the tail of a long word. Returns (advance, top of the ink, (mask, x, y) as drawn,
        how far the letter was moved).
        """
        mask, ox, oy, adv, (l, t, r, b) = face.glyph(ch, scale)
        if mask is None:
            return adv, baseline, None, 0
        rng, m = self.rng, self.mess
        em = face.em * scale
        h_shear, h_size, _, h_rot = self.habit(ch)
        shear += h_shear + lazy * 0.5
        size = (1 + h_size) * (1 - lazy) * (1 + rng.gauss(0, 0.015) * m)
        if ch.isupper():
            size *= 1.03  # capitals tend to come out a touch bigger
        elif ch.isdigit():
            size *= 0.95  # digits a touch smaller and tighter
        sx = size * squeeze * (1 + rng.gauss(0, 0.03) * m)
        sy = size * (1 + rng.gauss(0, 0.02) * m)
        rot = math.radians(h_rot + rng.gauss(0, 1.0) * (1 + 2 * lazy) * m)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        # Smooth waves across the letter: the first two bend strokes sideways, the last two up/down.
        # Each wave stretches or squeezes any part of the letter by at most 10%, so strokes bend
        # but never fold into blobs.
        waves = []
        for _ in range(4):
            cycles, angle = rng.uniform(0.5, 1.2), rng.uniform(0, 2 * math.pi)
            k = 2 * math.pi * cycles / em
            strain = max(-0.1, min(0.1, rng.gauss(0, 0.04) * (1 + 2 * lazy) * m))
            waves.append((strain / k, k * math.cos(angle), k * math.sin(angle), rng.uniform(0, 2 * math.pi)))
        cx, cy = adv * sx / 2, -0.5 * self.xh * scale * sy
        # the bend is zero at the letter's middle, so it changes the shape without moving the letter
        at_middle = [a * math.sin(kx * adv / 2 - ky * 0.5 * self.xh * scale + ph) for a, kx, ky, ph in waves]

        def forward(gx, gy):
            # scale about the origin, then slant and rotate about the middle of the letter
            qx, qy = gx * sx - cx, gy * sy - cy
            qx -= shear * qy
            return cx + cos_r * qx - sin_r * qy, cy + sin_r * qx + cos_r * qy

        def inverse(px, py):
            qx, qy = px - cx, py - cy
            qx, qy = cos_r * qx + sin_r * qy, cos_r * qy - sin_r * qx
            qx += shear * qy
            gx, gy = (qx + cx) / sx, (qy + cy) / sy
            d = [a * math.sin(kx * gx + ky * gy + ph) - c for (a, kx, ky, ph), c in zip(waves, at_middle)]
            return gx - d[0] - d[1], gy - d[2] - d[3]

        mw, mh = mask.size
        corners = [forward((u - ox) / SS, (v - oy) / SS) for u, v in ((0, 0), (mw, 0), (0, mh), (mw, mh))]
        pad = 2 * sum(abs(w[0]) for w in waves) + 2
        x0 = math.floor(x + min(p[0] for p in corners) - pad)
        x1 = math.ceil(x + max(p[0] for p in corners) + pad)
        y0 = math.floor(baseline + min(p[1] for p in corners) - pad)
        y1 = math.ceil(baseline + max(p[1] for p in corners) + pad)
        tw, th, n = (x1 - x0) * SS, (y1 - y0) * SS, 6
        xs = [round(tw * i / n) for i in range(n + 1)]
        ys = [round(th * j / n) for j in range(n + 1)]
        src = {}
        for i, tx in enumerate(xs):
            for j, ty in enumerate(ys):
                gx, gy = inverse(x0 + tx / SS - x, y0 + ty / SS - baseline)
                src[i, j] = (ox + gx * SS, oy + gy * SS)
        mesh = [((xs[i], ys[j], xs[i + 1], ys[j + 1]),
                 (*src[i, j], *src[i, j + 1], *src[i + 1, j + 1], *src[i + 1, j]))
                for i in range(n) for j in range(n)]
        warped = mask.transform((tw, th), Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
        if weight > 0:  # pressing harder: strokes a third of a pixel thicker
            warped = ImageChops.blend(warped, warped.filter(ImageFilter.MaxFilter(3)), 0.5)
        elif weight < 0:  # lighter touch: a third of a pixel thinner
            warped = ImageChops.blend(warped, warped.filter(ImageFilter.MinFilter(3)), 0.5)
        alpha = warped.resize((x1 - x0, y1 - y0), Image.Resampling.BOX)
        moved = 0
        if guard is not None:
            prev_mask, px, py, allowed = guard
            while moved < 0.4 * self.xh and overlap(prev_mask, px, py, alpha, x0 + moved, y0) > allowed:
                moved += 1
        ink.add(alpha.point([round(v * pressure) for v in range(256)]), (x0 + moved, y0))
        return adv * sx, baseline + min(forward(l, t)[1], forward(r, t)[1]), (alpha, x0 + moved, y0), moved

    def write_line(self, ink, words, x, baseline, right=None):
        """Writes one row. If the words do not fit before `right`, the writer squeezes them in
        the way people do, writing the later part of the line narrower rather than wrapping."""
        rng, m, xh = self.rng, self.mess, self.xh
        start, slope, drift = x, rng.gauss(0, 0.0018) * m, 0.0
        wave_k, wave_ph = rng.uniform(0.3, 0.8) * 2 * math.pi / (40 * xh), rng.uniform(0, 2 * math.pi)
        natural = sum(self.width(w) for w in words) + self.space * 1.1 * (len(words) - 1)
        room = (right - x + 0.6 * xh) if right is not None else natural
        squeeze = max(0.84, min(1.0, room / natural)) if natural > room else 1.0
        for word in words:
            drift = max(-0.15 * xh, min(0.15 * xh, drift + rng.gauss(0, 0.04 * xh) * m))
            self.slant = max(-0.12, min(0.12, self.slant * 0.9 + rng.gauss(0, 0.02) * m))
            word_pressure = rng.uniform(0.9, 1.0) * (0.86 if self.pressure < 0 else 1.0)
            # pen pressure changes from word to word, around this writer's usual pressure
            weight = rng.choice({-1: (0, 0, -1, -1, -1), 0: (0, 0, 0, 0, 1, -1), 1: (0, 0, 1, 1, 1)}[self.pressure])
            # the squeeze only sets in over the second half of the line
            here = (x - start) / max(1, room)
            word_squeeze = 1.0 if here < 0.45 else 1 - (1 - squeeze) * min(1, (here - 0.45) / 0.3)
            items = list(self.glyphs(word))
            letters = sum(1 for it in items if it[0] == "g" and it[1].isalpha())
            pos, prev, bob, joined = x, None, 0.0, False  # prev: (char, face, scale, mask, x, y)
            for k, item in enumerate(items):
                if item[0] != "g":
                    pos += self._object(ink, item, pos, baseline + drift + slope * (pos - start))
                    pos += 0.3 * xh  # a stacked object needs air before the next character
                    prev = None
                    continue
                _, ch, face, shift, accent, gap = item
                scale = SUB_SCALE if shift else 1.0
                pos += gap
                raise_by = {1: 0.7 * xh, -1: -0.35 * xh, 0: 0}[shift]
                # a long word gets lazier towards its end: smaller, more slanted, looser letters
                lazy = min(0.18, 0.03 * (k - 3)) * m if letters >= 6 and k >= 4 else 0.0
                # letters bob up and down smoothly, so neighbours sit at similar heights
                bob = 0.6 * bob + rng.gauss(0, 0.025 * xh) * m
                cx = pos + rng.gauss(0, 0.02 * xh) * m
                cy = (baseline + drift + slope * (cx - start) - raise_by + bob
                      + self.habit(ch)[2] * xh * scale
                      + 0.06 * xh * m * math.sin(wave_k * (cx - start) + wave_ph))
                guard = None
                if prev:
                    allowed = 1.1 * designed_overlap(prev[1], prev[0], prev[2], face, ch, scale, gap) + 100
                    if joined:
                        allowed += 14 * 255  # the two letters are meant to touch
                    guard = (*prev[3:], allowed)
                advance, top, drawn, moved = self._stamp(ink, face, ch, scale, cx, cy,
                                                         self.slant_bias + self.slant + rng.gauss(0, 0.03) * m,
                                                         word_pressure * rng.uniform(0.93, 1.0), guard,
                                                         word_squeeze, weight, lazy)
                if accent:
                    self._accent(ink, accent, cx + moved, cx + moved + advance, top)
                prev = (ch, face, scale, *drawn) if drawn else None
                # letters sit a little closer or further apart each time; some pairs run together
                nxt = items[k + 1] if k + 1 < len(items) else None
                joined = (nxt is not None and nxt[0] == "g" and not shift and not nxt[3]
                          and ch in JOIN_FROM and nxt[1].isalpha() and nxt[1].islower()
                          and rng.random() < 0.55)
                pos += moved + advance * self.letter_gap * (rng.uniform(0.8, 0.9) if joined
                                                            else rng.uniform(0.94, 1.08))
            if word.startswith(SLIP):
                self._scribble(ink, x, pos, baseline + drift)
            after = 1.0 + (0.35 if word[-1:] in ",.;:" else 0.0)  # a breath after punctuation
            x = pos + self.space * self.word_gap * word_squeeze * rng.uniform(0.75, 1.25) * after
        return x - self.space

    def _run(self, ink, text, x, baseline, scale):
        """Writes a short plain run (a numerator, a limit) at the given size; returns its width."""
        rng, m = self.rng, self.mess
        pos, prev = x, None
        for item in self.glyphs(text):
            if item[0] != "g":
                pos += self._object(ink, item, pos, baseline)
                prev = None
                continue
            _, ch, face, shift, accent, gap = item
            sc = scale * (SUB_SCALE if shift else 1.0)
            pos += gap * scale
            raise_by = {1: 0.7, -1: -0.35, 0: 0}[shift] * self.xh * scale
            guard = None
            if prev:
                guard = (*prev[3:], 1.1 * designed_overlap(prev[1], prev[0], prev[2], face, ch, sc, gap) + 100)
            advance, top, drawn, moved = self._stamp(ink, face, ch, sc, pos + rng.gauss(0, 0.02 * self.xh) * m,
                                                     baseline - raise_by + rng.gauss(0, 0.03 * self.xh) * m,
                                                     self.slant_bias + self.slant, rng.uniform(0.9, 1.0), guard)
            if accent:
                self._accent(ink, accent, pos + moved, pos + moved + advance, top)
            prev = (ch, face, sc, *drawn) if drawn else None
            pos += moved + advance * rng.uniform(0.96, 1.06)
        return pos - x

    def _object(self, ink, item, x, baseline):
        """Draws a stacked fraction or a sum sign with its limits; returns the width used."""
        rng, xh = self.rng, self.xh
        if item[0] == "frac":
            num, den = item[1], item[2]
            wn, wd = 0.75 * self.width(num), 0.75 * self.width(den)
            w = max(wn, wd) + 0.5 * xh
            self._run(ink, num, x + (w - wn) / 2, baseline - 0.9 * xh, 0.75)
            self._run(ink, den, x + (w - wd) / 2, baseline + 0.8 * xh, 0.75)
            y = baseline - 0.4 * xh
            ink.stroke([(x + rng.gauss(0, 0.1 * xh), y + rng.gauss(0, 0.06 * xh)),
                        (x + w + rng.gauss(0, 0.1 * xh), y + rng.gauss(0, 0.06 * xh))],
                       2.5, round(255 * rng.uniform(0.85, 1.0)))
            return w
        lower, upper = item[1], item[2]
        face = self.face_for("Σ")
        ws = face.advance("Σ", 1.3)
        wl, wu = 0.6 * self.width(lower), 0.6 * self.width(upper)
        w = max(ws, wl, wu) + 0.3 * xh
        cx = x + w / 2
        self._run(ink, "Σ", cx - ws / 2, baseline + 0.3 * xh, 1.3)
        self._run(ink, upper, cx - wu / 2, baseline - 1.7 * xh, 0.6)
        self._run(ink, lower, cx - wl / 2, baseline + 1.15 * xh, 0.6)
        return w

    def _scribble(self, ink, x0, x1, baseline):
        """Crosses out a false start with two quick back-and-forth strokes."""
        rng, xh = self.rng, self.xh
        for k in range(2):
            y = baseline - xh * (0.55 + 0.25 * k) + rng.gauss(0, 0.08 * xh)
            pts = [(x0 - 0.2 * xh, y)]
            for i in range(1, 5):
                pts.append((x0 + (x1 - x0) * i / 4 + rng.gauss(0, 0.1 * xh),
                            y + rng.gauss(0, 0.12 * xh)))
            ink.stroke(pts, 2.5, round(255 * rng.uniform(0.85, 1.0)))

    def _accent(self, ink, accent, x0, x1, glyph_top):
        """Draws a hat or bar stroke just above a letter whose ink starts at glyph_top."""
        rng, xh = self.rng, self.xh
        y = glyph_top - 0.25 * xh + rng.gauss(0, 0.03 * xh)
        a, b = x0 + 0.15 * (x1 - x0), x1 - 0.1 * (x1 - x0)
        if accent == HAT:
            pts = [(a, y + 0.3 * xh), ((a + b) / 2, y - 0.05 * xh), (b, y + 0.3 * xh)]
        else:
            pts = [(a, y + rng.gauss(0, 0.03 * xh)), (b, y + rng.gauss(0, 0.03 * xh))]
        ink.stroke(pts, 2.5, round(255 * rng.uniform(0.85, 1.0)))

    def underline(self, ink, x0, x1, y):
        pts, x, dy = [], x0, 0.0
        while x < x1:
            pts.append((x, y + dy))
            x += 30
            dy = max(-0.12 * self.xh, min(0.12 * self.xh, dy + self.rng.gauss(0, 0.04 * self.xh)))
        pts.append((x1, y + dy))
        ink.stroke(pts, 2.5, round(255 * self.rng.uniform(0.85, 1.0)))


def fallbacks_for(font):
    folder = Path(font).parent
    local = [folder / name for name in FALLBACK_FONTS
             if (folder / name).exists() and (folder / name).resolve() != Path(font).resolve()]
    return local + [Path(p) for p in SYSTEM_FALLBACKS if Path(p).exists()]


def paper_spec(kind):
    """Returns (xheight, text_left, text_right) in pixels for a paper kind."""
    margin, xh = PAPER_SPECS[kind]
    return xh * MM, margin * MM + 3 * MM, PAGE_W - 12 * MM


def paper_stock(texture, rng, size):
    """A blank sheet of the given stock: base colour, soft mottling and, on rough paper, specks."""
    bg, mottle, specks = TEXTURES.get(texture, TEXTURES["cream"])
    img = Image.new("RGB", size, bg)
    if mottle:
        w, h = size
        # two layers of soft clouds, one broad and one finer, so the paper looks fibrous not blotchy
        cloud = Image.new("L", size, 128)
        for cell, amp in ((w // 90 + 2, 1.0), (w // 25 + 2, 0.5)):
            layer = Image.effect_noise((cell, round(cell * h / w)), 40).resize(size, Image.Resampling.BICUBIC)
            layer = layer.filter(ImageFilter.GaussianBlur(3))
            cloud = ImageChops.add(cloud, layer.point(lambda v, a=amp: round(128 + (v - 128) * a)), offset=-128)
        cloud = cloud.point(lambda v: max(0, min(255, round(255 * (1 + mottle * (v - 128) / 40)))))
        img = ImageChops.multiply(img, Image.merge("RGB", [cloud] * 3))
    if specks:
        d = ImageDraw.Draw(img)
        for _ in range(specks):
            x, y = rng.uniform(0, size[0]), rng.uniform(0, size[1])
            r = rng.uniform(0.4, 1.1)
            shade = round(rng.uniform(150, 205))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(shade, shade - 3, shade - 8))
    return img, bg


def make_paper(kind, rng, height=PAGE_H, texture="cream"):
    """Draws a blank sheet and returns it with the baseline y of every writing row."""
    img, _ = paper_stock(texture, rng, (PAGE_W, height))
    d = ImageDraw.Draw(img)
    rows = []
    if kind in ("grid", "squared", "dotted"):
        step = (10 if kind == "squared" else 5) * MM
        grey = (196, 204, 214) if kind != "dotted" else (170, 176, 186)
        xs = []
        x = 10 * MM
        while x < PAGE_W - 8 * MM:
            if kind != "dotted":
                d.line([(round(x), 0), (round(x), height)], fill=grey, width=1)
            xs.append(round(x))
            x += step
        y, n = 12 * MM, 0
        while y < height - 10 * MM:
            if kind == "dotted":
                for gx in xs:
                    d.ellipse([gx - 1.5, round(y) - 1.5, gx + 1.5, round(y) + 1.5], fill=grey)
            else:
                d.line([(0, round(y)), (PAGE_W, round(y))], fill=grey, width=1)
            every = 1 if kind == "squared" else 2
            if n >= every and n % every == 0 and y < height - 14 * MM:
                rows.append(y)
            y, n = y + step, n + 1
    elif kind in ("lined", "narrow", "wide", "looseleaf", "legal"):
        pitch = {"narrow": 6.0, "wide": 8.7}.get(kind, 7.1) * MM
        y = 30 * MM
        while y < height - 12 * MM:
            d.line([(0, round(y)), (PAGE_W, round(y))], fill=RULE, width=2)
            rows.append(y)
            y += pitch
        mx = round(PAPER_SPECS[kind][0] * MM)
        d.line([(mx, 0), (mx, height)], fill=MARGIN_RED, width=2)
        if kind == "legal":  # the double rule of a legal pad
            d.line([(mx - 3 * MM, 0), (mx - 3 * MM, height)], fill=MARGIN_RED, width=2)
        if kind == "looseleaf":
            r = 3 * MM
            for cy in (height * 0.18, height * 0.5, height * 0.82):
                d.ellipse([12 * MM - r, cy - r, 12 * MM + r, cy + r],
                          fill=(232, 232, 230), outline=(205, 205, 200), width=2)
    elif kind == "fourline":
        gap, y = 2.6 * MM, 22 * MM
        while y + 3 * gap < height - 12 * MM:
            for k in range(4):
                d.line([(15 * MM, round(y + k * gap)), (PAGE_W - 10 * MM, round(y + k * gap))],
                       fill=MARGIN_RED if k == 2 else RULE, width=2)
            rows.append(y + 2 * gap)
            y += 3 * gap + 5 * MM
    else:
        y = 25 * MM
        while y < height - 15 * MM:
            rows.append(y)
            y += 8 * MM
    return img, rows


def ink_texture(mask):
    """Makes ink coverage uneven the way a ballpoint is: lighter patches along strokes plus grain.

    Stroke edges are first darkened a little, like ink soaking into paper, so thin strokes stay solid.
    """
    w, h = mask.size
    mask = mask.point(lambda v: round(255 * (v / 255) ** 0.7))
    patches = Image.effect_noise((w // 12 + 2, h // 12 + 2), 64).resize((w, h), Image.Resampling.BICUBIC)
    patches = patches.point(lambda v: max(0, min(255, round(255 * (0.9 + 0.12 * (v - 128) / 64)))))
    grain = Image.effect_noise((w, h), 32)
    grain = grain.point(lambda v: max(0, min(255, round(255 * (0.96 + 0.05 * (v - 128) / 32)))))
    return ImageChops.multiply(ImageChops.multiply(mask, patches), grain)


def finish(paper, ink, color, scan=None, scan_strength=1.0, bg=PAPER_BG):
    """Lays the ink onto the paper, softens it slightly and adds faint paper grain.
    With scan (a Random), the page also gets a phone-photo look: a slight tilt and uneven light."""
    page = Image.composite(Image.new("RGB", paper.size, color), paper, ink_texture(ink.mask))
    page = page.filter(ImageFilter.GaussianBlur(0.35))
    noise = Image.effect_noise(page.size, 5)
    page = ImageChops.add(page, Image.merge("RGB", [noise] * 3), offset=-128)
    if scan is not None and scan_strength > 0:
        page = scan_look(page, scan, scan_strength, bg)
    return page


def scan_look(page, rng, strength=1.0, bg=PAPER_BG):
    """A slight tilt, a soft light falling from one side and darker corners, like a phone photo.
    strength 1 is a careful photo; 2 is a quick snap with more tilt and shadow."""
    w, h = page.size
    page = page.rotate(rng.gauss(0, 0.35 * strength), resample=Image.Resampling.BICUBIC, fillcolor=bg)
    # light falling unevenly: a smooth ramp between random brightness at a few points
    dim = 0.1 * strength
    side = Image.new("L", (3, 3))
    side.putdata([round(255 * rng.uniform(1 - dim, 1.0)) for _ in range(9)])
    side = side.resize((w, h), Image.Resampling.BICUBIC)
    corners = Image.radial_gradient("L").resize((w, h), Image.Resampling.BILINEAR)
    corners = corners.point(lambda v: round(255 * (1.0 - 0.08 * strength * v / 255)))
    light = ImageChops.multiply(side, corners)
    return ImageChops.multiply(page, Image.merge("RGB", [light] * 3))


def ink_color(name):
    """A named ink or a '#rrggbb' value."""
    if isinstance(name, str) and name.startswith("#") and len(name) == 7:
        return tuple(int(name[i:i + 2], 16) for i in (1, 3, 5))
    return INKS.get(name, INKS["blue"])


def add_slips(words, rng, rate):
    """Puts a scribbled-out false start before a few words: the writer began the word, slipped,
    crossed it out and wrote it again."""
    out = []
    for word in words:
        plain = word.replace(SUP, "").replace(SUB, "")
        if len(plain) >= 5 and plain.isalpha() and rng.random() < rate:
            n = rng.randint(2, min(4, len(plain) - 2))
            slip = plain[:n]
            if rng.random() < 0.5:  # a wrong letter after the right ones
                slip += rng.choice("aeirstnlo")
            out.append(SLIP + slip)
        out.append(word)
    return out


def flow(blocks, writer, left, right, slip_rate=0.0):
    """Turns blocks into ruled lines: (kind, words, x offset from the text margin)."""
    for kind, text, indent, first in blocks:
        if kind == "blank":
            yield kind, [], 0
            continue
        width = right - left - indent
        words = text.split()
        if kind == "p" and slip_rate:
            words = add_slips(words, writer.rng, slip_rate)
        for i, words in enumerate(writer.wrap(words, width - first, width)):
            yield kind, words, indent + (first if i == 0 else 0)


def draw_row(ink, writer, kind, words, x, rule_y, lift, right=None):
    baseline = rule_y - lift
    end = writer.write_line(ink, words, x, baseline, right)
    if kind == "h":
        writer.underline(ink, x, end, baseline + 0.5 * writer.xh)


DEFAULTS = dict(paper="lined", texture="cream", ink="blue", seed=7, habit_seed=None, mess=1.0,
                size=1.0, slant=0.0, pressure=0, letter_gap=1.0, word_gap=1.0, slips=1.5, scan=0.0)


def render_pages(blocks, font, progress=None, **opts):
    """Renders blocks to a list of page images. Options (see DEFAULTS): paper, texture, ink
    (name or #rrggbb), seed (layout), habit_seed (the writer's letter habits; None = from seed),
    mess, size, slant (shear, + leans right), pressure (-1/0/1), letter_gap, word_gap
    (multipliers), slips (% of words with a crossed-out false start), scan (0 off, 1 light, 2 strong).
    progress(page_number) is called as each page is finished."""
    o = {**DEFAULTS, **opts}
    rng = random.Random(o["seed"])
    scan_rng = random.Random(o["seed"] + 1) if o["scan"] else None
    color, bg = ink_color(o["ink"]), TEXTURES.get(o["texture"], TEXTURES["cream"])[0]
    xh, left, right = paper_spec(o["paper"])
    writer = Writer(font, xh * o["size"], rng, o["mess"], fallbacks_for(font), o["habit_seed"],
                    o["slant"], o["pressure"], o["letter_gap"], o["word_gap"])
    missing = writer.missing("".join(text for _, text, _, _ in blocks))
    if missing:
        print("warning: no font has these characters:", "".join(sorted(missing)))
    lift = 0.02 * xh if o["paper"] == "fourline" else 0.06 * xh
    lines = list(flow(blocks, writer, left, right, o["slips"] / 100))
    pages, img, layer, rows, r = [], None, None, [], 0

    def close_page():
        pages.append(finish(img, layer, color, scan_rng, o["scan"], bg))
        if progress:
            progress(len(pages))

    for i, (kind, words, offset) in enumerate(lines):
        if img is None or r >= len(rows):
            if img is not None:
                close_page()
            img, rows = make_paper(o["paper"], rng, texture=o["texture"])
            layer, r = Ink(img.size), 0
            if kind == "blank":
                continue
        if words:
            writer.mess = o["mess"] * (1 + 0.3 * i / len(lines))  # writing loosens up as the work goes on
            x = left + offset + rng.gauss(0, 0.12 * xh) * writer.mess
            draw_row(layer, writer, kind, words, x, rows[r], lift + rng.gauss(0, 0.03 * xh) * writer.mess,
                     right)
        r += 1
    if img is not None:
        close_page()
    return pages


def render(blocks, font, paper, ink, seed, mess, size=1.0, slips=0.0, scan=False):
    """Command-line entry kept for compatibility; see render_pages."""
    return render_pages(blocks, font, paper=paper, ink=ink, seed=seed, mess=mess, size=size,
                        slips=slips, scan=1.0 if scan else 0.0)


def save_pdf(pages, path):
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=DPI)


def compare(font_dir, out, ink, seed):
    fonts = sorted(Path(font_dir).glob("*.ttf"))
    rows_per = 5
    rng = random.Random(seed)
    xh, left, right = paper_spec("lined")
    height = round(30 * MM + 7.1 * MM * (rows_per + 1) * len(fonts) + 10 * MM)
    img, rows = make_paper("lined", rng, height)
    layer = Ink(img.size)
    draw = ImageDraw.Draw(img)
    label_big = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", round(7 * MM))
    label_small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", round(2.6 * MM))
    for i, font in enumerate(fonts):
        strip = rows[i * (rows_per + 1): i * (rows_per + 1) + rows_per]
        draw.text((12.5 * MM, strip[0]), str(i + 1), font=label_big, fill=(200, 60, 60), anchor="ms")
        draw.text((12.5 * MM, strip[0] + 4 * MM), font.stem, font=label_small,
                  fill=(120, 120, 120), anchor="ma")
        writer = Writer(font, xh, rng)
        lines = list(flow(SAMPLE, writer, left, right))
        for rule_y, (kind, words, offset) in zip(strip, lines):
            draw_row(layer, writer, kind, words, left + offset, rule_y, 0.06 * xh)
    finish(img, layer, INKS[ink]).save(out)
    print(f"saved {out} ({len(fonts)} fonts)")


def read_text(path):
    return parse_markup(Path(path).read_text(encoding="utf-8"))


def blocks_to_markup(blocks):
    """The inverse of parse_markup, close enough to round-trip: lets a .docx be shown as editable
    text in the app."""
    def unmark(text):
        out, i = [], 0
        while i < len(text):
            c = text[i]
            if c in (FRAC, SUM) and END in text[i:]:
                end = text.index(END, i)
                a, _, b = text[i + 1:end].partition(SEP)
                out.append(f"\\frac{{{unmark(a)}}}{{{unmark(b)}}}" if c == FRAC
                           else f"\\sum_{{{unmark(a)}}}^{{{unmark(b)}}}")
                i = end + 1
            elif c in (SUP, SUB):
                j = i
                run = []
                while j < len(text) and text[j] == c and j + 1 < len(text):
                    run.append(text[j + 1])
                    j += 2
                out.append(("^{%s}" if c == SUP else "_{%s}") % "".join(run))
                i = j
            elif c in (HAT, BAR) and i + 1 < len(text):
                out.append(("\\hat{%s}" if c == HAT else "\\bar{%s}") % text[i + 1])
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)

    lines = []
    for kind, text, indent, first in blocks:
        if kind == "blank":
            lines.append("")
        elif kind == "h":
            lines.append("# " + unmark(text))
        else:
            lines.append(("    " if first else "") + unmark(text))
    return "\n".join(lines)


def parse_markup(source):
    blocks = []
    for line in source.splitlines():
        arg = r"\{((?:[^{}]|\{[^{}]*\})*)\}"  # a brace group that may hold one nested group, e.g. {σ^{2}}
        text = re.sub(r"\\sum_" + arg + r"\^" + arg, lambda mt: SUM + mt.group(1) + SEP + mt.group(2) + END,
                      line)
        text = re.sub(r"\\frac" + arg + arg, lambda mt: FRAC + mt.group(1) + SEP + mt.group(2) + END,
                      text)
        text = re.sub(r"\\(hat|bar)\{(.)\}", lambda mt: (HAT if mt.group(1) == "hat" else BAR) + mt.group(2),
                      text)
        text = re.sub(r"\^(\{[^}]*\}|\d+|\S)", lambda mt: mark(mt.group(1).strip("{}"), SUP), text)
        text = re.sub(r"_\{([^}]*)\}", lambda mt: mark(mt.group(1), SUB), text)
        # spaces inside a stacked object would split it into words, so drop them there
        text = re.sub(f"[{FRAC}{SUM}][^{END}]*{END}", lambda mt: mt.group(0).replace(" ", ""), text)
        text = normalize(text)
        if not text.strip():
            blocks.append(("blank", "", 0, 0))
        elif text.startswith("# "):
            blocks.append(("h", text[2:].strip(), 0, 0))
        else:
            first = 8 * MM if text.startswith(" ") else 0
            blocks.append(("p", text.strip(), 0, first))
    return blocks


def _roman(n):
    out = ""
    for v, s in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                 (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _format_number(n, fmt):
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    return str(n)


def read_docx(path):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    mc_fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    skip = {qn("w:pPr"), qn("w:rPr"), qn("m:rPr"), mc_fallback}

    def prop(node, path, default):
        el = node.find(path) if node is not None else None
        return el.get(qn("m:val"), default) if el is not None else default

    def group(s):
        plain = s.replace(SUP, "").replace(SUB, "")
        return s if len(plain) <= 1 or plain.isalnum() else f"({s})"

    def text_of(node, kind=""):
        """Text of a paragraph element in reading order, with equations written out inline."""
        tag = node.tag
        if tag in (qn("w:t"), qn("m:t")):
            return mark(node.text or "", kind)
        if tag in (qn("w:tab"), qn("w:br"), qn("w:cr")):
            return " "
        if tag in skip:
            return ""
        if tag == qn("w:r"):
            va = node.find(f"{qn('w:rPr')}/{qn('w:vertAlign')}")
            if va is not None:
                kind = {"superscript": SUP, "subscript": SUB}.get(va.get(qn("w:val")), kind)
        part = lambda name, k=kind: "".join(text_of(c, k) for c in node.findall(qn(name)))
        if tag == qn("m:sSup"):
            return part("m:e") + part("m:sup", SUP)
        if tag == qn("m:sSub"):
            return part("m:e") + part("m:sub", SUB)
        if tag == qn("m:sSubSup"):
            return part("m:e") + part("m:sub", SUB) + part("m:sup", SUP)
        if tag == qn("m:f"):
            return FRAC + part("m:num") + SEP + part("m:den") + END
        if tag == qn("m:acc"):
            accent = prop(node.find(qn("m:accPr")), qn("m:chr"), "\u0302")
            return (BAR if accent in "\u0304\u0305\u00af" else HAT) + part("m:e")
        if tag == qn("m:bar"):
            return BAR + part("m:e")
        if tag == qn("m:rad"):
            return "\u221a" + group(part("m:e"))
        if tag == qn("m:d"):
            pr = node.find(qn("m:dPr"))
            sep = prop(pr, qn("m:sepChr"), ",")
            return (prop(pr, qn("m:begChr"), "(") + sep.join(text_of(e, kind) for e in node.findall(qn("m:e")))
                    + prop(pr, qn("m:endChr"), ")"))
        if tag == qn("m:nary"):
            sign = prop(node.find(qn("m:naryPr")), qn("m:chr"), "\u222b")
            if sign == "\u03a3" or sign == "\u2211":
                return SUM + part("m:sub") + SEP + part("m:sup") + END + " " + part("m:e")
            return sign + part("m:sub", SUB) + part("m:sup", SUP) + " " + part("m:e")
        return "".join(text_of(c, kind) for c in node)

    doc = Document(path)
    levels = {}  # numId -> {ilvl: (numFmt, lvlText, start)}
    try:
        numbering = doc.part.numbering_part.element
        abstract = {}
        for an in numbering.findall(qn("w:abstractNum")):
            lv = {}
            for lvl in an.findall(qn("w:lvl")):
                get = lambda tag, default: (lvl.find(qn(tag)).get(qn("w:val"))
                                            if lvl.find(qn(tag)) is not None else default)
                lv[int(lvl.get(qn("w:ilvl")))] = (get("w:numFmt", "decimal"),
                                                  get("w:lvlText", "%1."), int(get("w:start", 1)))
            abstract[an.get(qn("w:abstractNumId"))] = lv
        for num in numbering.findall(qn("w:num")):
            levels[num.get(qn("w:numId"))] = abstract.get(
                num.find(qn("w:abstractNumId")).get(qn("w:val")), {})
    except (KeyError, NotImplementedError, AttributeError):
        pass

    counters = {}

    def list_marker(p):
        num_pr = None
        for ppr in (p._p.pPr, p.style.element.pPr if p.style is not None else None):
            if ppr is not None and ppr.numPr is not None:
                num_pr = ppr.numPr
                break
        if num_pr is None or num_pr.numId is None:
            return None, 0
        num_id = str(num_pr.numId.val)
        ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else 0
        lv = levels.get(num_id, {})
        fmt, text, start = lv.get(ilvl, ("decimal", "%1.", 1))
        if fmt == "bullet":
            return "-", ilvl
        count = counters.setdefault(num_id, {})
        count[ilvl] = count.get(ilvl, start - 1) + 1
        for deeper in [k for k in count if k > ilvl]:
            del count[deeper]
        marker = re.sub(r"%(\d)", lambda mt: _format_number(
            count.get(int(mt.group(1)) - 1, 1), lv.get(int(mt.group(1)) - 1, (fmt,))[0]), text)
        return marker, ilvl

    def emit(p):
        text = re.sub(r"\s+", " ", normalize(text_of(p._p))).strip()
        style = p.style.name if p.style is not None else ""
        if not text:
            return ("blank", "", 0, 0)
        if style.startswith("Heading") or style == "Title":
            return ("h", text, 0, 0)
        marker, ilvl = list_marker(p)
        if marker:
            return ("p", f"{marker} {text}", ilvl * 6 * MM, 0)
        pf = p.paragraph_format
        first = 8 * MM if pf.first_line_indent and pf.first_line_indent > 0 else 0
        return ("p", text, 0, first)

    blocks = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(emit(Paragraph(child, doc)))
        elif child.tag == qn("w:tbl"):
            for row in Table(child, doc).rows:
                cells = [re.sub(r"\s+", " ", normalize(text_of(c._tc))).strip() for c in row.cells]
                blocks.append(("p", " | ".join(c for c in cells if c), 0, 0))
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help=".docx or .txt file")
    ap.add_argument("-o", "--out", required=True, help="output .pdf (or .png for --compare)")
    ap.add_argument("--font", default=str(Path(__file__).resolve().parent.parent / "fonts" / "ReenieBeanie.ttf"),
                    help="main .ttf; fallbacks are picked from the same folder")
    ap.add_argument("--paper", default="lined", choices=PAPERS)
    ap.add_argument("--ink", default="blue", choices=sorted(INKS))
    ap.add_argument("--mess", type=float, default=1.0, help="0 = tidy, 1 = default, 2 = messy")
    ap.add_argument("--size", type=float, default=1.0, help="letter size multiplier, e.g. 0.9 or 1.1")
    ap.add_argument("--slips", type=float, default=1.5,
                    help="percent of words preceded by a crossed-out false start (0 = none)")
    ap.add_argument("--scan", action="store_true",
                    help="phone-photo look: slight tilt and uneven lighting (not for printing)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--compare", metavar="FONT_DIR", help="render one sample per font in a folder")
    args = ap.parse_args()

    if args.compare:
        compare(args.compare, args.out, args.ink, args.seed)
        return
    if not args.input:
        ap.error("an input file is required unless --compare is used")
    blocks = read_docx(args.input) if args.input.lower().endswith(".docx") else read_text(args.input)
    pages = render(blocks, args.font, args.paper, args.ink, args.seed, args.mess, args.size, args.slips,
                   args.scan)
    out = Path(args.out)
    save_pdf(pages, out)
    for n, page in enumerate(pages, 1):
        page.save(out.with_name(f"{out.stem}_p{n}.png"))
    print(f"saved {out} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
