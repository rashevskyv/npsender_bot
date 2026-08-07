"""Utility for generating Code128 barcode images for Nova Poshta ScanSheets and Express Waybills."""

import io
import logging
import barcode
from barcode.writer import ImageWriter

logger = logging.getLogger(__name__)


def generate_code128_barcode(barcode_data: str) -> bytes:
    """Generate high-resolution PNG bytes for a Code128 barcode string."""
    try:
        clean_data = "".join(filter(str.isdigit, str(barcode_data))) or str(barcode_data)
        rv = io.BytesIO()
        code128_cls = barcode.get_barcode_class("code128")
        code_inst = code128_cls(clean_data, writer=ImageWriter())
        code_inst.write(
            rv,
            options={
                "module_height": 18.0,
                "font_size": 12,
                "text_distance": 5.0,
                "dpi": 300,
                "quiet_zone": 3.0,
            },
        )
        return rv.getvalue()
    except Exception as e:
        logger.error(f"Error generating barcode for '{barcode_data}': {e}", exc_info=True)
        raise e
