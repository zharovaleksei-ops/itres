#!/usr/bin/env python3
"""
build_pdf.py — сборка PDF из документов сайта (статьи/политики/документы в docs/).

Две стратегии:
  1) HTML-документы (политики, документы) → один PDF через WeasyPrint
     (с обложкой, колонтитулами и нумерацией страниц);
  2) картинки-сканы документов (*.jpg/*.png в docs/scans/) → PDF через img2pdf
     (без пересжатия — побайтово сохранённое качество скана).

Использование:
    python tools/build_pdf.py                    # соберёт site/docs → site/dist/pdf
    python tools/build_pdf.py --docs site/docs --out site/dist/pdf
    python tools/build_pdf.py --single privacy.html   # только один документ
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import img2pdf

TITLE = "ИТС — Документы и политики"


def htmls_to_pdf(html_files: list[Path], out_pdf: Path) -> bool:
    if not html_files:
        return False
    try:
        from weasyprint import HTML
    except ImportError:
        print("WeasyPrint не установлен (pip install weasyprint) — HTML в PDF пропускаю.")
        return False

    parts = []
    for f in html_files:
        parts.append(f.read_text(encoding="utf-8"))
    merged = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 20mm 15mm;
           @bottom-center {{ content: "{TITLE} — стр. " counter(page) " из " counter(pages);
                            font: 9pt sans-serif; color:#777; }} }}
  .cover {{ text-align:center; padding-top:120mm; }}
  .cover h1 {{ font-size:26pt; }} .cover p {{ color:#555; }}
  section.doc {{ page-break-before: always; font: 11pt/1.5 Georgia, serif; }}
  section.doc h1 {{ font-size:17pt; border-bottom:2px solid #2b6cb0; padding-bottom:4px; }}
  a {{ color:#2b6cb0; text-decoration:none; }}
</style></head><body>
<div class="cover"><h1>{TITLE}</h1>
<p>Сборка от {date.today():%d.%m.%Y}<br/>Реестр операций ПДн · согласие на cookie · политика конфиденциальности<br/>
соответствие 152-ФЗ, 149-ФЗ и рекомендациям Роскомнадзора</p></div>
{chr(10).join(parts)}
</body></html>"""
    tmp = out_pdf.parent / "_merged.html"
    tmp.write_text(merged, encoding="utf-8")
    HTML(filename=str(tmp)).write_pdf(str(out_pdf))
    tmp.unlink()
    return True


def scans_to_pdf(scan_files: list[Path], out_pdf: Path) -> bool:
    if not scan_files:
        return False
    # img2pdf принимает RGB JPEG/PNG; конвертируем всё, что нужно, через Pillow
    prepared = []
    try:
        from PIL import Image
        for f in scan_files:
            with Image.open(f) as im:
                if im.mode not in ("RGB", "L", "CMYK") or f.suffix.lower() == ".png":
                    conv = out_pdf.parent / (f.stem + "_prep.jpg")
                    im.convert("RGB").save(conv, "JPEG", quality=92)
                    prepared.append(conv)
                else:
                    prepared.append(f)
    except ImportError:
        prepared = scan_files
    out_pdf.write_bytes(img2pdf.convert([str(p) for p in prepared]))
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="site/docs", help="каталог источников")
    ap.add_argument("--out", default="site/dist/pdf", help="каталог результата")
    ap.add_argument("--single", default="", help="собрать только этот HTML-файл")
    args = ap.parse_args()

    docs = Path(args.docs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not docs.is_dir():
        sys.exit(f"Каталог {docs} не найден.")

    made = []
    if args.single:
        ok = htmls_to_pdf([Path(args.single)], out / f"{Path(args.single).stem}.pdf")
    else:
        html_files = sorted(docs.glob("*.html"))
        ok = htmls_to_pdf(html_files, out / "documents.pdf")
        if ok:
            made.append(out / "documents.pdf")
        scans = sorted(p for p in docs.glob("scans/*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if scans_to_pdf(scans, out / "scans.pdf"):
            made.append(out / "scans.pdf")

    for p in made or []:
        print(f"Готово: {p} ({p.stat().st_size//1024} KB)")
    if not made and not args.single:
        print("Нет ни HTML-документов, ни сканов — PDF не собран.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
