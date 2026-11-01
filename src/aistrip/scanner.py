"""
scanner.py — Detect AI-related metadata in image files.

Detects:
  - C2PA / JUMBF manifests (the main cause of Instagram's "AI Info" label)
  - XMP data (especially fields written by AI tools like Canva, Adobe Firefly)
  - EXIF fields that reveal AI tools (Software, Make, Model, Comment tags)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Known AI tool signatures found in EXIF / XMP "Software" or "Creator" tags
# ---------------------------------------------------------------------------
AI_SOFTWARE_SIGNATURES: list[str] = [
    "canva",
    "adobe firefly",
    "adobe photoshop (ai",
    "generative fill",
    "midjourney",
    "stable diffusion",
    "dall-e",
    "dall·e",
    "imagen",
    "ideogram",
    "runway",
    "pika",
    "leonardo.ai",
    "nightcafe",
    "bing image creator",
    "copilot",
]

# C2PA uses JUMBF boxes embedded in JPEG APP11 markers
JPEG_APP11_MARKER = b"\xff\xeb"
# C2PA box type identifier
C2PA_BOX_TYPE = b"c2pa"
# PNG chunk type for C2PA
C2PA_PNG_CHUNK = b"caBX"
# XMP namespace for C2PA
C2PA_XMP_NS = "http://c2pa.org/"
ADOBE_XMP_NS = "http://ns.adobe.com/xap/"


@dataclass
class ScanResult:
    """Holds all findings from scanning a single image file."""

    path: Path
    has_exif: bool = False
    has_xmp: bool = False
    has_c2pa: bool = False
    has_iptc: bool = False
    ai_software_tags: list[str] = field(default_factory=list)
    xmp_snippet: Optional[str] = None
    total_metadata_bytes: int = 0

    @property
    def has_ai_metadata(self) -> bool:
        return self.has_c2pa or bool(self.ai_software_tags)

    @property
    def has_any_metadata(self) -> bool:
        return self.has_exif or self.has_xmp or self.has_c2pa or self.has_iptc

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.has_c2pa:
            lines.append("C2PA manifest (AI provenance data — triggers 'AI Info' label)")
        if self.ai_software_tags:
            lines.append(f"AI software signatures: {', '.join(self.ai_software_tags)}")
        if self.has_exif:
            lines.append("EXIF data present")
        if self.has_xmp:
            lines.append("XMP data present")
        if self.has_iptc:
            lines.append("IPTC data present")
        if self.total_metadata_bytes:
            lines.append(f"Approx. metadata size: {self.total_metadata_bytes:,} bytes")
        return lines


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_file(path: Path) -> ScanResult:
    """Scan a single image file and return a ScanResult."""
    result = ScanResult(path=path)

    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        _scan_jpeg(path, result)
    elif suffix == ".png":
        _scan_png(path, result)
    elif suffix in (".webp", ".tiff", ".tif", ".heic", ".heif"):
        _scan_generic(path, result)

    return result


# ---------------------------------------------------------------------------
# Format-specific scanners
# ---------------------------------------------------------------------------

def _scan_jpeg(path: Path, result: ScanResult) -> None:
    """Parse JPEG markers to detect metadata segments."""
    with open(path, "rb") as fh:
        data = fh.read()

    if not data.startswith(b"\xff\xd8"):
        return  # Not a valid JPEG

    pos = 2  # Skip SOI marker
    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            break
        marker = data[pos : pos + 2]
        pos += 2

        # Markers without a length field (SOI, EOI, RST*)
        if marker in (b"\xff\xd8", b"\xff\xd9") or (0xD0 <= marker[1] <= 0xD7):
            continue

        if pos + 2 > len(data):
            break
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        segment = data[pos + 2 : pos + length]
        pos += length

        # APP1 — EXIF or XMP
        if marker == b"\xff\xe1":
            if segment.startswith(b"Exif\x00\x00"):
                result.has_exif = True
                result.total_metadata_bytes += length
                _check_exif_software(segment[6:], result)
            elif segment.startswith(b"http://ns.adobe.com/xap/") or segment.startswith(b"<?xpacket"):
                result.has_xmp = True
                result.total_metadata_bytes += length
                xmp_text = segment.decode("utf-8", errors="replace")
                _check_xmp_for_ai(xmp_text, result)

        # APP11 — C2PA / JUMBF
        elif marker == JPEG_APP11_MARKER:
            result.total_metadata_bytes += length
            if _is_c2pa_jumbf(segment):
                result.has_c2pa = True

        # APP13 — IPTC
        elif marker == b"\xff\xed":
            result.has_iptc = True
            result.total_metadata_bytes += length

        # APP2 — ICC or extended XMP
        elif marker == b"\xff\xe2":
            result.total_metadata_bytes += length


def _scan_png(path: Path, result: ScanResult) -> None:
    """Parse PNG chunks to detect metadata."""
    with open(path, "rb") as fh:
        header = fh.read(8)
        if header != b"\x89PNG\r\n\x1a\n":
            return

        while True:
            chunk_header = fh.read(8)
            if len(chunk_header) < 8:
                break
            chunk_len = struct.unpack(">I", chunk_header[:4])[0]
            chunk_type = chunk_header[4:8]
            chunk_data = fh.read(chunk_len)
            fh.read(4)  # CRC

            if chunk_type == b"eXIf":
                result.has_exif = True
                result.total_metadata_bytes += chunk_len
                _check_exif_software(chunk_data, result)
            elif chunk_type in (b"tEXt", b"zTXt", b"iTXt"):
                text = chunk_data.decode("utf-8", errors="replace")
                if "XML:com.adobe.xmp" in text or "<?xpacket" in text:
                    result.has_xmp = True
                    result.total_metadata_bytes += chunk_len
                    _check_xmp_for_ai(text, result)
            elif chunk_type == C2PA_PNG_CHUNK:
                result.has_c2pa = True
                result.total_metadata_bytes += chunk_len

            if chunk_type == b"IDAT":
                break  # Stop before image data for efficiency


def _scan_generic(path: Path, result: ScanResult) -> None:
    """Fallback scanner using Pillow's info dict."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            info = img.info or {}

            if img.format in ("TIFF",) and hasattr(img, "tag_v2"):
                result.has_exif = True
                _check_pillow_tags(img, result)
            if "exif" in info:
                result.has_exif = True
                result.total_metadata_bytes += len(info["exif"])
            if "xmp" in info:
                result.has_xmp = True
                xmp_text = info["xmp"].decode("utf-8", errors="replace") if isinstance(info["xmp"], bytes) else info["xmp"]
                _check_xmp_for_ai(xmp_text, result)
            if "icc_profile" in info:
                pass  # ICC is not AI metadata; ignore
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_c2pa_jumbf(segment: bytes) -> bool:
    """Return True if a JPEG APP11 segment contains a C2PA JUMBF box."""
    # JUMBF header: 4-byte length + 4-byte type "jumb" + nested box with "c2pa"
    if len(segment) < 16:
        return False
    # Check for "jumb" or "jumd" JUMBF box identifiers
    if b"jumb" in segment[:32] or b"jumd" in segment[:32]:
        if b"c2pa" in segment[:256]:
            return True
    # Also check for raw "c2pa" box type in first 512 bytes
    return C2PA_BOX_TYPE in segment[:512]


def _check_exif_software(exif_bytes: bytes, result: ScanResult) -> None:
    """Scan raw EXIF bytes for AI tool software signatures."""
    lower = exif_bytes.lower()
    for sig in AI_SOFTWARE_SIGNATURES:
        if sig.encode() in lower:
            if sig not in result.ai_software_tags:
                result.ai_software_tags.append(sig)


def _check_xmp_for_ai(xmp_text: str, result: ScanResult) -> None:
    """Scan XMP text for C2PA namespace or known AI tool references."""
    lower = xmp_text.lower()
    if "c2pa.org" in lower or "c2pa:" in lower:
        result.has_c2pa = True
        result.xmp_snippet = xmp_text[:300]
    for sig in AI_SOFTWARE_SIGNATURES:
        if sig in lower:
            if sig not in result.ai_software_tags:
                result.ai_software_tags.append(sig)


def _check_pillow_tags(img, result: ScanResult) -> None:
    """For TIFF/HEIC: check Pillow tag values for AI software names."""
    try:
        tag_v2 = getattr(img, "tag_v2", {})
        # Tag 305 = Software
        software = tag_v2.get(305, "")
        if isinstance(software, bytes):
            software = software.decode("utf-8", errors="replace")
        software_lower = str(software).lower()
        for sig in AI_SOFTWARE_SIGNATURES:
            if sig in software_lower and sig not in result.ai_software_tags:
                result.ai_software_tags.append(sig)
    except Exception:
        pass
