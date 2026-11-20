"""Dựng bộ icon của app từ logo SVG (desktop/ui/logo.svg, bản nét dày cho cỡ nhỏ: logo-small.svg).

Ra: installer/icon.ico (file exe, bộ cài, lối tắt), desktop/ui/icon.ico (cửa sổ app), desktop/ui/icon.png (khay
hệ thống, màn hình chờ), installer/wizard*.bmp (dải màu thương hiệu bên trái và logo nhỏ góc phải của bộ cài). Cần Playwright + Chromium để vẽ SVG cho sắc nét ở từng cỡ.

  python scripts/make_icons.py
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "desktop" / "ui"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(svg: str, size: int, page) -> Image.Image:
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(f'<html><body style="margin:0;background:transparent">'
                     f'<div style="width:{size}px;height:{size}px">{svg.replace("<svg ", f"<svg width={chr(34)}{size}{chr(34)} height={chr(34)}{size}{chr(34)} ", 1)}</div></body></html>')
    png = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> None:
    full = (UI / "logo.svg").read_text(encoding="utf-8")
    small = (UI / "logo-small.svg").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        imgs = [render(small if s <= 24 else full, s, page) for s in SIZES]
        browser.close()
    big = imgs[-1]
    big.save(UI / "icon.png")
    big.save(UI / "icon.ico", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    shutil.copyfile(UI / "icon.ico", ROOT / "installer" / "icon.ico")
    wizard_images(imgs[-1])
    print("Đã dựng icon:", ", ".join(f"{s}px" for s in SIZES))


NAVY = (14, 26, 54)


def wizard_images(logo: Image.Image) -> None:
    """Ảnh của bộ cài Inno Setup: dải dọc xanh đậm có logo (trang chào, trang xong) và logo nhỏ (các trang khác).
    Mỗi ảnh có bản 1x và 2x cho màn hình độ phân giải cao."""
    for scale in (1, 2):
        w, h = 164 * scale, 314 * scale
        big = Image.new("RGB", (w, h), NAVY)
        size = 72 * scale
        mark = logo.resize((size, size), Image.LANCZOS)
        big.paste(mark, ((w - size) // 2, 48 * scale), mark)
        big.save(ROOT / "installer" / f"wizard{'' if scale == 1 else '-2x'}.bmp")
        sw, sh = 55 * scale, 58 * scale
        small = Image.new("RGB", (sw, sh), (255, 255, 255))
        s2 = 46 * scale
        mark = logo.resize((s2, s2), Image.LANCZOS)
        small.paste(mark, ((sw - s2) // 2, (sh - s2) // 2), mark)
        small.save(ROOT / "installer" / f"wizard-small{'' if scale == 1 else '-2x'}.bmp")


if __name__ == "__main__":
    main()
