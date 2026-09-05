"""Turn the interpreted controls of one framework into a PDF.

Only controls with interpretations are included. Empty fields do not appear. Mappings carry exportable
edges only (L2 derived edges excluded). The state banner precedes each control: a reader of this file
"""
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
)

from framework_reader.interpret.render import FIELD_LABELS
from framework_reader.publish.site import collect

_FONT = "STSong-Light"


def _ensure_font() -> None:
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))


def _style(name: str, size: float, *, leading: float | None = None,
           space_before: float = 0, space_after: float = 0) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=_FONT,
        fontSize=size,
        leading=leading or size * 1.5,
        spaceBefore=space_before,
        spaceAfter=space_after,
    )


def _value_html(value: object) -> str:
    if isinstance(value, dict):
        return "<br/>".join(
            f"Level {escape(str(k))}: {escape(str(v))}"
            for k, v in sorted(value.items())
        )
    if isinstance(value, list):
        return "<br/>".join(f"· {escape(str(item))}" for item in value)
    return escape(str(value)).replace("\n", "<br/>")


def render_framework_pdf(api, framework_id: str) -> bytes:
    """No such framework, or not a single interpretation: raises LookupError."""
    view = api.get_framework(framework_id)
    if view is None:
        raise LookupError(framework_id)
    entries = collect(api, framework_id)
    if not entries:
        raise LookupError(framework_id)

    _ensure_font()
    title = _style("title", 16, space_after=6)
    meta = _style("meta", 9, leading=13, space_after=12)
    heading = _style("heading", 12, space_before=10, space_after=4)
    mark = _style("mark", 9, leading=13, space_after=6)
    field = _style("field", 10, leading=15, space_before=4, space_after=2)
    body = _style("body", 10, leading=15, space_after=6)

    drafts = sum(
        1 for e in entries
        if api.interpretation_state(e.control_id) == "draft"
    )
    story = [
        Paragraph(escape(view.name), title),
        Paragraph(
            f"Covers {len(entries)} control(s)"
            + (f", of which {drafts} control(s) are still AI drafts, not confirmed by the author" if drafts else "")
            + f". Generated on {date.today().isoformat()}. "
            "No copyrighted source text, no derived mappings.",
            meta,
        ),
    ]
    labels = dict(FIELD_LABELS)
    for entry in entries:
        block = [
            Paragraph(f"{escape(entry.short_id)}　{escape(entry.label)}", heading),
        ]
        state = api.interpretation_state(entry.control_id)
        if state == "draft":
            block.append(Paragraph("[AI draft, not confirmed by the author]", mark))
        elif state == "confirmed":
            block.append(Paragraph("[Confirmed]", mark))
        for name, value in entry.fields:
            block.append(Paragraph(escape(labels.get(name, name)), field))
            block.append(Paragraph(_value_html(value), body))
        if entry.mappings:
            shown = "; ".join(
                f"{escape(m.control_id.split(':', 1)[-1])} {escape(m.label)}"
                for m in entry.mappings
            )
            block.append(Paragraph("Mapped to controls in other frameworks (official mappings)", field))
            block.append(Paragraph(shown, body))
        story.append(KeepTogether(block))
        story.append(Spacer(1, 4 * mm))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=view.name, author="Framework Workbench",
    )

    def _footer(canvas, document):
        canvas.saveState()
        canvas.setFont(_FONT, 8)
        canvas.drawString(document.leftMargin, 10 * mm, view.name)
        canvas.drawRightString(
            A4[0] - document.rightMargin, 10 * mm,
            str(canvas.getPageNumber()),
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
