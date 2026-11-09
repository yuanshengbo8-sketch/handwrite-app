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
import zlib
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
INS = "\x10"  # a word starting with this was forgotten and squeezed in above the line with a caret
GLUE = "\x11"  # a word ending with this is followed by no space (Chinese characters, mixed runs)
CONTROL = MARKS + (SLIP, FRAC, SUM, SEP, END, INS, GLUE)
SUB_SCALE = 0.72  # size of superscripts and subscripts
# Chinese: a character is written about this many x-heights tall, so it fills a ruled row the
# way it does in a school exercise book.
CJK_HEIGHT = 1.75
CJK_PUNCT_CLOSE = set("，。、；：？！）》」』】〉”’…—～·")
CJK_PUNCT_OPEN = set("（《「『【〈“‘")
# Characters a hurried writer mixes up, for the occasional wrong character that gets crossed out
CJK_CONFUSE = [
    "己已巳", "未末", "拨拔", "侯候", "戊戌戍", "即既", "在再", "的得地", "做作", "像象", "那哪",
    "他她它", "以已", "带戴", "克刻", "副幅", "制治", "历厉", "辨辩辫", "竟竞", "梁粱", "账帐",
    "园圆", "清请情晴", "测侧", "峰锋蜂", "坐座", "密蜜", "暴爆", "须需", "练炼", "接按", "复夏",
    "未末", "日曰", "人入八", "土士", "大太犬", "王玉主", "折拆", "外处", "问间", "买卖", "贝见",
    "到倒", "幸辛", "进近", "应因", "情倩", "提题", "具其", "该咳", "识织职", "观现", "经径",
]
CJK_COMMON = ("的一是了不在有人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能"
              "下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实"
              "加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间"
              "样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军"
              "很情者最立代想已通并提直题程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流"
              "路级少图山统接知较将组见计别手角期根论运农指几九区强放决西被干做必战先回则任取据处府")
# Messiness levels 1-10 as the writer's overall looseness (mess). Level 2 is what used to be the
# loosest setting; each level above it is clearly scruffier than the one before.
LEVEL_MESS = {1: 1.3, 2: 2.0, 3: 2.5, 4: 3.0, 5: 3.5, 6: 4.0, 7: 4.6, 8: 5.2, 9: 5.9, 10: 6.6}


def is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF or 0x2E80 <= o <= 0x2FDF
            or 0x20000 <= o <= 0x2FFFF or ch in CJK_PUNCT_CLOSE or ch in CJK_PUNCT_OPEN)


def is_hanzi(ch):
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x2FFFF
# Letters that end with a stroke on the right in a print hand, which may run into the next one
JOIN_FROM = set("adehiklmnut")
SUP_MAP = dict(zip("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207c\u207d\u207e\u207f\u2071",
                   "0123456789+-=()ni"))
SUB_MAP = dict(zip("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u208a\u208b\u208c\u208d\u208e",
                   "0123456789+-=()"))
FALLBACK_FONTS = ("PatrickHand.ttf", "Kalam.ttf")
CJK_FONTS = ("LongCang.ttf", "ZhiMangXing.ttf", "ZCOOLKuaiLe.ttf")  # bundled Chinese hands
# The faster, running form of a Chinese hand. A hurried writer slips into it for some characters
# (and always the same ones), the way people write 的 or 是 in running script when rushing.
CJK_CURSIVE = {"LongCang.ttf": "ZhiMangXing.ttf"}
# Characters a font draws in their traditional form; they are taken from the next font instead
CJK_SKIP = {"LongCang.ttf": set("时将")}
# Last resort for rare characters the bundled Chinese fonts lack: a Kai (brush-pen) or plain
# system font, so a character never comes out blank.
CJK_SYSTEM_FALLBACKS = ("C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/msyh.ttc",
                        "/System/Library/Fonts/Supplemental/Kaiti.ttc",
                        "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
                        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
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


def _near_cjk(text, i):
    """Whether the character at i stands next to Chinese text (skipping spaces and quotes)."""
    for step in (-1, 1):
        j = i + step
        while 0 <= j < len(text) and (text[j].isspace() or text[j] in "“”‘’…—"):
            j += step
        if 0 <= j < len(text) and is_hanzi(text[j]):
            return True
    return False


def normalize(text):
    # curly quotes, dashes and ellipses stay as they are in Chinese text, where the Chinese font
    # writes them; next to Latin text they become the plain marks every handwriting font has
    out = []
    for i, c in enumerate(text):
        if c in "“”‘’…—" and _near_cjk(text, i):
            out.append(c)
        else:
            out.append(c.translate(ASCII_PUNCT))
    text = "".join(out)
    return "".join(SUP + SUP_MAP[c] if c in SUP_MAP else SUB + SUB_MAP[c] if c in SUB_MAP else c
                   for c in text)


class Face:
    """A font scaled so its lowercase x is xheight pixels tall, with glyphs cached as 3x masks."""

    def __init__(self, path, xheight, cjk=False):
        self.path = str(path)
        self.cjk = cjk
        self.cmap = set(TTFont(self.path, fontNumber=0, lazy=True).getBestCmap())
        self.cmap -= {ord(c) for c in CJK_SKIP.get(Path(self.path).name, ())}
        probe = self.font_at(200)
        # a Chinese font is sized by its characters (CJK_HEIGHT x-heights tall) and shifted so they
        # stand on the line; a Latin font by its lowercase x
        self.shift, self.xh = 0.0, xheight
        if cjk:
            ref = next((c for c in "国田口" if ord(c) in self.cmap), None)
            if ref:
                _, t, _, b = probe.getbbox(ref, anchor="ls")
                self.em = CJK_HEIGHT * xheight / (b - t) * 200
                self.shift = -(b / 200 * self.em) - 0.04 * xheight
            else:
                self.em = xheight / 0.5
        else:
            self.em = xheight / -probe.getbbox("x", anchor="ls")[1] * 200
        self.fonts = {}
        self.cache = {}

    def font_at(self, size):
        return ImageFont.truetype(self.path, size, index=0)

    def font(self, scale):
        if scale not in self.fonts:
            self.fonts[scale] = self.font_at(max(4, round(self.em * scale * SS)))
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
            adv = font.getlength(ch) / SS
            if self.cjk and is_hanzi(ch) and mask is not None:
                # by hand a character takes the room its strokes need, not a fixed square: the
                # advance comes from the ink, with a little air either side (fonts differ a lot
                # in how much empty box they leave around a character)
                ink_w, bearing = (r - l) / SS, 0.07 * self.xh * scale
                box = max(ink_w, 0.62 * CJK_HEIGHT * self.xh * scale)
                shift = l / SS - (box - ink_w) / 2 - bearing
                l, r = l - shift * SS, r - shift * SS
                self.cache[key] = (mask, 2 - l, 2 - t, box + 2 * bearing, (l / SS, t / SS, r / SS, b / SS))
                return self.cache[key]
            if self.cjk and (ch in CJK_PUNCT_CLOSE or ch in CJK_PUNCT_OPEN) and mask is not None:
                # full-width punctuation sits in a whole character box; by hand it takes about half
                ink_w = (r - l) / SS
                adv = min(adv, ink_w + 0.45 * self.em * scale)
                if ch in CJK_PUNCT_OPEN:  # pull the mark to the left edge of its narrower box
                    shift = l / SS - 0.1 * self.em * scale
                    l, r = l - shift * SS, r - shift * SS
                    mask_ox = 2 - l
                    self.cache[key] = (mask, mask_ox, 2 - t, adv, (l / SS, t / SS, r / SS, b / SS))
                    return self.cache[key]
            self.cache[key] = (mask, 2 - l, 2 - t, adv, (l / SS, t / SS, r / SS, b / SS))
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
                 slant=0.0, pressure=0, letter_gap=1.0, word_gap=1.0, cjk_fonts=(), cursive_font=None):
        self.faces = [Face(p, xheight) for p in (font_path, *fallbacks)]
        self.cjk_faces = []
        for p in cjk_fonts:
            try:
                self.cjk_faces.append(Face(p, xheight, cjk=True))
            except OSError:
                pass
        self.cursive = None
        if cursive_font:
            try:
                self.cursive = Face(cursive_font, xheight, cjk=True)
            except OSError:
                pass
        self._habit_key = 0 if habit_seed is None else int(habit_seed) % (2 ** 31)
        self._face_of = {}
        self.space = self.faces[0].advance(" ")
        self.xh, self.rng = xheight, rng
        self.set_mess(mess)
        self.slant = 0.0
        # Standing choices of this writer: overall lean (shear, + leans right), how hard they
        # press (-1 light, 0 normal, 1 heavy) and how tightly they pack letters and words.
        self.slant_bias, self.pressure = slant, pressure
        self.letter_gap, self.word_gap = letter_gap, word_gap
        # This writer's fixed habits: every character gets its own lean, size, height and tilt
        # that it keeps through the whole document, with only small variation on top.
        self._habit_rng = random.Random(rng.random() if habit_seed is None else habit_seed)
        self.habits = {}

    def set_mess(self, mess):
        """mess is how loosely this writer writes: about 1 is careful, 2 relaxed, 6 a scrawl.

        Things that drift slowly (slant, the baseline, word size) grow in step with it; the
        letter-by-letter wobble grows more slowly, so a messy page still reads as one hand
        rather than as letters cut out of different notes."""
        m = self.mess = mess
        self.wobble = m if m <= 2 else 2 * (m / 2) ** 0.7
        self.extra = max(0.0, m - 2)  # how far past the old loosest setting

    def habit(self, ch):
        if ch not in self.habits:
            h = self._habit_rng
            self.habits[ch] = (h.gauss(0, 0.045), h.gauss(0, 0.03), h.gauss(0, 0.04), h.gauss(0, 1.3))
        return self.habits[ch]

    def face_for(self, ch):
        """Chinese characters and full-width marks come from the Chinese hand, everything else from
        the Latin one; each falls back to the other fonts that have the character."""
        if ch not in self._face_of:
            code = ord(ch)
            order = (self.cjk_faces + self.faces) if is_cjk(ch) else (self.faces + self.cjk_faces)
            self._face_of[ch] = next((f for f in order if code in f.cmap), self.faces[0])
        face = self._face_of[ch]
        if self.cursive is not None and face.cjk and self.extra > 1.0 and is_hanzi(ch):
            # how likely this writer is to run this character together: common characters first
            share = min(0.75, 0.13 * (self.mess - 3.0)) * (1.5 if ch in CJK_COMMON else 0.8)
            pick = (zlib.crc32(ch.encode("utf-8")) * 2654435761 + self._habit_key * 40503) % 1000 / 1000
            if pick < share and ord(ch) in self.cursive.cmap:
                return self.cursive
        return face

    def cjk_width(self):
        """The width of one Chinese character as this writer sets it."""
        face = self.face_for("国")
        return face.advance("国") if face.cjk else 1.8 * self.xh

    def glyphs(self, word):
        """Yields items: ("g", char, face, shift, accent, gap) for a letter, where shift is 1 for
        superscript and -1 for subscript, accent is HAT, BAR or None and gap is extra space before
        a superscript or subscript run; ("frac", num, den) and ("sum", lower, upper) for
        stacked objects."""
        word = word.lstrip(SLIP + INS).rstrip(GLUE)
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
        return {c for c in text if c not in CONTROL and not c.isspace()
                and all(ord(c) not in f.cmap for f in self.faces + self.cjk_faces)}

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
        if word.startswith(INS):  # written above the line, takes no room on it
            return 0
        return sum(self.item_width(it) for it in self.glyphs(word))

    def gap(self, word, nxt=None):
        """The usual space after a word: a word space, or for glued Chinese text a small gap
        (a bit more where Chinese meets Latin letters or digits)."""
        if not word.endswith(GLUE):
            return self.space * 1.1
        last = word.rstrip(GLUE)[-1:]
        first = nxt.lstrip(SLIP + INS)[:1] if nxt else ""
        if (last and not is_cjk(last)) or (first and not is_cjk(first)):
            return 0.35 * self.space
        return 0.06 * self.xh

    def _limit(self, width):
        e = self.extra
        return width + self.rng.uniform(-0.8 - 0.15 * e, min(4.0, 2.5 + 0.3 * e)) * self.xh

    def wrap(self, words, first_width, width):
        """Breaks words into lines. A line may run a little past the margin: the writer then
        squeezes the last words in rather than starting a new line."""
        lines, cur, used, prev = [], [], 0, None
        limit = self._limit(first_width)
        for word in words:
            if word.startswith(INS):  # rides along above the line wherever it falls
                cur.append(word)
                continue
            w = self.width(word)
            grown = w if prev is None else used + self.gap(prev, word) + w
            if prev is not None and grown > limit:
                lines.append(cur)
                cur, used = [word], w
                limit = self._limit(width)
            else:
                cur.append(word)
                used = grown
            prev = word
        if cur:
            lines.append(cur)
        return lines

    def _stamp(self, ink, face, ch, scale, x, baseline, shear, pressure, guard=None,
               squeeze=1.0, weight=0, lazy=0.0, grow=1.0):
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
        rng, m = self.rng, self.wobble
        baseline += face.shift * scale
        em = face.em * scale
        h_shear, h_size, _, h_rot = self.habit(ch)
        shear += h_shear + lazy * 0.5
        # a Chinese character is a dense knot of strokes: bend it less, or it turns to a blot
        bend = (0.55 + 0.05 * min(self.extra, 5)) if face.cjk else 1.0
        # letter sizes stay fairly even even in a messy hand; the mess goes into shape and slant
        ms = min(m, 3.0)
        if not face.cjk and self.extra > 0:
            # a fast Latin hand lets single letters bounce off the line and swell or shrink
            e = min(self.extra, 5)
            ms += 0.35 * e
            baseline += rng.gauss(0, 0.025 * e) * self.xh * scale
        size = grow * (1 + h_size) * (1 - lazy) * (1 + rng.gauss(0, 0.015) * ms)
        if ch.isupper():
            size *= 1.03  # capitals tend to come out a touch bigger
        elif ch.isdigit():
            size *= 0.95  # digits a touch smaller and tighter
        # a hurried hand writes narrower letters
        sx = size * squeeze * (1 + rng.gauss(0, 0.03) * ms) * (1 - 0.012 * min(self.extra, 5))
        sy = size * (1 + rng.gauss(0, 0.02) * ms)
        rot_m = min(m, 3.0) + 0.3 * max(0.0, m - 3.0)
        rot = h_rot + rng.gauss(0, 1.0) * (1 + 2 * lazy) * rot_m * (0.8 if face.cjk else 1.0 + 0.12 * min(self.extra, 5))
        if face.cjk:  # a hurried Chinese hand lets its horizontal strokes climb to the right
            rot -= 0.45 * min(self.extra, 5) * (1 + rng.gauss(0, 0.3))
        rot = math.radians(rot)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        # Smooth waves across the letter: the first two bend strokes sideways, the last two up/down.
        # Each wave stretches or squeezes any part of the letter by at most 10%, so strokes bend
        # but never fold into blobs.
        waves = []
        cap = (0.1 + 0.025 * self.extra) * bend
        for _ in range(4):
            cycles, angle = rng.uniform(0.5, 1.2), rng.uniform(0, 2 * math.pi)
            k = 2 * math.pi * cycles / em
            strain = max(-cap, min(cap, rng.gauss(0, 0.04) * (1 + 2 * lazy) * m * bend))
            waves.append((strain / k, k * math.cos(angle), k * math.sin(angle), rng.uniform(0, 2 * math.pi)))
        mid_y = (t + b) / 2 if face.cjk else -0.5 * self.xh * scale
        cx, cy = adv * sx / 2, mid_y * sy
        # the bend is zero at the letter's middle, so it changes the shape without moving the letter
        at_middle = [a * math.sin(kx * adv / 2 + ky * mid_y + ph) for a, kx, ky, ph in waves]

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
        rng, m, w, e, xh = self.rng, self.mess, self.wobble, self.extra, self.xh
        # How far a row wanders off its ruled line is held in check even for the messiest hand:
        # people scrawl, but they still write along the lines.
        lm = min(m, 3.0) + 0.15 * max(0.0, m - 3.0)
        start, slope, drift = x, rng.gauss(0, 0.0018) * lm, 0.0
        wave_k, wave_ph = rng.uniform(0.3, 0.8) * 2 * math.pi / (40 * xh), rng.uniform(0, 2 * math.pi)
        wave_amp = 0.06 * xh * lm
        drift_cap = 0.15 * xh * max(1.0, min(1.8, (m / 2) ** 0.7))
        slant_cap = 0.12 * max(1.0, min(1.6, (m / 2) ** 0.5))
        size_m = max(0.0, min(m, 5.0) - 1)
        line_size = 1 + max(-0.06, min(0.06, rng.gauss(0, 0.01) * size_m))
        real = [wd for wd in words if not wd.startswith(INS)]
        natural = sum(self.width(wd) for wd in real) + sum(self.gap(a, b) for a, b in zip(real, real[1:]))
        room = (right - x + 0.6 * xh) if right is not None else natural
        squeeze = max(0.84, min(1.0, room / natural)) if natural > room else 1.0
        word_squeeze, prev_word = 1.0, None
        for n_word, word in enumerate(words):
            if word.startswith(INS):
                if prev_word is not None:
                    self._insert(ink, word, x - 0.5 * self.gap(prev_word), baseline + drift + slope * (x - start))
                continue
            cjk_word = is_cjk(word.lstrip(SLIP)[:1] or "a")
            # Chinese moves on by the character, so its wander is spread over more, smaller steps
            drift = max(-drift_cap, min(drift_cap, drift + rng.gauss(0, 0.04 * xh) * lm * (0.5 if cjk_word else 1)))
            self.slant = max(-slant_cap, min(slant_cap, self.slant * 0.9 + rng.gauss(0, 0.02) * w))
            word_pressure = rng.uniform(0.9 - 0.02 * e, 1.0) * (0.86 if self.pressure < 0 else 1.0)
            # some words come out a little bigger or smaller than the rest
            grow = line_size * max(0.88, min(1.14, 1 + rng.gauss(0, 0.012 if cjk_word else 0.016) * size_m))
            # pen pressure changes from word to word, around this writer's usual pressure
            weight = rng.choice({-1: (0, 0, -1, -1, -1), 0: (0, 0, 0, 0, 1, -1), 1: (0, 0, 1, 1, 1)}[self.pressure])
            # the squeeze only sets in over the second half of the line
            here = (x - start) / max(1, room)
            word_squeeze = 1.0 if here < 0.45 else 1 - (1 - squeeze) * min(1, (here - 0.45) / 0.3)
            items = list(self.glyphs(word))
            letters = sum(1 for it in items if it[0] == "g" and it[1].isalpha())
            fade = min(0.2, 0.04 * max(0.0, m - 1.5))  # the pen lifts a little towards a word's end
            join_p = min(0.85, 0.55 + 0.05 * e)
            spread = 0.07 * max(1.0, w / 2)
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
                lazy = min(0.25, min(0.18, 0.03 * (k - 3)) * m) if letters >= 6 and k >= 4 else 0.0
                # letters bob up and down smoothly, so neighbours sit at similar heights
                bob = 0.6 * bob + rng.gauss(0, 0.025 * xh) * w
                cx = pos + rng.gauss(0, 0.02 * xh) * w
                cy = (baseline + drift + slope * (cx - start) - raise_by + bob
                      + self.habit(ch)[2] * xh * scale
                      + wave_amp * math.sin(wave_k * (cx - start) + wave_ph))
                guard = None
                if prev:
                    allowed = 1.1 * designed_overlap(prev[1], prev[0], prev[2], face, ch, scale, gap) + 100
                    if joined:
                        allowed += 14 * 255  # the two letters are meant to touch
                    allowed *= 1 + 0.25 * e  # a hurried hand lets letters crowd into each other
                    guard = (*prev[3:], allowed)
                pressure = word_pressure * rng.uniform(0.93, 1.0) * (1 - fade * k / max(1, len(items) - 1))
                # a hurried hand also leans further forward
                lean = self.slant_bias + 0.015 * min(e, 5) + self.slant
                advance, top, drawn, moved = self._stamp(ink, face, ch, scale, cx, cy,
                                                         lean + rng.gauss(0, 0.02) * min(w, 2.0),
                                                         pressure, guard, word_squeeze, weight, lazy, grow)
                if accent:
                    self._accent(ink, accent, cx + moved, cx + moved + advance, top)
                prev = (ch, face, scale, *drawn) if drawn else None
                # letters sit a little closer or further apart each time; some pairs run together
                nxt = items[k + 1] if k + 1 < len(items) else None
                joined = (nxt is not None and nxt[0] == "g" and not shift and not nxt[3]
                          and ch in JOIN_FROM and nxt[1].isalpha() and nxt[1].islower()
                          and rng.random() < join_p)
                pos += moved + advance * self.letter_gap * (rng.uniform(0.8 - 0.02 * e, 0.9) if joined
                                                            else rng.uniform(1.01 - spread, 1.01 + spread))
            if word.startswith(SLIP):
                self._scribble(ink, x, pos, baseline + drift, word)
            nxt_word = next((wd for wd in words[n_word + 1:] if not wd.startswith(INS)), None)
            if word.endswith(GLUE):
                # Chinese characters follow each other with only a small, uneven gap; a hurried
                # hand sometimes runs one character into the next
                g = self.gap(word, nxt_word)
                x = pos + max(-0.12 * xh * min(e, 4) / 4, g * word_squeeze + rng.gauss(0, 0.05 * xh) * (1 + 0.35 * e))
            else:
                after = 1.0 + (0.35 if word[-1:] in ",.;:" else 0.0)  # a breath after punctuation
                wg = 0.25 * max(1.0, (m / 2) ** 0.5)
                x = pos + self.space * self.word_gap * word_squeeze * rng.uniform(1 - wg, 1 + wg) * after
            prev_word = word
        return x - (self.space if prev_word is not None and not prev_word.endswith(GLUE) else 0)

    def _insert(self, ink, word, x, baseline):
        """A word the writer left out and added afterwards: a caret under the gap and the word
        written small above it."""
        rng, xh = self.rng, self.xh
        text = word.lstrip(INS).rstrip(GLUE)
        cjk = any(is_hanzi(c) for c in text)
        scale = 0.55 if cjk else 0.7
        y = baseline + (0.05 if cjk else 0.2) * xh
        ink.stroke([(x - 0.28 * xh + rng.gauss(0, 0.04 * xh), y + rng.gauss(0, 0.05 * xh)),
                    (x + rng.gauss(0, 0.04 * xh), y - 0.55 * xh),
                    (x + 0.28 * xh + rng.gauss(0, 0.04 * xh), y + rng.gauss(0, 0.05 * xh))],
                   2.4, round(255 * rng.uniform(0.85, 1.0)))
        w = scale * self.width(text)
        up = baseline - (1.7 if cjk else 1.3) * xh + rng.gauss(0, 0.05 * xh)
        self._run(ink, text, x - w / 2 + rng.gauss(0, 0.1 * xh), up, scale)

    def _run(self, ink, text, x, baseline, scale):
        """Writes a short plain run (a numerator, a limit) at the given size; returns its width."""
        rng, m = self.rng, self.wobble
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

    def _scribble(self, ink, x0, x1, baseline, word=""):
        """Crosses out a slip. Latin words get one or two quick strokes through the middle, now and
        then a scribbled blot; a wrong Chinese character is mostly blotted out or struck with two
        or three short lines, the way it is done in an exercise book."""
        rng, xh = self.rng, self.xh
        cjk = any(is_hanzi(c) for c in word)
        top, bottom = ((baseline - CJK_HEIGHT * xh, baseline) if cjk
                       else (baseline - 0.95 * xh, baseline - 0.05 * xh))
        roll = rng.random()
        if cjk:
            style = "blot" if roll < 0.45 else "lines" if roll < 0.85 else "single"
        else:
            blot = 0.12 + 0.04 * self.extra
            style = "blot" if roll < blot else "double" if roll < blot + 0.5 else "single"
        value = lambda: round(255 * rng.uniform(0.85, 1.0))
        if style == "blot":
            # tight back-and-forth loops until the word is a dark knot
            pts, x, up = [], x0 - 0.1 * xh, True
            step = 0.13 * xh
            for sweep in range(2):
                while (x < x1 + 0.1 * xh) if sweep == 0 else (x > x0 - 0.1 * xh):
                    y = (top + 0.1 * (bottom - top)) if up else (bottom - 0.1 * (bottom - top))
                    pts.append((x + rng.gauss(0, 0.05 * xh), y + rng.gauss(0, 0.08 * xh)))
                    x += step * rng.uniform(0.7, 1.3) * (1 if sweep == 0 else -1)
                    up = not up
            ink.stroke(pts, 2.4, value())
            return
        if style == "lines":
            n = rng.choice((2, 2, 3))
            for k in range(n):
                y = top + (bottom - top) * (k + 1) / (n + 1) + rng.gauss(0, 0.06 * xh)
                tilt = rng.gauss(0, 0.05 * xh)
                ink.stroke([(x0 - 0.15 * xh, y - tilt), ((x0 + x1) / 2, y + rng.gauss(0, 0.04 * xh)),
                            (x1 + 0.15 * xh, y + tilt)], 2.5, value())
            return
        if style == "single":
            y = (top + bottom) / 2 + rng.gauss(0, 0.08 * xh)
            tilt = rng.gauss(0, 0.12 * xh)
            ink.stroke([(x0 - 0.25 * xh, y + tilt), ((x0 + x1) / 2, y + rng.gauss(0, 0.05 * xh)),
                        (x1 + 0.2 * xh, y - tilt)], 2.5, value())
            return
        for k in range(2):
            y = baseline - xh * (0.55 + 0.25 * k) + rng.gauss(0, 0.08 * xh)
            pts = [(x0 - 0.2 * xh, y)]
            for i in range(1, 5):
                pts.append((x0 + (x1 - x0) * i / 4 + rng.gauss(0, 0.1 * xh),
                            y + rng.gauss(0, 0.12 * xh)))
            ink.stroke(pts, 2.5, value())

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


def cursive_for(font, zh_font=None):
    """The running-script companion of the chosen Chinese hand, if it has one."""
    name = Path(zh_font).name if zh_font else CJK_FONTS[0]
    companion = Path(zh_font).parent / CJK_CURSIVE[name] if zh_font and name in CJK_CURSIVE else (
        Path(font).parent / CJK_CURSIVE[name] if name in CJK_CURSIVE else None)
    return companion if companion is not None and companion.exists() else None


def cjk_fonts_for(font, zh_font=None):
    """The Chinese hand to write with (zh_font, or the first bundled one), then the other bundled
    Chinese fonts and a system font for characters it lacks."""
    folder = Path(font).parent
    chosen = [Path(zh_font)] if zh_font and Path(zh_font).exists() else []
    bundled = [folder / name for name in CJK_FONTS if (folder / name).exists()]
    rest = [p for p in bundled if not chosen or p.resolve() != chosen[0].resolve()]
    return chosen + rest + [Path(p) for p in CJK_SYSTEM_FALLBACKS if Path(p).exists()]


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


def tokenize(text):
    """Splits a paragraph into the units the writer places one after another. Latin text splits at
    spaces. Chinese has no spaces, so every character is its own unit, glued (GLUE) to the next;
    closing marks such as ，。」 stay with the character before them and opening marks such as
    「（ with the one after, so no line starts with a comma or ends with an opening quote."""
    out = []
    for word in text.split():
        if not any(is_cjk(c) for c in word):
            out.append(word)
            continue
        units, i = [], 0  # (kind, text): h hanzi, c closing mark, o opening mark, l anything else
        while i < len(word):
            c = word[i]
            if c in (FRAC, SUM) and END in word[i:]:
                end = word.index(END, i)
                seg, kind, i = word[i:end + 1], "l", end + 1
            elif c in MARKS and i + 1 < len(word):
                seg, i = word[i:i + 2], i + 2
                kind = "h" if is_hanzi(seg[-1]) else "l"
            else:
                seg, i = c, i + 1
                kind = ("h" if is_hanzi(c) else "c" if c in CJK_PUNCT_CLOSE
                        else "o" if c in CJK_PUNCT_OPEN else "l")
            if kind == "l" and units and units[-1][0] == "l":
                units[-1] = ("l", units[-1][1] + seg)
            else:
                units.append((kind, seg))
        pieces, opening = [], ""
        for kind, seg in units:
            if kind == "o":
                opening += seg
            elif (kind == "c" or (kind == "l" and all(ch in ",.;:!?)]" for ch in seg))) and pieces and not opening:
                pieces[-1] += seg
            else:
                pieces.append(opening + seg)
                opening = ""
        if opening:
            pieces.append(opening)
        out.extend(p + GLUE for p in pieces[:-1])
        out.append(pieces[-1])
    return out


def _misspell(word, rng):
    """A plausible slip of the pen: two letters swapped, one dropped or one doubled."""
    i = rng.randint(1, len(word) - 2)
    roll = rng.random()
    if roll < 0.4:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if roll < 0.7:
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


def add_slips(words, rng, rate, mess=1.0):
    """Puts the mistakes of a real writer into a paragraph. Before a few words the writer began
    the word, slipped, crossed it out and wrote it again; a looser writer also crosses out whole
    misspelt words and forgets the odd word, adding it above the line with a caret. In Chinese a
    wrong character (often one that looks or sounds alike) is blotted out and rewritten."""
    out = []
    caret = 0.0 if mess < 3.2 else rate * 0.25 * min(2.0, (mess - 2.8) / 1.5)
    for n, word in enumerate(words):
        glue = GLUE if word.endswith(GLUE) else ""
        bare = word.rstrip(GLUE)
        plain = bare.replace(SUP, "").replace(SUB, "")
        if len(plain) == 1 and is_hanzi(plain):
            # a Chinese line holds about three times as many characters as an English one words
            if rng.random() < rate / 2:
                group = next((g for g in CJK_CONFUSE if plain in g), "")
                roll = rng.random()
                if group and roll < 0.5:
                    wrong = rng.choice([c for c in group if c != plain])
                elif roll < 0.75:
                    wrong = plain  # written badly, blotted out, written again
                else:
                    wrong = rng.choice(CJK_COMMON)
                out.append(SLIP + wrong + GLUE)
            elif n > 0 and rng.random() < caret / 3:
                out.append(INS + word)
                continue
        elif len(plain) >= 5 and plain.isalpha() and rng.random() < rate:
            if mess >= 2.8 and rng.random() < 0.35:
                out.append(SLIP + _misspell(plain, rng))  # the whole word, misspelt
            else:
                k = rng.randint(2, min(4, len(plain) - 2))
                slip = plain[:k]
                if rng.random() < 0.5:  # a wrong letter after the right ones
                    slip += rng.choice("aeirstnlo")
                out.append(SLIP + slip)
        elif n > 0 and len(plain) >= 3 and plain.isalpha() and rng.random() < caret:
            out.append(INS + word)
            continue
        out.append(word)
    return out


def flow(blocks, writer, left, right, slip_rate=0.0):
    """Turns blocks into ruled lines: (kind, words, x offset from the text margin)."""
    for kind, text, indent, first in blocks:
        if kind == "blank":
            yield kind, [], 0
            continue
        width = right - left - indent
        words = tokenize(text)
        if first and words and is_cjk(words[0].lstrip(SLIP + INS)[:1] or "a"):
            first = 2 * writer.cjk_width()  # a Chinese paragraph is indented by two characters
        if kind == "p" and slip_rate:
            words = add_slips(words, writer.rng, slip_rate, writer.mess)
        for i, words in enumerate(writer.wrap(words, width - first, width)):
            yield kind, words, indent + (first if i == 0 else 0)


def join_cjk_lines(text):
    """Text taken out of a PDF breaks Chinese paragraphs at the end of every printed line. Joins a
    line onto the next when it is long, ends mid-sentence in Chinese and the next goes on in
    Chinese, so the hand-written page can break its lines where it needs to."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if out and s and is_cjk(s[0]):
            prev = out[-1].rstrip()
            if (len(prev) >= 15 and prev and (is_hanzi(prev[-1]) or prev[-1] in "，、；：“‘（《「")
                    and sum(is_cjk(c) for c in prev) > len(prev) / 2):
                out[-1] = prev + s
                continue
        out.append(line)
    return "\n".join(out)


def draw_row(ink, writer, kind, words, x, rule_y, lift, right=None):
    baseline = rule_y - lift
    end = writer.write_line(ink, words, x, baseline, right)
    if kind == "h":
        writer.underline(ink, x, end, baseline + 0.5 * writer.xh)


DEFAULTS = dict(paper="lined", texture="cream", ink="blue", seed=7, habit_seed=None, mess=1.0,
                level=None, zh_font=None, size=1.0, slant=0.0, pressure=0, letter_gap=1.0,
                word_gap=1.0, slips=1.5, scan=0.0)


def render_pages(blocks, font, progress=None, **opts):
    """Renders blocks to a list of page images. Options (see DEFAULTS): paper, texture, ink
    (name or #rrggbb), seed (layout), habit_seed (the writer's letter habits; None = from seed),
    mess, size, slant (shear, + leans right), pressure (-1/0/1), letter_gap, word_gap
    (multipliers), slips (% of words with a crossed-out false start), scan (0 off, 1 light, 2 strong),
    level (messiness 1-10; overrides mess), zh_font (the .ttf for Chinese text; None = first bundled).
    progress(page_number) is called as each page is finished."""
    o = {**DEFAULTS, **opts}
    if o["level"] is not None:
        level = max(1, min(10, round(float(o["level"]))))
        o["mess"] = LEVEL_MESS[level]
        o["slips"] = o["slips"] * (0.5 + 0.25 * level)  # a messier writer also makes more slips
    rng = random.Random(o["seed"])
    scan_rng = random.Random(o["seed"] + 1) if o["scan"] else None
    color, bg = ink_color(o["ink"]), TEXTURES.get(o["texture"], TEXTURES["cream"])[0]
    xh, left, right = paper_spec(o["paper"])
    writer = Writer(font, xh * o["size"], rng, o["mess"], fallbacks_for(font), o["habit_seed"],
                    o["slant"], o["pressure"], o["letter_gap"], o["word_gap"],
                    cjk_fonts_for(font, o["zh_font"]), cursive_for(font, o["zh_font"]))
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
            writer.set_mess(o["mess"] * (1 + 0.3 * i / len(lines)))  # writing loosens up as the work goes on
            x = left + offset + rng.gauss(0, 0.12 * xh) * min(writer.mess, 5.0)
            draw_row(layer, writer, kind, words, x, rows[r], lift + rng.gauss(0, 0.03 * xh) * min(writer.mess, 4.0),
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
            first = 8 * MM if text.startswith((" ", "　")) else 0
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
    ap.add_argument("--level", type=int, choices=range(1, 11), help="messiness level 1-10 (overrides --mess)")
    ap.add_argument("--zh-font", help="Chinese .ttf (default: the first bundled Chinese hand)")
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
    pages = render_pages(blocks, args.font, paper=args.paper, ink=args.ink, seed=args.seed, mess=args.mess,
                         level=args.level, zh_font=args.zh_font, size=args.size, slips=args.slips,
                         scan=1.0 if args.scan else 0.0)
    out = Path(args.out)
    save_pdf(pages, out)
    for n, page in enumerate(pages, 1):
        page.save(out.with_name(f"{out.stem}_p{n}.png"))
    print(f"saved {out} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
