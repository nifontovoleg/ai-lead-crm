"""Run with the bundled document runtime (python-docx)."""
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "docs/Техническое задание.md"
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2.2)
    for style_name in ["Normal", "Title", "Heading 1", "Heading 2"]:
        style = document.styles[style_name]
        style.font.name = "Arial"
        style.font.color.rgb = RGBColor(0, 0, 0)
    normal = document.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.12
    document.styles["Title"].font.size = Pt(24)
    document.styles["Heading 1"].font.size = Pt(15)
    document.styles["Heading 1"].paragraph_format.space_before = Pt(13)
    document.styles["Heading 1"].paragraph_format.space_after = Pt(7)
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            paragraph = document.add_paragraph(line[2:], "Title")
        elif line.startswith("## "):
            paragraph = document.add_paragraph(line[3:], "Heading 1")
        else:
            paragraph = document.add_paragraph(line)
        paragraph.paragraph_format.widow_control = True
        properties = paragraph._p.get_or_add_pPr()
        # Документ на русском: проверка орфографии без автоматической смены языка.
        for run in paragraph.runs:
            language = OxmlElement("w:lang")
            language.set(qn("w:val"), "ru-RU")
            run._r.get_or_add_rPr().append(language)
    document.core_properties.title = "CRM в Google Sheets с AI скорингом"
    document.core_properties.subject = "Техническое задание проекта №2"
    document.core_properties.author = "Олег Нифонтов"
    document.save(ROOT / "docs/Техническое задание.docx")
    print("DOCX created; visual QA required")


if __name__ == "__main__":
    main()
