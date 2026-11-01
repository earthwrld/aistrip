"""Basic tests for aistrip scanner and cleaner."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers to build minimal test images
# ---------------------------------------------------------------------------

def _make_jpeg(tmp_path: Path, with_exif: bool = False) -> Path:
    img = Image.new("RGB", (64, 64), color=(100, 150, 200))
    buf = io.BytesIO()
    if with_exif:
        import piexif
        exif = piexif.dump({"0th": {piexif.ImageIFD.Software: b"Canva"}})
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    path = tmp_path / "test.jpg"
    path.write_bytes(buf.getvalue())
    return path


def _make_png(tmp_path: Path) -> Path:
    img = Image.new("RGB", (64, 64), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path = tmp_path / "test.png"
    path.write_bytes(buf.getvalue())
    return path


# ---------------------------------------------------------------------------
# Scanner tests
# ---------------------------------------------------------------------------

class TestScanner:
    def test_clean_jpeg_has_no_ai_metadata(self, tmp_path):
        from aistrip.scanner import scan_file

        path = _make_jpeg(tmp_path, with_exif=False)
        result = scan_file(path)
        assert not result.has_c2pa
        assert not result.ai_software_tags

    def test_jpeg_with_canva_exif_detected(self, tmp_path):
        from aistrip.scanner import scan_file

        try:
            import piexif  # noqa: F401
        except ImportError:
            pytest.skip("piexif not installed")

        path = _make_jpeg(tmp_path, with_exif=True)
        result = scan_file(path)
        assert result.has_exif
        assert "canva" in result.ai_software_tags

    def test_png_scan_no_metadata(self, tmp_path):
        from aistrip.scanner import scan_file

        path = _make_png(tmp_path)
        result = scan_file(path)
        # A freshly created PNG has no AI metadata
        assert not result.has_c2pa
        assert not result.ai_software_tags

    def test_scan_result_has_any_metadata_false_for_clean_image(self, tmp_path):
        from aistrip.scanner import scan_file

        path = _make_jpeg(tmp_path)
        result = scan_file(path)
        # has_exif may be True or False depending on Pillow's JPEG save behaviour
        # but has_ai_metadata must be False
        assert not result.has_ai_metadata


# ---------------------------------------------------------------------------
# Cleaner tests
# ---------------------------------------------------------------------------

class TestCleaner:
    def test_clean_jpeg_creates_output_file(self, tmp_path):
        from aistrip.cleaner import clean_file

        path = _make_jpeg(tmp_path)
        result = clean_file(path)
        assert result.success, result.error
        assert result.output is not None
        assert result.output.exists()
        assert result.output.name == "test_clean.jpg"

    def test_clean_jpeg_output_is_valid_image(self, tmp_path):
        from aistrip.cleaner import clean_file

        path = _make_jpeg(tmp_path)
        result = clean_file(path)
        assert result.success
        with Image.open(result.output) as img:
            assert img.size == (64, 64)

    def test_clean_png_creates_output_file(self, tmp_path):
        from aistrip.cleaner import clean_file

        path = _make_png(tmp_path)
        result = clean_file(path)
        assert result.success, result.error
        assert result.output.name == "test_clean.png"

    def test_clean_removes_exif_from_jpeg(self, tmp_path):
        from aistrip.cleaner import clean_file
        from aistrip.scanner import scan_file

        try:
            import piexif  # noqa: F401
        except ImportError:
            pytest.skip("piexif not installed")

        dirty = _make_jpeg(tmp_path, with_exif=True)
        result = clean_file(dirty)
        assert result.success

        scan = scan_file(result.output)
        assert not scan.ai_software_tags, "AI software tags should be removed after cleaning"

    def test_clean_unsupported_format_returns_error(self, tmp_path):
        from aistrip.cleaner import clean_file

        path = tmp_path / "file.bmp"
        path.write_bytes(b"BM\x00" * 10)
        result = clean_file(path)
        assert not result.success
        assert "Unsupported" in (result.error or "")


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_scan_single_file(self, tmp_path):
        from click.testing import CliRunner
        from aistrip.cli import main

        path = _make_jpeg(tmp_path)
        runner = CliRunner()
        res = runner.invoke(main, ["--scan", str(path)])
        assert res.exit_code == 0
        assert "Scan Results" in res.output

    def test_clean_single_file(self, tmp_path):
        from click.testing import CliRunner
        from aistrip.cli import main

        path = _make_jpeg(tmp_path)
        runner = CliRunner()
        res = runner.invoke(main, ["--clean", str(path)])
        assert res.exit_code == 0
        assert "Clean Results" in res.output
        assert (tmp_path / "test_clean.jpg").exists()

    def test_no_mode_raises_usage_error(self, tmp_path):
        from click.testing import CliRunner
        from aistrip.cli import main

        path = _make_jpeg(tmp_path)
        runner = CliRunner()
        res = runner.invoke(main, [str(path)])
        assert res.exit_code != 0

    def test_scan_folder_batch(self, tmp_path):
        from click.testing import CliRunner
        from aistrip.cli import main

        _make_jpeg(tmp_path)
        _make_png(tmp_path)
        runner = CliRunner()
        res = runner.invoke(main, ["--scan", str(tmp_path)])
        assert res.exit_code == 0
        assert "Scan Results" in res.output
