"""Convert a markdown report into a PDF with embedded book graphs.

Usage:
    python md_to_pdf.py path/to/report.md path/to/output.pdf
"""

import sys
from pathlib import Path

import markdown
from weasyprint import HTML, CSS


CSS_STYLE = """
@page { size: A4; margin: 22mm 20mm; }
body {
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 11.5pt;
    line-height: 1.55;
    color: #1a1a1a;
    max-width: 100%;
}
h1 {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 22pt;
    margin: 0 0 6pt 0;
    color: #111;
    border-bottom: 2px solid #333;
    padding-bottom: 4pt;
}
h2 {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 15pt;
    color: #333;
    margin-top: 22pt;
    margin-bottom: 6pt;
}
h3 {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 12pt;
    color: #444;
    margin-top: 14pt;
    margin-bottom: 4pt;
}
p { margin: 0 0 8pt 0; text-align: justify; }
em { color: #4a2a1a; }
strong { color: #111; }
figure { margin: 12pt 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; border: 1px solid #ccc; }
figcaption {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 9.5pt;
    color: #666;
    margin-top: 4pt;
    font-style: italic;
}
blockquote {
    border-left: 3px solid #999;
    margin: 8pt 0 8pt 8pt;
    padding-left: 10pt;
    color: #333;
    font-style: italic;
}
ul, ol { margin: 4pt 0 10pt 20pt; }
li { margin-bottom: 3pt; }
hr { border: none; border-top: 1px solid #ccc; margin: 16pt 0; }
.meta {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 9.5pt;
    color: #666;
    margin-bottom: 18pt;
}
"""


def convert(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    html_doc = f"<html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    # base_url so relative image paths in markdown resolve.
    HTML(string=html_doc, base_url=str(md_path.parent)).write_pdf(
        str(pdf_path), stylesheets=[CSS(string=CSS_STYLE)]
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: md_to_pdf.py <input.md> <output.pdf>")
        sys.exit(1)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")
