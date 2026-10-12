"""Portfolio Statement PDF.

A faithful port of the legacy make_pv_stat_pdf.py: same PollyReports bands,
coordinates, fonts, number formats and page size, so the attachment looks
exactly like before. The only addition is the ICB logo in the empty top-left
corner of the page header (switch off with portfolio.include_logo = false).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Sequence

from reportlab.pdfgen.canvas import Canvas

from icb_mailer.vendor.PollyReports import Band, Element, Image, Report, Rule, SumElement

PAGE_SIZE = (595.44, 841.68)  # as in the legacy module (A4 at 72 dpi)
LOGO_SIZE = 50  # points; fits above the "Name :" line without moving anything

XX = 75
HEAD = 270
LN1 = XX - 1
LN2 = 7 + XX
LN3 = 15 + XX


def _int_fmt(value: Any) -> str:
    # NUMBER columns come back as int or float depending on the value.
    return format(int(value), ",d")


def _money_fmt(value: Any) -> str:
    return format(value, ",.02f")


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _column_separators(x: float) -> list[Element]:
    return [Element((x, y), ("Times-Roman", 9), text="|") for y in (LN1, LN2, LN3)]


def _page_header(profile: Sequence[Any], logo: Path | None) -> Band:
    account, name, as_on, _email, branch = profile[:5]
    elements: list[Any] = []
    if logo is not None:
        elements.append(Image((0, 0), width=LOGO_SIZE, height=LOGO_SIZE, text=str(logo)))
    elements += [
        Element((HEAD, 0), ("Times-Bold", 10), text="Investment Corporation of Bangladesh", align="centre"),
        Element((HEAD, 10), ("Times-Bold", 10), text=_text(branch), align="centre"),
        Element((HEAD, 20), ("Times-Bold", 10), text="Investors' Department", align="centre"),
        Element((HEAD, 30), ("Times-Bold", 9), text="Portfolio Statement", align="centre"),
        Element((HEAD, 40), ("Times-Roman", 8), text="(With Avg.Cost Price & Market Price)", align="centre"),
        Element((270, 50), ("Times-Bold", 8), text="As On :  ", align="right"),
        Element((270, 50), ("Times-Bold", 8), text=_text(as_on), align="left"),
        Element((40, 60), ("Times-Roman", 8), text="Name :  ", align="right"),
        Element((40, 60), ("Times-Roman", 8), text=_text(name), align="left"),
        Element((450, 60), ("Times-Roman", 8), text="Account No. :  ", align="right"),
        Element((450, 60), ("Times-Roman", 8), text=str(account), align="left"),
        Element((510, 60), ("Times-Roman", 8), text="(Tk.)", align="left"),
        Rule((1, 1 + XX), 540, thickness=0.5),
        *_column_separators(0),
        Element((80, 5 + XX), ("Times-Roman", 8), text=" Company Name", align="centre"),
        Element((80, 15 + XX), ("Times-Roman", 8), text="1", align="centre"),
        *_column_separators(180),
        Element((200, 5 + XX), ("Times-Roman", 8), text="Type", align="centre"),
        Element((200, 15 + XX), ("Times-Roman", 8), text="2", align="centre"),
        *_column_separators(220),
        Element((245, 5 + XX), ("Times-Roman", 8), text="No of Shares", align="centre"),
        Element((245, 15 + XX), ("Times-Roman", 8), text="3", align="centre"),
        *_column_separators(270),
        Element((295, 1 + XX), ("Times-Roman", 8), text="Avg. Cost", align="centre"),
        Element((295, 10 + XX), ("Times-Roman", 8), text="Price", align="centre"),
        Element((295, 17 + XX), ("Times-Roman", 8), text="4", align="centre"),
        *_column_separators(320),
        Element((345, 1 + XX), ("Times-Roman", 8), text="Market", align="centre"),
        Element((345, 10 + XX), ("Times-Roman", 8), text="Price", align="centre"),
        Element((345, 17 + XX), ("Times-Roman", 8), text="5", align="centre"),
        *_column_separators(370),
        Element((400, 1 + XX), ("Times-Roman", 8), text="Purchase Cost", align="centre"),
        Element((400, 10 + XX), ("Times-Roman", 8), text="(Total)", align="centre"),
        Element((400, 17 + XX), ("Times-Roman", 8), text="6", align="centre"),
        *_column_separators(430),
        Element((460, 1 + XX), ("Times-Roman", 8), text="Market Value", align="centre"),
        Element((460, 10 + XX), ("Times-Roman", 8), text="(Total)", align="centre"),
        Element((460, 17 + XX), ("Times-Roman", 8), text="7", align="centre"),
        *_column_separators(490),
        Element((520, 5 + XX), ("Times-Roman", 8), text="Difference", align="centre"),
        Element((520, 15 + XX), ("Times-Roman", 8), text="(7-6)", align="centre"),
        *_column_separators(540),
        Rule((1, 26 + XX), 540, thickness=0.5),
    ]
    return Band(elements)


def _detail_band() -> Band:
    return Band([
        Element((10, 0), ("Times-Roman", 7), key=0),
        Element((192, 0), ("Times-Roman", 8), key=1),
        Element((260, 0), ("Times-Roman", 8), key=2, align="right", format=_int_fmt),
        Element((310, 0), ("Times-Roman", 8), key=3, align="right", format=_money_fmt),
        Element((360, 0), ("Times-Roman", 8), key=4, align="right", format=_money_fmt),
        Element((425, 0), ("Times-Roman", 8), key=5, align="right", format=_money_fmt),
        Element((485, 0), ("Times-Roman", 8), key=6, align="right", format=_money_fmt),
        Element((537, 0), ("Times-Roman", 8), key=7, align="right", format=_money_fmt),
    ])


def _report_footer(footer_text: Sequence[Any]) -> Band:
    f = [_text(v) for v in footer_text]
    return Band([
        Rule((220, 0), 320, thickness=0.5),
        SumElement((260, 0), ("Times-Bold", 8), key=2, align="right", format=_int_fmt),
        SumElement((425, 0), ("Times-Bold", 8), key=5, align="right", format=_money_fmt),
        SumElement((485, 0), ("Times-Bold", 8), key=6, align="right", format=_money_fmt),
        SumElement((537, 0), ("Times-Bold", 8), key=7, align="right", format=_money_fmt),
        Rule((220, 10), 320, thickness=0.5),
        Element((100, 20), ("Times-Roman", 8), text="Max. Loan Limit:", align="right"),
        Element((160, 20), ("Times-Roman", 8), text=f[0], align="right"),
        Element((310, 25), ("Times-Bold", 8), text="As per control", align="right"),
        Rule((261, 34), 50, thickness=0.5),
        Element((100, 30), ("Times-Roman", 8), text="Current Balance:", align="right"),
        Element((160, 30), ("Times-Roman", 8), text=f[1], align="right"),
        Element((310, 40), ("Times-Roman", 8), text="Portfolio Value:", align="right"),
        Element((370, 40), ("Times-Roman", 8), text=f[3], align="right"),
        Element((310, 50), ("Times-Roman", 8), text="Purchase Power:", align="right"),
        Element((370, 50), ("Times-Roman", 8), text=f[4], align="right"),
        Element((100, 40), ("Times-Roman", 8), text="Portfolio Value:", align="right"),
        Element((160, 40), ("Times-Roman", 8), text=f[2], align="right"),
        Element((100, 50), ("Times-Roman", 8), text="(As per market)", align="right"),
        Element((310, 60), ("Times-Roman", 8), text="Margin:", align="right"),
        Element((370, 60), ("Times-Roman", 8), text=f[5], align="right"),
        Element((310, 70), ("Times-Roman", 8), text="Sortfall:", align="right"),
        Element((370, 70), ("Times-Roman", 8), text=f[6], align="right"),
    ])


def build_portfolio_pdf(
    profile: Sequence[Any],
    holdings: Sequence[Sequence[Any]],
    footer_text: Sequence[Any],
    logo: Path | None = None,
) -> bytes:
    """Render the statement and return the PDF bytes.

    profile     -> (account, name, as-on date, email, branch name)
    holdings    -> rows of EMAIL_PV_STAT_DATA (8 columns)
    footer_text -> the 7 to_char() values of EMAIL_PV_STAT_FOOTER
    """
    if logo is not None and not logo.exists():
        logo = None
    report = Report(list(holdings))
    report.detailband = _detail_band()
    report.pageheader = _page_header(profile, logo)
    report.reportfooter = _report_footer(footer_text)

    buffer = io.BytesIO()
    canvas = Canvas(buffer, PAGE_SIZE, pageCompression=1)
    canvas.setTitle(f"Portfolio Statement - {profile[0]}")
    canvas.setAuthor("Investment Corporation of Bangladesh")
    canvas.setSubject("Portfolio Statement")
    report.generate(canvas)
    canvas.save()
    return buffer.getvalue()
