#!/usr/bin/env python3
"""
optimize_images.py — «причёска» изображений сайта: сжатие, ресайз, WebP/AVIF.

Что делает:
  * рекурсивно сканирует каталог (или один файл) с картинками;
  * GIF/анимации не трогает (копирует как есть);
  * уменьшает вес: ограничивает максимальный размер (по умолчанию 2560 px по длинной
    стороне), пересохраняет JPEG/PNG с оптимальными параметрами;
  * создаёт современные форматы WebP (и AVIF, если поддержан Pillow) рядом с оригиналом;
  * генерирует blur-превью (LQIP) для плавной загрузки;
  * удаляет EXIF/GPS-метаданные (это ещё и требование приватности — 152-ФЗ);
  * пишет отчёт report.json: имя файла, исходный/новый вес, экономия %.

Использование:
    python tools/optimize_images.py assets/images                    # на месте + webp
    python tools/optimize_images.py assets/images --out dist/img     # копия в dist
    python tools/optimize_images.py assets/images --max-edge 1920 --quality 80
    python tools/optimize_images.py assets/images --dry-run          # только отчёт

Зависимости: Pillow (pip install pillow). AVIF — при поддержке сборки Pillow.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
from dataclasses import dataclass, field, asdict
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
except ImportError:  # pragma: no cover
    sys.exit("Нужен Pillow: pip install pillow")

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MODERN_ONLY = {".avif"}  # не обрабатываем как исходники, но учитываем веса


@dataclass
class Stats:
    src: str
    dst: str = ""
    src_bytes: int = 0
    dst_bytes: int = 0
    skipped: str = ""
    outputs: dict = field(default_factory=dict)

    @property
    def saved_pct(self) -> float:
        if self.src_bytes and self.dst_bytes:
            return round((1 - self.dst_bytes / self.src_bytes) * 100, 1)
        return 0.0


def has_alpha(img: "Image.Image") -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return True
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def auto_groom(rgb: "Image.Image") -> "Image.Image":
    """«причёска» фото: лёгкий автоконтраст + мягкое шумоподавление.

    Убирает цифровой шум/артефакты старого сайта — JPEG/WebP/AVIF сжимают
    такое изображение заметно легче. Результат всё равно проверяется по весу
    в encode_variants(): если стало тяжелее — причёску откатываем.
    """
    out = ImageOps.autocontrast(rgb, cutoff=1)
    return out.filter(ImageFilter.SMOOTH_MORE)


def make_lqip(img: "Image.Image", path: Path) -> str:
    """крошечное размытое превью в base64 — фон под картинку при загрузке"""
    tiny = img.copy()
    tiny.thumbnail((24, 24), Image.LANCZOS)
    tiny = tiny.convert("RGB")
    buf = BytesIO()
    tiny.save(buf, "JPEG", quality=20, optimize=True)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    path.with_suffix(".lqip.txt").write_text(uri)
    return uri


def best_ext_for(img: "Image.Image") -> str:
    """фото без прозрачности → .jpg; всё с альфа-каналом → .png"""
    return ".png" if has_alpha(img) else ".jpg"


def encode_variants(img: "Image.Image", stem: str, dst_dir: Path, args,
                    originals: dict[str, int]) -> tuple[dict, dict]:
    """Кодирует варианты (основной jpg/png, webp, avif) во временный каталог.

    Возвращает (outputs, sizes): имена файлов и их размеры в байтах.
    Оригиналы (weights из исходных файлов) участвуют в выборе самого
    лёгкого варианта: вариант, тяжелее оригинала, помечается и удаляется.
    Ресайз выполняется один раз до кодирования — все форматы одинаковой
    геометрии; «причёска» (autocontrast+sharpen) только для фото-вариантов.
    """
    outputs: dict = {}
    sizes: dict = {}
    tmpdir = dst_dir / "_tmp_enc"
    tmpdir.mkdir(parents=True, exist_ok=True)

    if max(img.size) > args.max_edge:
        img.thumbnail((args.max_edge, args.max_edge), Image.LANCZOS)

    ext = best_ext_for(img)
    main_tmp = tmpdir / (stem + ext)
    if ext == ".jpg":
        groomed = None  # обработанное фото переиспользуем для webp/avif
        try:
            g = auto_groom(img.convert("RGB"))
            g.save(main_tmp, "JPEG", quality=args.quality,
                   optimize=True, progressive=True)
            if main_tmp.stat().st_size < src_weight_of(originals, ext, img):
                groomed = g  # причёска выгодна — оставляем её
            else:
                img.convert("RGB").save(main_tmp, "JPEG", quality=args.quality,
                                        optimize=True, progressive=True)
        except OSError:
            img.convert("RGB").save(main_tmp, "JPEG", quality=args.quality,
                                    optimize=True, progressive=True)
    else:
        groomed = None
        rgba = img.convert("RGBA")
        if args.pngquant_like:  # квантование палитры для сильной экономии PNG
            q = rgba.quantize(colors=256, method=Image.MEDIANCUT)
            q.save(main_tmp, "PNG", optimize=True)
        else:
            rgba.save(main_tmp, "PNG", optimize=True)
    sizes["main"] = main_tmp.stat().st_size
    outputs["main_tmp"] = str(main_tmp)

    if not args.no_webp:
        webp_src = groomed if groomed is not None else img
        webp_tmp = tmpdir / (stem + ".webp")
        webp_tmp.write_bytes(b"")
        webp_src.save(webp_tmp, "WEBP", quality=args.quality, method=6)
        sizes["webp"] = webp_tmp.stat().st_size
        outputs["webp_tmp"] = str(webp_tmp)

        avif_tmp = tmpdir / (stem + ".avif")
        try:
            webp_src.save(avif_tmp, "AVIF", quality=args.quality)
            sizes["avif"] = avif_tmp.stat().st_size
            outputs["avif_tmp"] = str(avif_tmp)
        except (ValueError, OSError):
            avif_tmp.unlink(missing_ok=True)  # сборка Pillow без AVIF

    # не оставляем вариант тяжелее оригинала того же формата
    for key in ("main", "webp", "avif"):
        sfx = {"main": ext, "webp": ".webp", "avif": ".avif"}[key].lstrip(".")
        orig = originals.get(sfx)
        if orig is not None and sizes.get(key, 0) >= orig:
            p = Path(outputs.pop(f"{key}_tmp"))
            p.unlink(missing_ok=True)
            sizes.pop(key, None)
            outputs[f"{key}_skipped_heavier"] = True
    return outputs, sizes


def src_weight_of(originals: dict, ext: str, img: "Image.Image") -> float:
    return originals.get(ext.lstrip("."), float("inf"))


def process_image(src: Path, dst_dir: Path, args) -> Stats:
    st = Stats(src=str(src), src_bytes=src.stat().st_size)

    # Уже готовые производные не пережимаем повторно — иначе второй прогон
    # «съедает» качество (webp→webp, jpg из png и т.п.):
    #   * avif/lqip — всегда производные;
    #   * webp — производный, если рядом есть одноимённый исходник или другой webp-«родитель»;
    #   * jpg/jpeg — производный, если рядом есть одноимённый png.
    src_ext = src.suffix.lower()
    base = dst_dir / src.stem
    has_main_src = any(base.with_suffix(e).exists() for e in (".png", ".jpg", ".jpeg"))
    derived = (
        src_ext in MODERN_ONLY or src.name.endswith(".lqip.txt")
        or (src_ext == ".webp" and (has_main_src or src.parent != dst_dir))
        or (src_ext in (".jpg", ".jpeg") and base.with_suffix(".png").exists())
    )
    if derived:
        st.skipped = "производный формат — создан из исходника"
        return st

    try:
        img = Image.open(src)
        img.load()
        src_format = (img.format or "").lower()
    except (UnidentifiedImageError, OSError) as e:
        st.skipped = f"не читается: {e}"
        return st

    # GIF и анимация — не пересобираем
    if getattr(img, "n_frames", 1) > 1 or img.format == "GIF":
        st.skipped = "анимация/GIF — пропущено"
        return st

    img = ImageOps.exif_transpose(img)   # поворачиваем по EXIF Orientation…
    img.info.pop("exif", None)           # …и выкидываем метаданные (GPS! 152-ФЗ)

    # нормализуем режимы, которые нельзя сохранить в JPEG/WebP напрямую
    if img.mode in ("P", "PA", "LA", "I", "F", "CMYK"):
        img = img.convert("RGBA" if has_alpha(img) else "RGB")

    # ресайз один раз — дальше все варианты кодируются из уменьшенного изображения
    if max(img.size) > args.max_edge:
        img.thumbnail((args.max_edge, args.max_edge), Image.LANCZOS)

    # веса уже существующих файлов-«родственников» того же базового имени
    originals: dict[str, int] = {src_format: src.stat().st_size}
    for sibling in dst_dir.glob(src.stem + ".*"):
        sfx = sibling.suffix.lower().lstrip(".")
        if sfx not in originals and sibling.suffix.lower() in SUPPORTED | MODERN_ONLY:
            originals[sfx] = sibling.stat().st_size

    outputs, sizes = encode_variants(img, src.stem, dst_dir, args, originals)

    # --- основной файл (jpg/png): заменяем только если стало легче ---
    main_tmp = outputs.pop("main_tmp", None)
    ext = best_ext_for(img)
    if main_tmp:
        new_size = sizes["main"]
        if new_size < src_bytes_of(src, originals, ext):
            dst = src if src.suffix.lower() == ext else src.with_name(src.stem + ext)
            Path(main_tmp).replace(dst)
            st.dst, st.dst_bytes = str(dst), new_size
            st.outputs["main"] = dst.name
        else:
            Path(main_tmp).unlink(missing_ok=True)
            st.dst, st.dst_bytes = str(src), src.stat().st_size
            st.outputs["main"] = src.name + " (оригинал легче, не пережат)"
    else:
        st.dst, st.dst_bytes = str(src), src.stat().st_size
        st.outputs["main"] = src.name + " (пережатие не выгоднее оригинала)"

    # --- WebP / AVIF: сохраняем на диск только то, что легче имеющегося ---
    for key, sfx in (("webp", ".webp"), ("avif", ".avif")):
        tmp = outputs.pop(f"{key}_tmp", None)
        if not tmp:
            if outputs.pop(f"{key}_skipped_heavier", None):
                st.outputs[key] = f"пропущен (тяжелее оригинала)"
            continue
        final = dst_dir / (src.stem + sfx)
        if final.exists() and sizes[key] >= final.stat().st_size:
            Path(tmp).unlink(missing_ok=True)
            st.outputs[key] = f"{final.name} (уже легче)"
        else:
            Path(tmp).replace(final)
            st.outputs[key] = final.name
            st.outputs[f"{key}_bytes"] = sizes[key]

    shutil.rmtree(dst_dir / "_tmp_enc", ignore_errors=True)

    if args.lqip:
        make_lqip(img, Path(st.dst))
        st.outputs["lqip"] = Path(st.dst).with_suffix(".lqip.txt").name

    return st


def src_bytes_of(src: Path, originals: dict, ext: str) -> int:
    """с чем сравнивать: с весом файла того же формата, что будем писать"""
    key = ext.lstrip(".")
    if src.suffix.lower() == ext:
        return src.stat().st_size
    return originals.get(key, float("inf"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Оптимизация картинок сайта")
    ap.add_argument("path", help="файл или каталог с изображениями")
    ap.add_argument("--out", default="", help="каталог результата (по умолчанию — на месте)")
    ap.add_argument("--max-edge", type=int, default=2560, help="макс. длинная сторона, px")
    ap.add_argument("--quality", type=int, default=82, help="качество JPEG/WebP, 1..100")
    ap.add_argument("--no-webp", action="store_true", help="не генерировать WebP/AVIF")
    ap.add_argument("--lqip", action="store_true", help="генерировать blur-превью")
    ap.add_argument("--pngquant-like", action="store_true", help="квантовать PNG до 256 цветов")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать в проект, только отчёт")
    args = ap.parse_args()

    root = Path(args.path)
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*") if p.suffix.lower() in SUPPORTED
    )
    if not files:
        print(f"В {root} изображений не найдено.")
        return 0

    # dry-run пишется во временный каталог, чтобы не засорять проект
    if args.dry_run:
        dst_dir = Path("/tmp/_opt_preview")
        shutil.rmtree(dst_dir, ignore_errors=True)
    else:
        dst_dir = Path(args.out) if args.out else (root.parent if root.is_file() else root)
    dst_dir.mkdir(parents=True, exist_ok=True)

    results: list[Stats] = []
    for f in files:
        r = process_image(f, dst_dir, args)
        results.append(r)
        status = r.skipped or f"{r.src_bytes//1024}KB → {r.dst_bytes//1024}KB ({r.saved_pct}%)"
        print(f"[{'..' if r.skipped else 'ok'}] {r.src}: {status}")

    total_in = sum(r.src_bytes for r in results if not r.skipped)
    total_out = sum(r.dst_bytes for r in results if not r.skipped)
    summary = {
        "processed": len([r for r in results if not r.skipped]),
        "skipped": len([r for r in results if r.skipped]),
        "total_src_kb": total_in // 1024,
        "total_dst_kb": total_out // 1024,
        "saved_pct": round((1 - total_out / total_in) * 100, 1) if total_in else 0,
        "images": [asdict(r) | {"saved_pct": r.saved_pct} for r in results],
    }
    report_dir = Path(args.out) if args.out else root
    report_path = report_dir / "report.json" if report_dir.is_dir() else report_dir.with_name("report.json")
    if not args.dry_run:
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        shutil.rmtree(dst_dir, ignore_errors=True)
        report_path = Path("(dry-run, отчёт не сохранён)")

    print(f"\nИтого: {summary['total_src_kb']}KB → {summary['total_dst_kb']}KB "
          f"(−{summary['saved_pct']}%). Отчёт: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
