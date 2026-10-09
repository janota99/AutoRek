"""A compact "tape" snapshot of one Sales Tax Calculator result, as PNG or PDF, no Streamlit.

Meant to be attached as backup to an invoice that needs a tax correction. It is drawn from a
``TaxResult`` that ``tax_calculator.calculate`` already computed; nothing here recomputes a number.
The stamp says it is a calculator estimate, so the tape is never mistaken for a filed return.
"""
from __future__ import annotations

import io
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFont

from .tax_calculator import SAAS_TAXABLE_SHARE, STATE_RATE, TaxResult

_SCALE = 3                     # draw at 3x so the image stays sharp when placed in a PDF
_W = 320                       # logical width in px
_PAD = 16
_INK, _MUTED, _BLUE, _GREEN, _DARK = "#0f172a", "#64748b", "#1d4ed8", "#047857", "#0f172a"
_FONTS = {
    "regular": ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
    "bold": ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "mono": ("consola.ttf", "DejaVuSansMono.ttf"),
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONTS[kind]:
        try:
            return ImageFont.truetype(name, size * _SCALE)
        except OSError:
            continue
    return ImageFont.load_default(size * _SCALE)


def _usd(v: Decimal) -> str:
    return f"${v:,.2f}"


def render_tape(result: TaxResult, stamp: str) -> Image.Image:
    """Draw the tape. ``stamp`` is the date/time text shown at the top (the caller supplies the clock)."""
    loc = result.location
    rows: list[tuple[str, str, str, str]] = [   # (label, value, label colour, value colour)
        ("Price (pre-tax)", _usd(result.price), _MUTED, _INK),
        ("Taxable amount" + (f" ({SAAS_TAXABLE_SHARE:.0%} SaaS)" if result.saas else ""),
         _usd(result.taxable_amount), _MUTED, _INK),
        (f"TX State {STATE_RATE:.2f}%", _usd(result.state.amount), _BLUE, _BLUE),
    ]
    rows += [(f"{ln.label} {ln.rate:.2f}%", _usd(ln.amount), _MUTED, _INK) for ln in result.local_lines]
    rows.append(("Total tax", _usd(result.total_tax), _INK, _INK))

    f_title, f_small, f_label, f_val = _font("bold", 14), _font("regular", 10), _font("regular", 12), _font("mono", 12)
    f_bold, f_big = _font("bold", 12), _font("mono", 24)
    line_h, top = 24, 84
    height = top + len(rows) * line_h + 12 + 64 + 40
    img = Image.new("RGB", (_W * _SCALE, height * _SCALE), "white")
    d = ImageDraw.Draw(img)
    s = _SCALE

    d.rectangle([0, 0, _W * s, 56 * s], fill=_DARK)
    d.text((_PAD * s, 10 * s), "Sales Tax Tape · Texas", font=f_title, fill="white")
    d.text((_PAD * s, 34 * s), stamp, font=f_small, fill="#94a3b8")
    d.text((_PAD * s, 66 * s), f"{loc.name} · {'SaaS' if result.saas else 'Standard sale'}", font=f_small, fill=_MUTED)

    y = top
    for label, value, lc, vc in rows:
        bold = label == "Total tax"
        d.text((_PAD * s, (y + 14) * s), label, font=f_bold if bold else f_label, fill=lc, anchor="ls")
        d.text(((_W - _PAD) * s, (y + 14) * s), value, font=f_val, fill=vc, anchor="rs")
        y += line_h
        if label.startswith("Taxable") or bold:
            d.line([_PAD * s, (y - 4) * s, (_W - _PAD) * s, (y - 4) * s], fill="#e2e8f0", width=s)
    y += 8
    d.rounded_rectangle([_PAD * s, y * s, (_W - _PAD) * s, (y + 56) * s], radius=10 * s, fill=_DARK)
    d.text(((_PAD + 12) * s, (y + 8) * s), "TOTAL DUE", font=f_small, fill="#94a3b8")
    d.text(((_PAD + 12) * s, (y + 22) * s), f"{result.effective_rate:.3f}% effective", font=f_small, fill="#94a3b8")
    d.text(((_W - _PAD - 12) * s, (y + 28) * s), _usd(result.total_due), font=f_big, fill="#34d399", anchor="rm")
    d.text((_PAD * s, (y + 66) * s), "Calculator estimate for backup only. Not a tax return.", font=f_small, fill=_MUTED)
    return img


def tape_png(result: TaxResult, stamp: str) -> bytes:
    buf = io.BytesIO()
    render_tape(result, stamp).save(buf, "PNG", dpi=(288, 288))
    return buf.getvalue()


def tape_pdf(result: TaxResult, stamp: str) -> bytes:
    """A one-page PDF sized to the tape (about 3.3 in wide at 288 dpi)."""
    buf = io.BytesIO()
    render_tape(result, stamp).save(buf, "PDF", resolution=288)
    return buf.getvalue()
