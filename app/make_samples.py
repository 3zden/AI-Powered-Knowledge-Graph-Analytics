"""Generate the fictional sample corpus (one source per format in the architecture diagram).

All companies and people below are invented. Run once inside the app image:
    docker compose run --rm app python make_samples.py
"""
import json
import os
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from docx import Document
from fpdf import FPDF

DATA = Path(os.environ.get("DATA_DIR", "/data"))

WEB = {
    "index.html": """<html><head><title>EuroTech Watch</title></head><body>
<h1>EuroTech Watch - company profiles</h1>
<ul><li><a href="helios.html">Helios Robotics profile</a></li>
<li><a href="verdant.html">Verdant Grid profile</a></li></ul>
<p>Independent coverage of European deep-tech start-ups.</p></body></html>""",
    "helios.html": """<html><head><title>Helios Robotics SA</title></head><body>
<h1>Helios Robotics SA</h1>
<p>Helios Robotics, founded in 2016 by Dr. Amara Okafor, is headquartered in Lyon, France.
Okafor still serves as chief executive officer of the company.</p>
<p>The company's flagship product, the Atlas Arm, is a collaborative industrial robot arm
used in automotive and electronics assembly lines.</p>
<p>Helios sources the battery packs of the Atlas Arm from Lumen Batteries S.p.A.</p>
<a href="index.html">Back</a></body></html>""",
    "verdant.html": """<html><head><title>Verdant Grid AS</title></head><body>
<h1>Verdant Grid AS</h1>
<p>Verdant Grid AS is an Oslo-based energy software company led by CEO Lars Henriksen.
It develops GridMind, a platform that balances renewable energy on regional power grids.</p>
<p>In 2023 Verdant Grid acquired Kestrel Sensors, a sensor start-up based in Bergen.</p>
<a href="index.html">Back</a></body></html>""",
}

API_NEWS = [
    {"id": 1, "date": "2024-03-02", "headline": "Northwind leads Series B in Helios",
     "body": "London investor Northwind Capital invested 40 million euros in Helios Robotics to scale production of the Atlas Arm."},
    {"id": 2, "date": "2024-05-17", "headline": "Verdant Grid and Corvid Analytics announce partnership",
     "body": "Verdant Grid has partnered with Corvid Analytics to bring forecasting models into GridMind."},
    {"id": 3, "date": "2024-09-09", "headline": "Northwind backs battery maker",
     "body": "Northwind Capital Partners LLP invested in Lumen Batteries, the Turin solid-state battery manufacturer."},
]

PDF_TEXT = """Northwind Capital Partners LLP - Annual Portfolio Report 2024

Northwind Capital is a venture capital firm based in London, United Kingdom.
Daniel Weiss is a partner at Northwind Capital and leads the industrial technology practice.

Portfolio companies: Northwind Capital has invested in Helios Robotics (Lyon), Verdant Grid (Oslo)
and Lumen Batteries (Turin).

Governance: following the Series B round, Daniel Weiss joined the board of directors of Helios Robotics."""

DOCX_PARAS = [
    "Lumen Batteries - Company brief",
    "Lumen Batteries S.p.A. is a battery manufacturer located in Turin, Italy.",
    "Its chief technology officer, Sofia Marchetti, leads the development of SolidCell, a solid-state energy storage cell.",
    "Lumen supplies battery packs to Helios Robotics and storage modules to Verdant Grid.",
    "In 2024 Julien Moreau joined the board of directors of Lumen Batteries.",
]

EMAIL = {
    "From": "Mei Tanaka <mei@corvid.example>",
    "To": "press@eurotech.example",
    "Subject": "Corvid Analytics x Verdant Grid partnership",
    "Date": "Fri, 17 May 2024 09:12:00 -0400",
    "body": """Hello,

I founded Corvid Analytics in Montreal in 2019 to build forecasting software for utilities.
I'm happy to confirm that Corvid Analytics Inc. is now partnering with Verdant Grid:
our demand forecasting models will run inside GridMind.

Best regards,
M. Tanaka
Founder, Corvid Analytics""",
}

DB_ROWS = [
    ("Quanta Bio", "Basel", "Julien Moreau", "Laboratory automation and biotech research"),
    ("Corvid Analytics Inc.", "Montreal", "Mei Tanaka", "Forecasting software for utilities"),
]

TXT = """Field notes - Basel visit
Quanta Bio, whose CEO is Julien Moreau, partners with Helios Robotics to automate its laboratories in Basel
using Atlas Arm robots."""


def main():
    web = DATA / "web"
    (web / "api").mkdir(parents=True, exist_ok=True)
    for name, html in WEB.items():
        (web / name).write_text(html)
    (web / "api" / "news.json").write_text(json.dumps(API_NEWS, indent=2))

    files = DATA / "files"
    files.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, PDF_TEXT)
    pdf.output(str(files / "northwind_report.pdf"))

    doc = Document()
    for p in DOCX_PARAS:
        doc.add_paragraph(p)
    doc.save(files / "lumen_brief.docx")

    msg = EmailMessage()
    for k in ("From", "To", "Subject", "Date"):
        msg[k] = EMAIL[k]
    msg.set_content(EMAIL["body"])
    (files / "corvid_partnership.eml").write_bytes(bytes(msg))

    (files / "basel_notes.txt").write_text(TXT)

    db_dir = DATA / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "registry.sqlite"
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE companies (name TEXT, city TEXT, ceo TEXT, activity TEXT)")
        con.executemany("INSERT INTO companies VALUES (?,?,?,?)", DB_ROWS)
    print("samples written to", DATA)


if __name__ == "__main__":
    main()
