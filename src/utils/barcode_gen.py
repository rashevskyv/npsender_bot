"""Utility for generating Code128 barcode images for Nova Poshta ScanSheets and Express Waybills."""

import io
import logging
import barcode
from barcode.writer import ImageWriter

logger = logging.getLogger(__name__)


def generate_code128_barcode(barcode_data: str) -> bytes:
    """Generate high-resolution PNG bytes for a Code128 barcode string."""
    try:
        clean_data = str(barcode_data or "").strip()
        if not clean_data:
            raise ValueError("Barcode data cannot be empty")
        rv = io.BytesIO()
        code128_cls = barcode.get_barcode_class("code128")
        code_inst = code128_cls(clean_data, writer=ImageWriter())
        code_inst.write(
            rv,
            options={
                "module_height": 18.0,
                "module_width": 0.35,
                "font_size": 9,
                "text_distance": 4.0,
                "dpi": 300,
                "quiet_zone": 6.5,
            },
        )
        return rv.getvalue()
    except Exception as e:
        logger.error(f"Error generating barcode for '{barcode_data}': {e}", exc_info=True)
        raise e


def _get_font(size: int, bold: bool = False):
    """Retrieve TTF font with fallback to PIL default font."""
    from PIL import ImageFont

    bold_candidates = [
        "arialbd.ttf",
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "arial.ttf",
    ]
    regular_candidates = [
        "arial.ttf",
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    candidates = bold_candidates if bold else regular_candidates
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def generate_client_card_image(
    full_name: str,
    phone: str,
    card_number: str,
    barcode_data: str,
    barcode_label: str = "",
) -> bytes:
    """Generate high-contrast Nova Poshta digital client card with scannable barcode."""
    from PIL import Image, ImageDraw

    card_w, card_h = 800, 500
    img = Image.new("RGB", (card_w, card_h), color="#FFFFFF")
    draw = ImageDraw.Draw(img)

    # Red border
    draw.rounded_rectangle([(0, 0), (card_w - 1, card_h - 1)], radius=24, outline="#DA291C", width=4)

    # Top red header banner
    draw.rounded_rectangle([(4, 4), (card_w - 5, 90)], radius=20, fill="#DA291C")
    draw.rectangle([(4, 50), (card_w - 5, 90)], fill="#DA291C")

    font_title = _get_font(30, bold=True)
    font_sub = _get_font(18, bold=False)
    draw.text((30, 26), "НОВА ПОШТА", fill="#FFFFFF", font=font_title)
    draw.text((card_w - 230, 36), "КАРТКА КЛІЄНТА", fill="#FFFFFF", font=font_sub)

    # Client Name
    font_name = _get_font(26, bold=True)
    name_display = full_name.strip() if full_name else "Клієнт Нової Пошти"
    if len(name_display) > 36:
        name_display = name_display[:34] + "..."
    draw.text((40, 115), name_display, fill="#1A1A1A", font=font_name)

    # Phone & Card Info
    font_info = _get_font(20, bold=False)
    phone_display = f"📞 {phone.strip()}" if phone else "📞 Номер не вказано"
    card_display = f"№ {card_number.strip()}" if card_number else ""
    draw.text((40, 160), phone_display, fill="#495057", font=font_info)
    if card_display:
        draw.text((card_w - 320, 160), card_display, fill="#495057", font=font_info)

    # Generate Barcode
    clean_bc = str(barcode_data or "").strip()
    if not clean_bc:
        clean_bc = str(phone or "0000000000").strip()

    code128_cls = barcode.get_barcode_class("code128")
    code_inst = code128_cls(clean_bc, writer=ImageWriter())
    bc_io = io.BytesIO()
    code_inst.write(
        bc_io,
        options={
            "module_height": 16.0,
            "module_width": 0.35,
            "font_size": 0,
            "text_distance": 0.0,
            "dpi": 300,
            "quiet_zone": 1.0,
        },
    )
    bc_img = Image.open(bc_io).convert("RGB")

    target_bc_w = card_w - 80
    ratio = target_bc_w / bc_img.width
    target_bc_h = int(bc_img.height * ratio)
    if target_bc_h > 200:
        target_bc_h = 200
        target_bc_w = int(bc_img.width * (target_bc_h / bc_img.height))

    bc_resized = bc_img.resize((target_bc_w, target_bc_h), Image.Resampling.LANCZOS)
    bc_x = (card_w - target_bc_w) // 2
    bc_y = 210
    img.paste(bc_resized, (bc_x, bc_y))

    # Barcode label underneath
    font_code = _get_font(24, bold=True)
    display_label = str(barcode_label or clean_bc).strip()
    bbox = draw.textbbox((0, 0), display_label, font=font_code)
    text_w = bbox[2] - bbox[0]
    draw.text(((card_w - text_w) // 2, bc_y + target_bc_h + 8), display_label, fill="#212529", font=font_code)

    rv = io.BytesIO()
    img.save(rv, format="PNG")
    return rv.getvalue()

