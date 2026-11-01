"""
cleaner.py — Strip all AI/provenance metadata from image files.

Strategy:
  - Re-encode the image via Pillow, which naturally drops unknown chunks/markers.
  - Explicitly clear EXIF, XMP, IPTC before saving.
  - For JPEG: scan and remove APP11 (C2PA/JUMBF) markers manually before
    passing to Pillow, so even partially-embedded C2PA data is wiped.

Output: a new file with the suffix `_clean` inserted before the extension
  e.g.  photo.jpg  →  photo_clean.jpg
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class CleanResult:
    """Result of a clean operation on a single file."""

    source: Path
    output: Path | None = None
    success: bool = False
    bytes_removed: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def clean_file(path: Path) -> CleanResult:
    """Strip all metadata from *path* and write `<stem>_clean<suffix>`."""
    result = CleanResult(source=path)
    suffix = path.suffix.lower()

    try:
        if suffix in (".jpg", ".jpeg"):
            output = _clean_jpeg(path)
        elif suffix == ".png":
            output = _clean_png(path)
        elif suffix in (".webp", ".tiff", ".tif"):
            output = _clean_generic(path, suffix)
        elif suffix in (".heic", ".heif"):
            output = _clean_heic(path, suffix)
        else:
            result.error = f"Unsupported format: {suffix}"
            return result

        result.output = output
        result.success = True
        result.bytes_removed = path.stat().st_size - output.stat().st_size
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Format-specific cleaners
# ---------------------------------------------------------------------------

def _output_path(source: Path) -> Path:
    """Compute the _clean output path next to the source file."""
    return source.with_stem(source.stem + "_clean")


def _clean_jpeg(path: Path) -> Path:
    """Strip all metadata from a JPEG file."""
    from PIL import Image

    # Step 1: Strip C2PA (APP11) and other non-image markers at the byte level
    raw = path.read_bytes()
    stripped = _strip_jpeg_markers(raw)

    # Step 2: Re-encode via Pillow to drop any remaining metadata
    with Image.open(io.BytesIO(stripped)) as img:
        img = img.convert("RGB")  # Ensure no embedded profile artifacts
        out_buf = io.BytesIO()
        img.save(
            out_buf,
            format="JPEG",
            quality=95,
            subsampling=0,
            exif=b"",          # Explicitly clear EXIF
        )

    output = _output_path(path)
    output.write_bytes(out_buf.getvalue())
    return output


def _clean_png(path: Path) -> Path:
    """Strip all metadata from a PNG file."""
    from PIL import Image

    with Image.open(path) as img:
        # Pillow re-save drops all ancillary metadata chunks (tEXt, zTXt, iTXt, eXIf, caBX)
        img_clean = img.copy()
        out_buf = io.BytesIO()
        img_clean.save(out_buf, format="PNG")

    output = _output_path(path)
    output.write_bytes(out_buf.getvalue())
    return output


def _clean_generic(path: Path, suffix: str) -> Path:
    """Strip metadata from WebP and TIFF via Pillow re-encode."""
    from PIL import Image

    fmt_map = {".webp": "WEBP", ".tiff": "TIFF", ".tif": "TIFF"}
    fmt = fmt_map.get(suffix, "WEBP")

    with Image.open(path) as img:
        img_clean = img.copy()
        out_buf = io.BytesIO()
        save_kwargs: dict = {"format": fmt}
        if fmt == "WEBP":
            save_kwargs["quality"] = 95
        img_clean.save(out_buf, **save_kwargs)

    output = _output_path(path)
    output.write_bytes(out_buf.getvalue())
    return output


def _clean_heic(path: Path, suffix: str) -> Path:
    """Strip metadata from HEIC/HEIF — convert to JPEG _clean output."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError as e:
        raise ImportError(
            "pillow-heif is required for HEIC/HEIF support. "
            "Install it with: pip install pillow-heif"
        ) from e

    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=95, subsampling=0, exif=b"")

    # Save as JPEG (HEIC → JPEG_clean)
    output = path.with_name(path.stem + "_clean.jpg")
    output.write_bytes(out_buf.getvalue())
    return output


# ---------------------------------------------------------------------------
# Low-level JPEG marker stripping
# ---------------------------------------------------------------------------

# Markers whose segments we KEEP (image data and essential structure)
_JPEG_KEEP_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3,  # SOFn (start of frame)
    0xC4,                      # DHT (Huffman table)
    0xDB,                      # DQT (quantization table)
    0xD0, 0xD1, 0xD2, 0xD3,  # RSTn (restart markers)
    0xD4, 0xD5, 0xD6, 0xD7,
    0xD8,                      # SOI
    0xD9,                      # EOI
    0xDA,                      # SOS (start of scan) — contains actual image data
    0xDD,                      # DRI (restart interval)
    0xE0,                      # APP0 (JFIF header — needed for compatibility)
}

# APP markers that carry metadata we want to strip
_JPEG_STRIP_MARKERS = {
    0xE1,  # APP1  — EXIF / XMP
    0xE2,  # APP2  — ICC extended XMP / Flashpix
    0xE3,  # APP3
    0xE4,  # APP4
    0xE5,  # APP5
    0xE6,  # APP6
    0xE7,  # APP7
    0xE8,  # APP8
    0xE9,  # APP9
    0xEA,  # APP10
    0xEB,  # APP11 — C2PA / JUMBF  ← the key one
    0xEC,  # APP12
    0xED,  # APP13 — IPTC
    0xEE,  # APP14 — Adobe color
    0xEF,  # APP15
    0xFE,  # COM (comment)
}


def _strip_jpeg_markers(data: bytes) -> bytes:
    """
    Walk JPEG markers and rebuild the file, dropping all metadata segments.
    Preserves SOI, APP0 (JFIF), SOFn, DHT, DQT, SOS+image data, EOI.
    """
    if not data.startswith(b"\xff\xd8"):
        return data  # Not a JPEG; return as-is

    out = bytearray(b"\xff\xd8")  # Write SOI
    pos = 2

    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            # We've hit raw scan data after SOS
            out.extend(data[pos:])
            break

        marker_byte = data[pos + 1]
        marker = (0xFF, marker_byte)
        pos += 2

        # Standalone markers (no length field)
        if marker_byte in (0xD8, 0xD9) or (0xD0 <= marker_byte <= 0xD7):
            if marker_byte == 0xD9:  # EOI
                out.extend(b"\xff\xd9")
            continue

        # Segments with a 2-byte length field
        if pos + 2 > len(data):
            break
        seg_len = struct.unpack(">H", data[pos : pos + 2])[0]
        segment = data[pos : pos + seg_len]
        pos += seg_len

        if marker_byte in _JPEG_STRIP_MARKERS:
            # Keep APP0 (JFIF) only — it's required for JFIF compliance
            if marker_byte == 0xE0:
                out.extend(b"\xff\xe0")
                out.extend(segment)
            # All other metadata markers: silently drop
            continue

        # Keep everything else
        out.extend(b"\xff")
        out.extend(bytes([marker_byte]))
        out.extend(segment)

        # SOS: after segment header, the rest is raw compressed image data
        if marker_byte == 0xDA:
            # Find the next 0xFF 0xD9 (EOI) and copy all scan data
            scan_data = data[pos:]
            out.extend(scan_data)
            break

    return bytes(out)
