#!/usr/bin/env python3
"""スライドを描画した PDF から可読性の基準を機械で検査する。

PowerPoint・Keynote・Google スライド・LaTeX など、形式を問わず「描画した結果の PDF」を測る。
発表当日と同じ描画（PowerPoint なら PowerPoint が書き出した PDF）を測ることに意味がある。

  python check_pdf.py deck.pdf [--pages 1-14] [--shots DIR] [--json FILE] [--verbose]
  python check_pdf.py --pptx deck.pptx      # Windows で PowerPoint があれば PDF を書き出してから測る
  python check_pdf.py --selftest

閾値は「高さ 7.5 in（540 pt）のスライドでの pt」で持つ。ページの高さが 540 pt でなければ高さの比で換算する。
違反（1 件でもあれば終了コード 1）: G1 文字の大きさ / G2 コントラスト比 / G3 スライドからのはみ出し /
G5 文字どうしの重なり / G6 見出しの行数。参考情報: I1〜I6（終了コードに影響しない）。
終了コード: 0 違反なし / 1 違反あり / 2 実行できなかった。

依存: pypdfium2（Apache-2.0 / BSD-3-Clause、PDFium は BSD-3-Clause）、numpy（BSD-3-Clause）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from collections import Counter

TOOL = "check_pdf"
VERSION = "1.0.0"
BASIS_H = 540.0          # 高さ 7.5 in のスライド（pt）
RENDER_PX_PER_PT = 2.0   # 540 pt 換算 1 pt あたりの画素（1080 px の高さで描く）

try:
    import numpy as np
    import pypdfium2 as pdfium
    import pypdfium2.raw as R
except ImportError as e:  # pragma: no cover
    print(f"必要なライブラリがありません（{e.name}）。次を実行してから再実行してください:\n"
          f"  python -m pip install -r {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirements.txt')}")
    sys.exit(2)


# ---------------------------------------------------------------- 文字の読み取り

BOLD_RE = re.compile(r"bold|black|heavy|semibold|demibold|demi|extrabold|ultrabold|"
                     r"[-_ ]w[6-9]\b|[-_ ]w[6-9]$|[-_]sb\b|[-_]bd\b|[-_]hv\b", re.I)


class Ch:
    __slots__ = ("t", "ws", "size", "x0", "y0", "x1", "y1", "ox", "oy", "rgb", "alpha",
                 "font", "bold", "rot")


class Line:
    __slots__ = ("chars", "rot", "size", "base", "x0", "y0", "x1", "y1", "cy0", "cy1",
                 "script", "block", "text", "footer")


def _clean_font(name: str) -> str:
    return name.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", name) else name


def extract_chars(page, k: float, left: float, top: float) -> list[Ch]:
    """ページの文字を、540 pt 換算・左上原点の座標で返す。"""
    tp = page.get_textpage()
    n = tp.count_chars()
    l, r, b, t = (ctypes.c_double() for _ in range(4))
    ox, oy = ctypes.c_double(), ctypes.c_double()
    cr, cg, cb, ca = (ctypes.c_uint() for _ in range(4))
    m = R.FS_MATRIX()
    buf = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    out: list[Ch] = []
    font_cache: dict[int, tuple[str, bool]] = {}
    for i in range(n):
        u = R.FPDFText_GetUnicode(tp, i)
        s = chr(u) if u else "�"
        if s in "\r\n\x00":
            continue
        if R.FPDFText_IsGenerated(tp, i) == 1 and not s.isspace():
            continue    # PDFium が補った空白は語の区切りとして残す
        obj = R.FPDFText_GetTextObject(tp, i)
        mode = R.FPDFTextObj_GetTextRenderMode(obj) if obj else 0
        if mode in (3, 7):      # 見えない文字（OCR の重ね文字など）は測らない
            continue
        c = Ch()
        c.t = s
        c.ws = s.isspace() or u in (0x200B, 0xFEFF)
        R.FPDFText_GetMatrix(tp, i, ctypes.byref(m))
        det = abs(m.a * m.d - m.b * m.c)
        sc = math.sqrt(det) if det > 0 else 1.0
        c.size = R.FPDFText_GetFontSize(tp, i) * sc * k
        c.rot = (abs(m.b) > 0.05 * sc) or (m.a < 0)
        R.FPDFText_GetCharBox(tp, i, ctypes.byref(l), ctypes.byref(r), ctypes.byref(b), ctypes.byref(t))
        c.x0, c.x1 = (l.value - left) * k, (r.value - left) * k
        c.y0, c.y1 = (top - t.value) * k, (top - b.value) * k
        R.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
        c.ox, c.oy = (ox.value - left) * k, (top - oy.value) * k
        if R.FPDFText_GetFillColor(tp, i, ctypes.byref(cr), ctypes.byref(cg), ctypes.byref(cb), ctypes.byref(ca)):
            c.rgb, c.alpha = (cr.value, cg.value, cb.value), ca.value / 255.0
        else:
            c.rgb, c.alpha = (0, 0, 0), 1.0
        key = (ctypes.cast(obj, ctypes.c_void_p).value if obj else None) or -i - 1
        if key not in font_cache:
            R.FPDFText_GetFontInfo(tp, i, buf, 256, ctypes.byref(flags))
            name = _clean_font(buf.value.decode("latin-1", "replace"))
            w = R.FPDFText_GetFontWeight(tp, i)
            font_cache[key] = (name, bool(BOLD_RE.search(name)) or w >= 600 or bool(flags.value & (1 << 18)))
        c.font, c.bold = font_cache[key]
        if mode == 2:           # 塗り＋線（擬似太字）
            c.bold = True
        out.append(c)
    tp.close()
    return out


def build_lines(chars: list[Ch]) -> list[Line]:
    """描かれた順に文字をたどって行にまとめる。"""
    lines: list[Line] = []
    cur: list[Ch] = []
    band = [0.0, 0.0, 0.0, 0.0]   # x0, x1, y0, y1（空白を除く）
    last = None

    def flush():
        if cur and any(not c.ws for c in cur):
            lines.append(_make_line(list(cur)))
        cur.clear()

    for c in chars:
        if c.ws:
            if cur:
                cur.append(c)
            continue
        if last is not None:
            same = False
            if c.rot == last.rot:
                if c.rot:
                    same = math.hypot(c.ox - last.ox, c.oy - last.oy) < 3 * max(c.size, last.size)
                else:
                    # 字の大きさの帯（基線の 0.75 em 上から 0.25 em 下）どうしの重なりで同じ行かを決める。
                    # 点やハイフンのような小さい字も、上付き・下付きも同じ行に入る。
                    e0, e1 = c.oy - 0.75 * c.size, c.oy + 0.25 * c.size
                    lo, hi = max(band[2], e0), min(band[3], e1)
                    ref = max(min(e1 - e0, band[3] - band[2]), 0.1)
                    gap = c.x0 - band[1]
                    sz = max(c.size, last.size)
                    lig = abs(c.x0 - last.x0) < 0.5 and abs(c.x1 - last.x1) < 0.5   # 合字（fi など）の 2 字目
                    same = (hi - lo) >= 0.45 * ref and (c.x0 >= band[1] - 0.35 * sz or lig) and gap <= 2.5 * sz
            if not same:
                flush()
        e0, e1 = c.oy - 0.75 * c.size, c.oy + 0.25 * c.size
        if not cur or all(x.ws for x in cur):
            cur[:] = [c]
            band = [c.x0, c.x1, e0, e1]
        else:
            cur.append(c)
            band = [min(band[0], c.x0), max(band[1], c.x1), min(band[2], e0), max(band[3], e1)]
        last = c
    flush()
    return _attach_scripts(_merge_interleaved(lines))


def _attach_scripts(lines: list[Line]) -> list[Line]:
    """別に描かれた上付き・下付き（短く、小さく、隣の行の基線から上下にずれた字）を隣の行に戻す。"""
    i = 0
    while i < len(lines):
        s = lines[i]
        ink = [c for c in s.chars if not c.ws]
        host = None
        if not s.rot and len(ink) <= 6:
            smax = max(c.size for c in ink)
            for p in lines:
                if p is s or p.rot or smax > 0.95 * p.size:
                    continue
                off = abs(s.base - p.base)
                lo = 0.12 if smax <= 0.85 * p.size else 0.25
                if not (lo * p.size <= off <= 0.7 * p.size):
                    continue
                if p.x0 - 0.1 * p.size <= s.x0 <= p.x1 + 0.4 * p.size:
                    host = p
                    break
        if host is None:
            i += 1
            continue
        j = lines.index(host)
        lines[j] = _make_line(sorted(host.chars + s.chars, key=lambda c: (c.x0 + c.x1) / 2))
        del lines[i]
        i = 0 if j > i else i     # 並びが変わったので最初から見直す
    return lines


def _merge_interleaved(lines: list[Line]) -> list[Line]:
    """同じ基線の上で横に入り組んでいるのに字どうしは重ならない行を 1 行に戻す。
    ブラウザなどはフォントごとに分けて描くため、1 行が描かれる順に飛び飛びになることがある。"""
    changed = True
    while changed:
        changed = False
        for i in range(len(lines)):
            a = lines[i]
            if a.rot:
                continue
            for j in range(i + 1, len(lines)):
                b = lines[j]
                if b.rot or abs(a.base - b.base) > 0.12 * min(a.size, b.size):
                    continue
                if not (0.9 <= a.size / b.size <= 1.11):
                    continue
                if min(a.x1, b.x1) - max(a.x0, b.x0) <= 0:
                    continue
                inks_a = [c for c in a.chars if not c.ws]
                clash = any(min(p.x1, q.x1) - max(p.x0, q.x0) > 0.5 and min(p.y1, q.y1) - max(p.y0, q.y0) > 0.5
                            for p in inks_a for q in b.chars if not q.ws)
                if clash:
                    continue
                merged = sorted(a.chars + b.chars, key=lambda c: (c.x0 + c.x1) / 2)
                lines[i] = _make_line(merged)
                del lines[j]
                changed = True
                break
            if changed:
                break
    return lines


def _make_line(chars: list[Ch]) -> Line:
    while chars and chars[-1].ws:
        chars.pop()
    ln = Line()
    ln.chars = chars
    ink = [c for c in chars if not c.ws]
    ln.rot = ink[0].rot
    cnt = Counter(round(c.size, 1) for c in ink)
    ln.size = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
    dom = [c for c in ink if abs(c.size - ln.size) < 0.15]
    ln.base = sorted(c.oy for c in dom)[len(dom) // 2]
    # 上付き・下付き: 行で最も大きい字より小さく、その基線から上下にずれている字
    ln.script = set()
    if not ln.rot:
        mx = max(c.size for c in ink)
        ref = sorted(c.oy for c in ink if c.size >= mx - 0.15)
        rbase = ref[len(ref) // 2]
        for idx, c in enumerate(chars):
            off = abs(c.oy - rbase)
            if not c.ws and ((c.size <= 0.85 * mx and off >= 0.12 * mx) or (c.size <= 0.95 * mx and off >= 0.25 * mx)):
                ln.script.add(idx)
    ln.x0 = min(c.x0 for c in ink)
    ln.x1 = max(c.x1 for c in ink)
    ln.y0 = min(c.y0 for c in ink)
    ln.y1 = max(c.y1 for c in ink)
    # 重なりの判定に使う「芯」: 基線の少し下から字の高さの 7 割まで
    ln.cy0 = ln.base - 0.70 * ln.size
    ln.cy1 = ln.base + 0.20 * ln.size
    # 語ごとに置かれて空白の字が無い PDF でも読めるよう、字の間が空いていれば空白を補う
    parts, prev = [], None
    for c in chars:
        if prev is not None and not c.ws and not prev.ws and c.x0 - prev.x1 > 0.3 * ln.size:
            parts.append(" ")
        parts.append(c.t)
        prev = c
    ln.text = "".join(parts)
    ln.block = -1
    ln.footer = False
    return ln


def build_blocks(lines: list[Line]) -> list[list[Line]]:
    """大きさが揃い、縦に続き、横が重なる行をまとまりにまとめる。"""
    blocks: list[list[Line]] = []
    for ln in lines:
        if ln.rot:
            ln.block = len(blocks)
            blocks.append([ln])
            continue
        target = -1
        for bi in range(len(blocks) - 1, max(-1, len(blocks) - 40), -1):
            b = blocks[bi]
            lastl = b[-1]
            if lastl.rot or not (0.93 <= ln.size / lastl.size <= 1.08):
                continue
            gap = ln.cy0 - lastl.cy1
            if not (-1.0 <= gap <= 0.75 * max(ln.size, lastl.size)):
                continue
            bx0, bx1 = min(x.x0 for x in b), max(x.x1 for x in b)
            if min(bx1, ln.x1) - max(bx0, ln.x0) > 0 or abs(ln.x0 - lastl.x0) <= ln.size:
                target = bi
                break
        if target < 0:
            ln.block = len(blocks)
            blocks.append([ln])
        else:
            ln.block = target
            blocks[target].append(ln)
    return blocks


def image_rects(page, k: float, left: float, top: float) -> list[tuple[float, float, float, float]]:
    """画像（フォームの中の画像はフォームの範囲）の矩形を 540 pt 換算・左上原点で返す。"""
    rects = []

    def has_image(obj, depth=0):
        if depth > 6:
            return False
        n = R.FPDFFormObj_CountObjects(obj)
        for i in range(max(n, 0)):
            ch = R.FPDFFormObj_GetObject(obj, i)
            t = R.FPDFPageObj_GetType(ch)
            if t == R.FPDF_PAGEOBJ_IMAGE or (t == R.FPDF_PAGEOBJ_FORM and has_image(ch, depth + 1)):
                return True
        return False

    for obj in page.get_objects(max_depth=1):
        t = obj.type
        if t == R.FPDF_PAGEOBJ_IMAGE or (t == R.FPDF_PAGEOBJ_FORM and has_image(obj.raw)):
            try:
                l, b, r, tt = obj.get_bounds()
            except Exception:
                continue
            rects.append(((l - left) * k, (top - tt) * k, (r - left) * k, (top - b) * k))
    return rects


# ---------------------------------------------------------------- 色

def _lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hexc(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(max(0, min(255, v)))) for v in rgb)


def suggest_color(fg, bg, need: float):
    """文字色を濃く（背景が暗ければ明るく）して need を満たす最小の変更を探す。"""
    dark_bg = luminance(bg) < luminance(fg) and luminance(bg) < 0.18
    for step in range(1, 101):
        f = step / 100.0
        if dark_bg:
            c = tuple(v + (255 - v) * f for v in fg)
        else:
            c = tuple(v * (1 - f) for v in fg)
        if contrast(c, bg) >= need + 0.05:
            return hexc(c)
    return None


def bg_clusters(px: np.ndarray):
    """背景の候補の色を、多い順に（平均色, 割合）で返す。"""
    total = len(px)
    rem = px.astype(np.int32)
    out = []
    for _ in range(3):
        if len(rem) < max(8, 0.08 * total):
            break
        q = rem >> 4
        key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
        vals, counts = np.unique(key, return_counts=True)
        mean = rem[key == vals[counts.argmax()]].mean(0)
        for _ in range(2):
            inc = ((rem - mean) ** 2).sum(1) <= 40 ** 2
            mean = rem[inc].mean(0)
        out.append((tuple(float(v) for v in mean), inc.sum() / total))
        rem = rem[~inc]
    return out


# ---------------------------------------------------------------- 描画したツールの推定

def guess_renderer(meta: dict) -> tuple[str, str]:
    prod = (meta.get("Producer") or "").strip()
    crea = (meta.get("Creator") or "").strip()
    both = f"{prod} {crea}".lower()
    if "libreoffice" in both or "openoffice" in both:
        return ("LibreOffice",
                "LibreOffice で描画した PDF。元が .pptx なら、フォントの置き換えで折り返しや幅が発表当日（PowerPoint）と"
                "変わりうる。発表当日に使うツールで書き出した PDF で測り直すこと")
    if "powerpoint" in both:
        if "print to pdf" in both or "pdf printer" in both:
            return ("PowerPoint（印刷経由）", "印刷経由の PDF。可能なら［エクスポート］→ PDF で書き出した PDF を使う")
        return ("Microsoft PowerPoint", "")
    if "keynote" in both:
        return ("Keynote", "")
    if "google" in both:
        return ("Google スライド", "")
    if re.search(r"pdftex|xetex|luatex|xdvipdfmx|dvipdfm|latex|beamer", both):
        return ("LaTeX", "")
    if "chrom" in both or "skia" in both:
        return ("ブラウザ（Chromium 系）", "")
    if "quartz" in both or "mac os x" in both or "macos" in both:
        return ("macOS の PDF 書き出し", "")
    if "word" in both:
        return ("Microsoft Word", "")
    if "acrobat" in both or "distiller" in both:
        return ("Adobe Acrobat", "")
    if prod or crea:
        return ("不明", "描画したツールを推定できなかった")
    return ("不明", "PDF に Producer・Creator が無く、描画したツールを推定できなかった")


# ---------------------------------------------------------------- 検査の本体

class Opts:
    """閾値（すべて 540 pt 換算）。"""

    def __init__(self, a=None):
        g = (lambda n, d: getattr(a, n, d) if a is not None and getattr(a, n, None) is not None else d)
        self.min_body = g("min_body", 18.0)
        self.min_footer = g("min_footer", 12.0)
        self.appendix_from = g("appendix_from", None)
        self.min_appendix = g("min_appendix", 14.0)
        self.contrast_body = g("contrast_body", 7.0)
        self.contrast_large = g("contrast_large", 4.5)
        self.large_pt = g("large_pt", 24.0)
        self.large_bold_pt = g("large_bold_pt", 18.0)
        self.footer_band = g("footer_band", 0.10)
        self.title_band = g("title_band", 0.25)
        self.overlap_pt = g("overlap_pt", 1.0)
        self.title_max_lines = g("title_max_lines", 2)
        self.margin_in = g("margin_in", 0.6)
        self.margin_v_in = g("margin_v_in", 0.4)
        self.image_share = g("image_share", 0.25)
        self.size_tol = g("size_tol", 0.1)

    def as_dict(self):
        return {
            "basis": "高さ 7.5 in（540 pt）のスライドでの pt",
            "G1_min_body_pt": self.min_body, "G1_min_footer_pt": self.min_footer,
            "G1_appendix_from_page": self.appendix_from, "G1_min_appendix_pt": self.min_appendix,
            "G1_size_tolerance_pt": self.size_tol,
            "G2_contrast_body": self.contrast_body, "G2_contrast_large_or_footer": self.contrast_large,
            "G2_large_pt": self.large_pt, "G2_large_bold_pt": self.large_bold_pt,
            "G3_outside_tolerance_pt": 0.5,
            "G5_overlap_pt": self.overlap_pt,
            "G6_title_max_lines": self.title_max_lines, "G6_title_band": self.title_band,
            "footer_band": self.footer_band,
            "I1_margin_in": self.margin_in, "I1_margin_v_in": self.margin_v_in, "I6_image_share": self.image_share,
        }


def head(text: str, n: int = 20) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n] + "…"


def el(text: str, x: float, y: float) -> str:
    return f"「{head(text)}」(x {x:.0f}, y {y:.0f} pt)"


def analyze_page(page, pno: int, o: Opts, shots_dir=None, ndigits=2):
    left, bottom, right, top = page.get_bbox()
    w_pdf, h_pdf = right - left, top - bottom
    k = BASIS_H / h_pdf
    W, H = w_pdf * k, BASIS_H
    gates, infos = [], []

    chars = extract_chars(page, k, left, top)
    lines = build_lines(chars)
    blocks = build_blocks(lines)
    imgs = image_rects(page, k, left, top)
    foot_y = H * (1 - o.footer_band)
    for ln in lines:
        ln.footer = (ln.y0 + ln.y1) / 2 >= foot_y

    # ページを描く（背景の色、--shots）
    scale = RENDER_PX_PER_PT * k
    bmp = page.render(scale=scale, rev_byteorder=True)
    img = bmp.to_numpy()
    if img.shape[2] == 4:
        img = img[:, :, :3]
    img = np.ascontiguousarray(img)
    PH, PW = img.shape[:2]
    if shots_dir:
        write_png(os.path.join(shots_dir, f"{pno:0{ndigits}d}.png"), img)

    def px(v):
        return v * RENDER_PX_PER_PT

    # 文字の画素の覆い（背景の画素を取るときに除く）
    mask = np.zeros((PH, PW), dtype=bool)
    for c in chars:
        if c.ws:
            continue
        x0, x1 = int(px(c.x0)) - 2, int(math.ceil(px(c.x1))) + 2
        y0, y1 = int(px(c.y0)) - 2, int(math.ceil(px(c.y1))) + 2
        mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True

    appendix = o.appendix_from is not None and pno >= o.appendix_from

    # ---- G1 文字の大きさ
    for ln in lines:
        thr = o.min_footer if ln.footer else (o.min_appendix if appendix else o.min_body)
        role = "脚注の領域" if ln.footer else ("付録" if appendix else "本文")
        main = [c for i, c in enumerate(ln.chars) if not c.ws and i not in ln.script]
        scr = [c for i, c in enumerate(ln.chars) if not c.ws and i in ln.script]
        if main:
            mn = min(c.size for c in main)
            if mn < thr - o.size_tol:
                gates.append({"id": "G1", "element": el(ln.text, ln.x0, ln.y0), "measured": round(mn, 1),
                              "threshold": thr,
                              "hint": f"{role}の文字を {thr:g} pt 以上に上げる（入らなければ文字を減らす・分ける）"})
        if scr:
            mn = min(c.size for c in scr)
            if mn < thr - o.size_tol:
                infos.append({"id": "G1", "element": el(ln.text, ln.x0, ln.y0), "measured": round(mn, 1),
                              "threshold": thr,
                              "note": "上付き・下付きと推定したので違反から外した（描かれた大きさで測った値）"})

    # ---- G2 コントラスト比
    cmin = {"本文": 99.0, "大きい文字": 99.0, "脚注の領域": 99.0}
    for ln in lines:
        spans, cur = [], []    # 同じ塗りの色が続く字の番号の並び
        for idx, c in enumerate(ln.chars):
            if c.ws:
                continue
            if cur and (c.rgb != ln.chars[cur[-1]].rgb or abs(c.alpha - ln.chars[cur[-1]].alpha) > 0.01):
                spans.append(cur)
                cur = []
            cur.append(idx)
        if cur:
            spans.append(cur)
        for ids in spans:
            sp = [ln.chars[i] for i in ids]
            text = "".join(ln.chars[i].t for i in range(ids[0], ids[-1] + 1))
            size = min((ln.chars[i].size for i in ids if i not in ln.script), default=min(c.size for c in sp))
            bold = all(c.bold for c in sp)
            large = size >= o.large_pt - o.size_tol or (bold and size >= o.large_bold_pt - o.size_tol)
            thr = o.contrast_large if (large or ln.footer) else o.contrast_body
            sx0, sx1 = min(c.x0 for c in sp), max(c.x1 for c in sp)
            sy0, sy1 = min(c.y0 for c in sp), max(c.y1 for c in sp)
            if sx1 < 0 or sy1 < 0 or sx0 > W or sy0 > H:
                continue
            pad = max(3.0, 0.3 * size)
            pix = None
            for mult in (1, 2, 4):
                X0 = max(0, int(px(sx0 - pad * mult)))
                X1 = min(PW, int(math.ceil(px(sx1 + pad * mult))))
                Y0 = max(0, int(px(sy0 - pad * mult)))
                Y1 = min(PH, int(math.ceil(px(sy1 + pad * mult))))
                if X1 <= X0 or Y1 <= Y0:
                    break
                sel = ~mask[Y0:Y1, X0:X1]
                if sel.sum() >= 40:
                    pix = img[Y0:Y1, X0:X1][sel]
                    break
            if pix is None:
                infos.append({"id": "G2", "element": el(text, sx0, sy0), "undetermined": True,
                              "note": "周りに背景の画素が取れず、判定できない"})
                continue
            cl = bg_clusters(pix)
            fg0 = sp[0].rgb
            alpha = sp[0].alpha

            def fg_on(bg):
                return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg0, bg))

            # 描かれた色が塗りの色と合うか（他の物の下に隠れた文字、模様の塗りなどを除く）
            gx0, gx1 = max(0, int(px(sx0))), min(PW, int(math.ceil(px(sx1))))
            gy0, gy1 = max(0, int(px(sy0))), min(PH, int(math.ceil(px(sy1))))
            glyph = img[gy0:gy1, gx0:gx1].reshape(-1, 3).astype(np.int32)
            exp = np.array(fg_on(cl[0][0]))
            hit = int((((glyph - exp) ** 2).sum(1) <= 60 ** 2).sum()) if len(glyph) else 0
            if len(glyph) and hit < max(3, 0.01 * len(glyph)) and contrast(fg_on(cl[0][0]), cl[0][0]) > 1.3:
                infos.append({"id": "G2", "element": el(text, sx0, sy0), "undetermined": True,
                              "note": f"塗りの色 {hexc(fg0)} が描かれた画素に見当たらない（他の物の下にある、"
                                      f"模様や階調の塗り、など）。判定できない。ページの画像で確認する"})
                continue
            bg0, share0 = cl[0]
            if share0 >= 0.6:
                ratio = contrast(fg_on(bg0), bg0)
                role = "脚注の領域" if ln.footer else ("大きい文字" if large else "本文")
                cmin[role] = min(cmin[role], ratio)
                if ratio < thr:
                    sug = suggest_color(fg_on(bg0), bg0, thr)
                    hint = (f"文字色 {hexc(fg0)} を {sug} 以上に濃く（背景 {hexc(bg0)}）" if sug and luminance(bg0) >= luminance(fg_on(bg0))
                            else f"文字色 {hexc(fg0)} を {sug} 以上に明るく（背景 {hexc(bg0)}）" if sug
                            else f"背景 {hexc(bg0)} か文字色 {hexc(fg0)} を変える")
                    gates.append({"id": "G2", "element": el(text, sx0, sy0), "measured": round(ratio, 2),
                                  "threshold": thr, "hint": hint})
                continue
            sig = [(c, s) for c, s in cl if s >= 0.15] or [cl[0]]
            ratios = [contrast(fg_on(c), c) for c, s in sig]
            if min(ratios) >= thr:
                continue
            if max(ratios) < thr:
                gates.append({"id": "G2", "element": el(text, sx0, sy0), "measured": round(max(ratios), 2),
                              "threshold": thr,
                              "hint": f"背景が一色でない（{', '.join(hexc(c) for c, s in sig)}）が、どの色に対しても足りない。"
                                      f"文字の下に無地の領域を敷くか文字色を変える"})
            else:
                infos.append({"id": "G2", "element": el(text, sx0, sy0), "undetermined": True,
                              "measured": [round(min(ratios), 2), round(max(ratios), 2)], "threshold": thr,
                              "note": f"背景が一色に決まらない（写真・模様・境目。{', '.join(hexc(c) for c, s in sig)}）。"
                                      f"判定できない。ページの画像で確認し、必要なら文字の下に無地の領域を敷く"})

    # ---- G3 スライドからのはみ出し
    for ln in lines:
        worst, side = 0.0, ""
        for c in ln.chars:
            if c.ws:
                continue
            for d, s in ((-c.x0, "左"), (c.x1 - W, "右"), (-c.y0, "上"), (c.y1 - H, "下")):
                if d > worst:
                    worst, side = d, s
        if worst > 0.5:
            gates.append({"id": "G3", "element": el(ln.text, ln.x0, ln.y0), "measured": round(worst, 1),
                          "threshold": 0.0,
                          "hint": f"ページの{side}へ {worst:.1f} pt はみ出している。内側へ移すか文字を短くする"})

    # ---- G5 文字どうしの重なり
    flat = [ln for ln in lines if not ln.rot]
    for i in range(len(flat)):
        a = flat[i]
        for j in range(i + 1, len(flat)):
            b = flat[j]
            if a.block == b.block:
                continue
            w = min(a.x1, b.x1) - max(a.x0, b.x0)
            h = min(a.cy1, b.cy1) - max(a.cy0, b.cy0)
            if w <= o.overlap_pt or h <= o.overlap_pt:
                continue
            if a.text.strip() == b.text.strip():     # 影などの二重描き
                continue
            # 同じ基線で横に接しているだけ（別々に描かれた語のつなぎ目）は重なりとしない
            if abs(a.base - b.base) < 0.15 * min(a.size, b.size) and w < 0.3 * min(a.size, b.size):
                continue
            gates.append({"id": "G5", "element": el(a.text, a.x0, a.y0) + " と " + el(b.text, b.x0, b.y0),
                          "measured": round(min(w, h), 1), "threshold": o.overlap_pt,
                          "hint": "2 つの文字のまとまりが重なっている。片方を動かすか、文字を減らして枠に収める"})

    # ---- G6 見出しの行数
    title_info = None
    flat_blocks = [b for b in blocks if not b[0].rot and not any(x.footer for x in b)]
    cands = [b for b in flat_blocks if min(x.y0 for x in b) < H * o.title_band]
    if cands:
        def bsize(b):
            return max(x.size for x in b)
        best = max(cands, key=bsize)
        bs = bsize(best)
        others = [x.size for b in flat_blocks if b is not best for x in b]
        doubts = []
        if any(b is not best and bsize(b) >= 0.95 * bs for b in cands):
            doubts.append("上端の領域に同じくらいの大きさのまとまりが他にもある")
        if any(s > bs * 1.02 for s in others):
            doubts.append("上端の領域の外にもっと大きい文字がある")
        if others and bs < 1.15 * sorted(others)[len(others) // 2]:
            doubts.append("本文との大きさの差が小さい")
        nlines = len(best)
        title_info = {"lines": nlines, "size": round(bs, 1), "text": head(" ".join(x.text for x in best)),
                      "doubt": doubts}
        if nlines > o.title_max_lines:
            item = {"id": "G6", "element": el(" ".join(x.text for x in best), best[0].x0, best[0].y0),
                    "measured": nlines, "threshold": o.title_max_lines}
            if doubts:
                item["note"] = f"見出しと推定したまとまりが {nlines} 行だが、推定の確度が低い（{'、'.join(doubts)}）。ページの画像で確認する"
                infos.append(item)
            else:
                item["hint"] = f"見出しを {o.title_max_lines} 行以内に縮める（言い換える・語を削る・文字幅を見直す）"
                gates.append(item)

    # ---- I1 余白
    mh, mv = o.margin_in * 72.0, o.margin_v_in * 72.0   # 左右と上下の余白（pt）
    footer_lines = []
    for ln in lines:
        if ln.footer:
            footer_lines.append(ln)
            continue
        d = {"左": (mh - ln.x0, mh), "右": (ln.x1 - (W - mh), mh), "上": (mv - ln.y0, mv), "下": (ln.y1 - (H - mv), mv)}
        side, (over, mg) = max(d.items(), key=lambda kv: kv[1][0] / max(kv[1][1], 1e-6))
        if over > 0.5:
            infos.append({"id": "I1", "element": el(ln.text, ln.x0, ln.y0), "measured": round((mg - over) / 72, 2),
                          "threshold": round(mg / 72, 2), "heavy": over >= mg / 2,
                          "note": (f"{side}端から {(mg - over) / 72:.2f} in（余白 {mg / 72:g} in より外）" if over < mg
                                   else f"{side}端を越えてページの外に出ている（G3 を参照）")})

    # ---- I2 行
    for b in blocks:
        if len(b) < 2 or b[0].rot:
            continue
        lastl = b[-1]
        ink = [c for c in lastl.chars if not c.ws]
        if len(ink) <= 2:
            infos.append({"id": "I2", "element": el(lastl.text, lastl.x0, lastl.y0), "measured": len(ink),
                          "heavy": True, "note": f"まとまりの最後の行が {len(ink)} 文字だけ。前の行に収まるよう語を削るか枠を広げる"})
        for ln in b[:-1]:
            if ln.text.rstrip().endswith(("-", "‐", "–", "­")):
                infos.append({"id": "I2", "kind": "hyphen", "element": el(ln.text, ln.x0, ln.y0), "heavy": False,
                              "note": "行末がハイフンか en ダッシュ。語の途中で折り返していないか確認する"})

    # ---- I3 内容の重心
    body_boxes = [(ln.x0, ln.y0, ln.x1, ln.y1) for ln in lines if not ln.footer]
    for r in imgs:
        x0, y0, x1, y1 = max(0, r[0]), max(0, r[1]), min(W, r[2]), min(H, r[3])
        if x1 > x0 and y1 > y0 and (x1 - x0) * (y1 - y0) < 0.9 * W * H:
            body_boxes.append((x0, y0, x1, y1))
    i3 = None
    if body_boxes:
        bx0 = max(0.0, min(b[0] for b in body_boxes))
        by0 = max(0.0, min(b[1] for b in body_boxes))
        bx1 = min(W, max(b[2] for b in body_boxes))
        by1 = min(H, max(b[3] for b in body_boxes))
        free = {"top": by0, "bottom": H - by1, "left": bx0, "right": W - bx1}
        heavy = abs(free["left"] - free["right"]) > 72 or abs(free["top"] - free["bottom"]) > 108
        i3 = {"id": "I3", "element": f"内容の外接矩形 (x {bx0:.0f}–{bx1:.0f}, y {by0:.0f}–{by1:.0f} pt)",
              "measured": {kk: round(v / 72, 2) for kk, v in free.items()}, "heavy": heavy,
              "note": "空き（in）: 上 {top:.2f} 下 {bottom:.2f} 左 {left:.2f} 右 {right:.2f}".format(
                  **{kk: v / 72 for kk, v in free.items()}) + ("。偏りが大きい" if heavy else "")}
        infos.append(i3)

    # ---- I4 色
    colors = Counter(hexc(c.rgb) for c in chars if not c.ws)
    if colors:
        infos.append({"id": "I4", "element": "文字色", "measured": dict(colors.most_common()), "heavy": False,
                      "note": "、".join(f"{c} {n} 字" for c, n in colors.most_common(6))})

    # ---- I6 測れなかったもの（画像の割合）
    gw, gh = 192, 108
    grid = np.zeros((gh, gw), dtype=bool)
    full_bg = False
    for r in imgs:
        x0, y0, x1, y1 = max(0, r[0]), max(0, r[1]), min(W, r[2]), min(H, r[3])
        if x1 <= x0 or y1 <= y0:
            continue
        if (x1 - x0) * (y1 - y0) >= 0.9 * W * H:
            full_bg = True
        grid[int(y0 / H * gh):int(math.ceil(y1 / H * gh)), int(x0 / W * gw):int(math.ceil(x1 / W * gw))] = True
    share = float(grid.mean())
    if share >= o.image_share:
        note = (f"画像がスライドの {share * 100:.0f}% を占める。このページの図の中の文字は測れないので、図を作る側で"
                f"大きさ（{o.min_body:g} pt 相当以上）とコントラストを確認する")
        if full_bg:
            note += "（スライド全体を覆う画像がある。背景の画像なら問題ない）"
        infos.append({"id": "I6", "element": f"画像 {len(imgs)} 個", "measured": round(share, 2),
                      "threshold": o.image_share, "heavy": share >= 0.4 and not full_bg, "note": note})

    nink = sum(1 for c in chars if not c.ws)
    kind = ("画像だけ" if share >= o.image_share and nink < 30 else
            "画像と文字" if share >= 0.15 else "文字だけ")
    # HTML の検査と同じ形に揃える（gates に unit、判定できないものは undetermined へ、説明は detail）
    for g in gates:
        g["unit"] = UNITS[g["id"]]
    und, rest = [], []
    for i in infos:
        if "note" in i:
            i["detail"] = i.pop("note")
        i.setdefault("kind", KINDS.get(i["id"], ""))
        if i.pop("undetermined", False):
            und.append({"id": i["id"], "element": i["element"], "reason": i["detail"], "measured": i.get("measured"),
                        "threshold": i.get("threshold"), "hint": "ページの画像で背景と文字の差を確認する"})
        else:
            rest.append(i)
    page_info = {"index": pno - 1, "id": pno, "gates": gates, "undetermined": und, "infos": rest,
                 "_kind": kind, "_footer": [(ln.x0, ln.x1, ln.y1, ln.text) for ln in footer_lines],
                 "_title": title_info, "_size": (W, H), "_k": k, "_chars": nink,
                 "_colors": colors, "_cmin": cmin, "_lines": len(lines), "_blocks": len(blocks)}
    bmp.close()
    return page_info


UNITS = {"G1": "pt", "G2": ":1", "G3": "pt", "G5": "pt", "G6": "行"}
KINDS = {"G1": "script", "G6": "headline-doubt", "I1": "margin", "I2": "orphan", "I3": "balance", "I4": "colors",
         "I6": "image-text"}


def deck_infos(slides: list[dict]) -> list[dict]:
    """ページをまたぐ参考情報。脚注の領域の位置の揺れは各ページの infos に追加し、色の合計と単調さは返す。"""
    n = len(slides)
    out = []
    # 脚注の領域: 左端・右端・基線の位置（4 pt 刻み）が 3 割以上のページに共通するものを「決まった位置」とする
    keys = Counter()
    for s in slides:
        ks = set()
        for x0, x1, yb, t in s["_footer"]:
            ks.add(("L", round(x0 / 4), round(yb / 4)))
            ks.add(("R", round(x1 / 4), round(yb / 4)))
            ks.add(("C", round((x0 + x1) / 8), round(yb / 4)))
        keys.update(ks)
    common = {kk for kk, c in keys.items() if c >= max(2, 0.3 * n)}
    if common:
        for s in slides:
            for x0, x1, yb, t in s["_footer"]:
                mine = {("L", round(x0 / 4), round(yb / 4)), ("R", round(x1 / 4), round(yb / 4)),
                        ("C", round((x0 + x1) / 8), round(yb / 4))}
                if not mine & common:
                    s["infos"].append({"id": "I1", "kind": "footnote-position", "element": el(t, x0, yb), "heavy": True,
                                       "detail": "脚注の領域の文字の位置が他のページと揃っていない"})
    # 単調さ
    i = 0
    while i < n:
        j = i
        while j + 1 < n and slides[j + 1]["_kind"] == slides[i]["_kind"]:
            j += 1
        run = j - i + 1
        if run >= 3:
            out.append({"id": "I5", "kind": "layout-runs", "element": f"ページ {slides[i]['id']}–{slides[j]['id']}",
                        "measured": run, "heavy": run >= 5,
                        "detail": f"「{slides[i]['_kind']}」の構成が {run} ページ続く"})
        i = j + 1
    tot = Counter()
    for s in slides:
        tot.update(s["_colors"])
    out.append({"id": "I4", "kind": "colors", "element": "文字色（全ページ）", "colors": dict(tot.most_common()),
                "count": len(tot), "heavy": False,
                "detail": f"文字色 {len(tot)} 種 — " + "、".join(f"{c} {k} 字" for c, k in tot.most_common(8))})
    return out


def parse_pages(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(1, n + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return [p for p in dict.fromkeys(out) if 1 <= p <= n]


def run_check(src, o: Opts, pages_spec=None, shots_dir=None, input_name=None):
    doc = pdfium.PdfDocument(src)
    try:
        n = len(doc)
        meta = doc.get_metadata_dict()
        renderer, rnote = guess_renderer(meta)
        pages = parse_pages(pages_spec, n)
        if shots_dir:
            os.makedirs(shots_dir, exist_ok=True)
        ndig = max(2, len(str(n)))
        slides, warnings = [], []
        if rnote:
            warnings.append({"id": "W-renderer", "detail": rnote})
        sizes = set()
        for p in pages:
            page = doc[p - 1]
            if page.get_rotation():
                warnings.append({"id": "W-rotation",
                                 "detail": f"ページ {p} は回転の指定がある（{page.get_rotation()} 度）。座標が合わない可能性がある"})
            s = analyze_page(page, p, o, shots_dir, ndig)
            sizes.add(tuple(round(v / s["_k"], 1) for v in s["_size"]))
            slides.append(s)
            page.close()
        dinfos = deck_infos(slides)
        if len(sizes) > 1:
            warnings.append({"id": "W-size", "detail": f"ページの大きさが揃っていない: {sorted(sizes)}"})
        if slides and all(s["_chars"] == 0 for s in slides):
            warnings.append({"id": "W-notext", "detail": "文字が 1 つも読めない。文字が図形（アウトライン）か画像になっている。"
                             "書き出しの設定で文字を文字のまま残すか、ページの画像で確認する"})
        first = slides[0] if slides else None
        canvas = {}
        if first:
            W, H = first["_size"]
            canvas = {"width_pt": round(W / first["_k"], 2), "height_pt": round(H / first["_k"], 2),
                      "scale_to_basis": round(first["_k"], 4), "basis_height_pt": BASIS_H,
                      "width_in_basis": round(W / 72, 3), "height_in_basis": 7.5}
        result = {
            "tool": TOOL, "version": VERSION,
            "input": {"path": input_name or (src if isinstance(src, str) else "<memory>"), "pages_total": n,
                      "pages_checked": pages, "producer": meta.get("Producer", ""),
                      "creator": meta.get("Creator", ""), "renderer": renderer},
            "canvas": canvas, "thresholds": o.as_dict(),
            "summary": {"gates": sum(len(s["gates"]) for s in slides),
                        "infos": sum(len(s["infos"]) for s in slides) + len(dinfos),
                        "warnings": len(warnings),
                        "undetermined": sum(len(s["undetermined"]) for s in slides),
                        "byGate": dict(sorted(Counter(g["id"] for s in slides for g in s["gates"]).items()))},
            "warnings": warnings,
            "slides": slides,
            "deck": {"infos": dinfos},
        }
        return result
    finally:
        doc.close()


def public(result: dict) -> dict:
    out = dict(result)
    out["slides"] = [{k: v for k, v in s.items() if not k.startswith("_")} for s in result["slides"]]
    return out


# ---------------------------------------------------------------- 出力

def report_text(res: dict, verbose: bool = False) -> str:
    L = []
    s = res["summary"]
    inp = res["input"]
    cv = res["canvas"]
    gate_cnt = Counter(g["id"] for sl in res["slides"] for g in sl["gates"])
    und = [(sl["id"], i) for sl in res["slides"] for i in sl["undetermined"]]
    L.append(f"違反: {s['gates']} 件" + (f"（{'、'.join(f'{k} {v}' for k, v in sorted(gate_cnt.items()))}）" if gate_cnt else "")
             + f" / 判定できない: {len(und)} 件 / 参考情報: {s['infos']} 件")
    L.append(f"描画したツールの推定: {inp['renderer']}（Producer: {inp['producer'] or 'なし'}）")
    if cv:
        L.append(f"入力: {os.path.basename(str(inp['path']))}（{inp['pages_total']} ページ中 {len(inp['pages_checked'])} ページを検査、"
                 f"{cv['width_pt']:g}×{cv['height_pt']:g} pt。高さ 540 pt に換算する係数 {cv['scale_to_basis']:g}）")
    for w in res["warnings"]:
        L.append(f"[注意] {w['detail']}")
    cm = {}
    for sl in res["slides"]:
        for kk, v in sl.get("_cmin", {}).items():
            cm[kk] = min(cm.get(kk, 99.0), v)
    cm = {kk: v for kk, v in cm.items() if v < 99}
    if cm:
        L.append("コントラスト比の最小（判定できた字）: " + " / ".join(f"{kk} {v:.2f}:1" for kk, v in cm.items()))
    L.append("")
    L.append("== 違反（ページごと。位置は 540 pt 換算・左上から）")
    for sl in res["slides"]:
        gs = sl["gates"]
        if not gs:
            L.append(f"p{sl['id']}: 違反なし")
            continue
        L.append(f"p{sl['id']}: 違反 {len(gs)} 件")
        per = Counter()
        for g in gs:
            per[g["id"]] += 1
            if not verbose and per[g["id"]] > 8:
                continue
            unit = {"G1": " pt", "G2": ":1", "G3": " pt", "G5": " pt", "G6": " 行"}.get(g["id"], "")
            rel = {"G1": "<", "G2": "<", "G3": ">", "G5": ">", "G6": ">"}.get(g["id"], "vs")
            L.append(f"  {g['id']} {g['element']}: {g['measured']}{unit} {rel} {g['threshold']:g}{unit} — {g['hint']}")
        for gid, c in per.items():
            if not verbose and c > 8:
                L.append(f"  {gid} ほか {c - 8} 件（--verbose か --json で全件）")
    if und:
        L.append("")
        L.append(f"== 判定できない（{len(und)} 件。違反には数えていない）")
        for pid, i in (und if verbose else und[:10]):
            L.append(f"  p{pid} {i['id']} {i['element']}: {i['reason']}")
        if not verbose and len(und) > 10:
            L.append(f"  ほか {len(und) - 10} 件（--verbose で全件）")
    L.append("")
    L.append("== 参考情報" + ("（全件）" if verbose else "（件数と重いもの。全件は --verbose）"))
    names = {"G1": "上付き・下付き（違反から除外）", "G6": "見出しの行数（推定の確度が低いもの）", "I1": "余白",
             "I2": "行", "I3": "内容の重心", "I4": "色", "I5": "単調さ", "I6": "測れなかったもの（画像）"}
    for iid in ("G1", "G6", "I1", "I2", "I3", "I4", "I5", "I6"):
        items = [(sl["id"], i) for sl in res["slides"] for i in sl["infos"] if i["id"] == iid]
        items += [("全体", i) for i in res["deck"]["infos"] if i["id"] == iid and i["kind"] != "colors"]
        if iid == "I4":
            for i in res["deck"]["infos"]:
                if i["kind"] == "colors":
                    L.append(f"I4 色: {i['detail']}")
            if verbose:
                for pid, i in items:
                    L.append(f"  p{pid}: {i['detail']}")
            continue
        if iid == "I3":
            hv = [(p, i) for p, i in items if i.get("heavy")]
            L.append(f"I3 内容の重心: {len(items)} ページを測定、偏りが大きいページ {len(hv)}")
            for pid, i in (items if verbose else hv[:5]):
                L.append(f"  p{pid}: {i['detail']}")
            continue
        if not items:
            continue
        hv = [(p, i) for p, i in items if i.get("heavy", True)]
        L.append(f"{iid} {names[iid]}: {len(items)} 件" + (f"（重いもの {len(hv)} 件）" if iid in ("I1", "I2", "I5", "I6") else ""))
        show = items if verbose else hv[:6]
        for pid, i in show:
            L.append(f"  {'' if pid == '全体' else 'p'}{pid} {i['element']}: {i.get('detail', '')}")
        if not verbose and len(hv) > 6:
            L.append(f"  ほか {len(hv) - 6} 件")
    if verbose:
        L.append("")
        L.append("== 見出しと推定したまとまり")
        for sl in res["slides"]:
            t = sl.get("_title")
            if t:
                L.append(f"  p{sl['id']}: {t['lines']} 行、{t['size']} pt「{t['text']}」" + (f"（確度が低い: {'、'.join(t['doubt'])}）" if t["doubt"] else ""))
    L.append("")
    L.append("次にすること: この検査は大きさ・色・位置のうち数で測れるものだけを測り、見た目の良し悪しは判定していない。"
             "--shots で書き出したページの画像を 1 枚ずつ開き、図の中の文字、位置の揃い方、強調の効果、判定できなかった箇所を目視で確認する。")
    return "\n".join(L)


def write_png(path: str, arr: np.ndarray):
    h, w = arr.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), arr.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- PowerPoint からの書き出し

NEXT_STEP = ("PowerPoint（またはお使いのツール）で PDF に保存し、そのファイルを渡してください: "
             "python check_pdf.py <保存した.pdf>")


def export_pptx(pptx: str, out_pdf: str | None, include_hidden: bool) -> str | None:
    if platform.system() != "Windows":
        print(f"この環境では PowerPoint に書き出させられません（Windows 専用）。{NEXT_STEP}")
        return None
    ps1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_pdf.ps1")
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell or not os.path.exists(ps1):
        print(f"PowerShell か export_pdf.ps1 が見つかりません。{NEXT_STEP}")
        return None
    if not out_pdf:
        out_pdf = os.path.join(tempfile.mkdtemp(prefix="check_pdf_"),
                               os.path.splitext(os.path.basename(pptx))[0] + ".pdf")
    cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Path", os.path.abspath(pptx),
           "-OutPdf", os.path.abspath(out_pdf)]
    if include_hidden:
        cmd.append("-IncludeHidden")
    p = subprocess.run(cmd, capture_output=True)
    msg = (p.stdout or b"").decode("utf-8", "replace").strip()
    if msg:
        print(msg)
    if p.returncode == 0 and os.path.exists(out_pdf):
        return out_pdf
    if p.returncode == 2:
        print(NEXT_STEP)
    elif p.returncode != 3:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        if err:
            print(err[-800:])
        print(NEXT_STEP)
    return None


# ---------------------------------------------------------------- 自己テスト

def _pdf_bytes(pages: list[tuple[float, float, bytes]]) -> bytes:
    """小さな PDF を合成する。各ページは (幅, 高さ, 内容のストリーム)。F1=Helvetica, F2=Helvetica-Bold, Im1=縞の画像。"""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")  # 1: Catalog
    add(b"")  # 2: Pages
    f1 = add(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    f2 = add(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>")
    stripes = bytes(([0] * 3 * 8 + [255] * 3 * 8) * 4 * 64)
    im = add(b"<</Type/XObject/Subtype/Image/Width 64/Height 64/ColorSpace/DeviceRGB/BitsPerComponent 8/Length %d>>stream\n"
             % len(stripes) + stripes + b"\nendstream")
    kids = []
    for w, h, content in pages:
        cs = add(b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream")
        kids.append(add(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 %g %g]/Resources<</Font<</F1 %d 0 R/F2 %d 0 R>>"
                        b"/XObject<</Im1 %d 0 R>>>>/Contents %d 0 R>>" % (w, h, f1, f2, im, cs)))
    objs[0] = b"<</Type/Catalog/Pages 2 0 R>>"
    objs[1] = b"<</Type/Pages/Kids[%s]/Count %d>>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % v for v in offs)
    out += b"trailer\n<</Size %d/Root 1 0 R/Info<<>>>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def _txt(x, y, size, s, rgb=(0.1, 0.1, 0.1), font="F1", rise=0):
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return (f"BT /{font} {size:g} Tf {rgb[0]:g} {rgb[1]:g} {rgb[2]:g} rg {rise:g} Ts 1 0 0 1 {x:g} {y:g} Tm ({s}) Tj ET\n").encode()


def _page(title_lines=("Section title",), body=(), extra=b"", w=960, h=540, f=1.0, bg=(1, 1, 1)):
    c = b"q %g %g %g rg 0 0 %g %g re f Q\n" % (bg[0], bg[1], bg[2], w, h)
    y = h - 70 * f
    for t in title_lines:
        c += _txt(60 * f, y, 32 * f, t, font="F2")
        y -= 38 * f
    for (x, yy, size, s, rgb) in body:
        c += _txt(x * f, yy * f, size * f, s, rgb)
    c += extra
    c += _txt(880 * f, 18 * f, 12 * f, "7", (0.25, 0.25, 0.25))
    return (w, h, c)


def selftest() -> int:
    B = (0.1, 0.1, 0.1)
    body = [(60, 300, 20, "Body text set at twenty points", B)]
    cases = {
        # 高さ 1080 pt のページで、40 pt（= 540 換算 20 pt）は合格、28 pt（= 14 pt）は違反
        "G1": (_page(body=[(60, 300, 20, "Body text at twenty", B), (60, 240, 14, "Small text at fourteen", B)],
                     w=1920, h=1080, f=2.0),
               _page(body=[(60, 300, 20, "Body text at twenty", B)], w=1920, h=1080, f=2.0,
                     extra=b"BT /F1 40 Tf 0.1 0.1 0.1 rg 1 0 0 1 120 480 Tm (Energy E = mc) Tj /F1 26 Tf 16 Ts (2) Tj ET\n")),
        "G2": (_page(body=[(60, 300, 20, "Pale gray text on white", (0.56, 0.56, 0.56))]),
               _page(body=body,
                     extra=b"q 0.12 0.2 0.35 rg 50 140 500 50 re f Q\n" + _txt(60, 155, 20, "White text on navy", (1, 1, 1))
                     + b"q 300 0 0 120 600 120 cm /Im1 Do Q\n" + _txt(610, 170, 20, "Over stripes", (0, 0, 0.5)))),
        "G3": (_page(body=[(60, 300, 20, "Body text set at twenty points", B), (840, 200, 20, "Runs off the edge", B)]),
               _page(body=[(60, 300, 20, "Body text set at twenty points", B), (60, 200, 20, "Stays inside", B)])),
        "G5": (_page(body=[(60, 300, 20, "First box text line", B), (90, 290, 20, "Second box overlaps", B)]),
               _page(body=[(60, 300, 20, "First box text line", B), (60, 250, 20, "Second box clear", B)])),
        "G6": (_page(title_lines=("A title that wraps", "onto a second line", "and then a third"), body=body),
               _page(title_lines=("A title that wraps", "onto a second line"), body=body)),
    }
    pages = []
    for bad, good in cases.values():
        pages += [bad, good]
    pdf = _pdf_bytes(pages)
    res = run_check(pdf, Opts(), input_name="<selftest>")
    ok_all = True
    for n, gid in enumerate(cases):
        bad, good = res["slides"][2 * n], res["slides"][2 * n + 1]
        bad_ids = [g["id"] for g in bad["gates"]]
        good_ids = [g["id"] for g in good["gates"]]
        ok = bad_ids == [gid] and not good_ids
        ok_all &= ok
        print(f"{gid}: {'PASS' if ok else 'FAIL'}  違反のページ → {bad_ids or 'なし'} / 違反のないページ → {good_ids or 'なし'}")
        if not ok:
            for g in bad["gates"] + good["gates"]:
                print(f"    {g['id']} {g['element']} {g['measured']} {g.get('hint', '')}")
    # 上付きが参考情報に回ったか、縞の上の文字が「判定できない」になったか
    g1 = res["slides"][1]["infos"]
    sup = any(i["id"] == "G1" for i in g1)
    und = any(i["id"] == "G2" for i in res["slides"][3]["undetermined"])
    print(f"上付きを違反から外して参考情報へ: {'PASS' if sup else 'FAIL'}")
    print(f"縞の上の文字を「判定できない」へ: {'PASS' if und else 'FAIL'}")
    ok_all &= sup and und
    print("自己テスト: " + ("すべて PASS" if ok_all else "FAIL あり"))
    return 0 if ok_all else 1


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="スライドの PDF から可読性の基準を検査する（違反 G1 G2 G3 G5 G6、参考 I1〜I6）")
    ap.add_argument("pdf", nargs="?", help="検査する PDF")
    ap.add_argument("--pptx", help="Windows で PowerPoint があれば、この .pptx を PDF に書き出してから測る")
    ap.add_argument("--pdf-out", help="--pptx で書き出す PDF の保存先（既定は一時フォルダ）")
    ap.add_argument("--include-hidden", action="store_true", help="--pptx で非表示のスライドも書き出す")
    ap.add_argument("--pages", help="検査するページ（例: 1-14,20）")
    ap.add_argument("--shots", help="各ページの PNG（NN.png）を保存するフォルダ")
    ap.add_argument("--json", help="全結果を書く JSON のファイル")
    ap.add_argument("--verbose", action="store_true", help="参考情報を全部出す")
    ap.add_argument("--selftest", action="store_true", help="合成した PDF で検出を確認する")
    ap.add_argument("--min-body", "--body-pt", dest="min_body", type=float, help="G1 本文の最小（pt、既定 18）")
    ap.add_argument("--min-footer", "--footnote-pt", dest="min_footer", type=float, help="G1 脚注の領域の最小（pt、既定 12）")
    ap.add_argument("--appendix-from", type=int, help="このページ以降を付録として --min-appendix で測る")
    ap.add_argument("--min-appendix", type=float, help="G1 付録の最小（pt、既定 14）")
    ap.add_argument("--contrast-body", type=float, help="G2 本文の最小コントラスト比（既定 7.0）")
    ap.add_argument("--contrast-large", type=float, help="G2 大きい文字・脚注の最小（既定 4.5）")
    ap.add_argument("--large-pt", type=float, help="G2 大きい文字とみなす大きさ（既定 24）")
    ap.add_argument("--large-bold-pt", type=float, help="G2 太字で大きい文字とみなす大きさ（既定 18）")
    ap.add_argument("--footer-band", type=float, help="脚注の役割とみなす下端の領域（高さの割合、既定 0.10）")
    ap.add_argument("--title-band", type=float, help="見出しを探す上端の領域（高さの割合、既定 0.25）")
    ap.add_argument("--overlap-pt", type=float, help="G5 重なりの許容（pt、既定 1）")
    ap.add_argument("--title-max-lines", "--headline-max-lines", dest="title_max_lines", type=int,
                    help="G6 見出しの最大行数（既定 2）")
    ap.add_argument("--margin-in", type=float, help="I1 左右の余白（in、既定 0.6）")
    ap.add_argument("--margin-v-in", type=float, help="I1 上下の余白（in、既定 0.4）")
    ap.add_argument("--image-share", type=float, help="I6 画像が占める割合の下限（既定 0.25）")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    src = a.pdf
    if a.pptx:
        if not os.path.exists(a.pptx):
            print(f"ファイルが見つかりません: {a.pptx}")
            return 2
        src = export_pptx(a.pptx, a.pdf_out, a.include_hidden)
        if not src:
            return 2
    if not src:
        ap.print_usage()
        print("検査する PDF を渡してください（または --pptx、--selftest）。")
        return 2
    ext = os.path.splitext(src)[1].lower()
    if ext in (".pptx", ".ppt", ".key", ".odp", ".tex", ".html", ".htm"):
        print(f"{ext} は直接は測れません。{NEXT_STEP}" + ("（.pptx なら --pptx も使える）" if ext == ".pptx" else ""))
        return 2
    if not os.path.exists(src):
        print(f"ファイルが見つかりません: {src}")
        return 2
    t0 = time.time()
    try:
        res = run_check(src, Opts(a), a.pages, a.shots)
    except pdfium.PdfiumError as e:
        print(f"PDF を読めませんでした（{e}）。{NEXT_STEP}")
        return 2
    print(report_text(res, a.verbose))
    print(f"（{len(res['slides'])} ページを {time.time() - t0:.1f} 秒で検査）")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(public(res), f, ensure_ascii=False, indent=1)
    return 1 if res["summary"]["gates"] else 0


if __name__ == "__main__":
    sys.exit(main())
