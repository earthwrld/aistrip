# aistrip 

`aistrip` is a Python CLI tool that **detects and removes AI provenance metadata** (C2PA manifests, EXIF, XMP, IPTC) from images. When you use AI-powered editing tools like **Canva's Magic Eraser**, **Adobe Firefly**, or **Photoshop's Generative Fill**, those tools embed a hidden digital fingerprint into your image files.

`aistrip` removes that fingerprint.

---

## Features

- **Scan mode**: inspect images and report exactly what AI metadata is present without modifying files
- **Clean mode**:  strip all AI metadata and save as a new `_clean` file (original is untouched)
- **Multi-format**:  supports JPEG, PNG, WebP, TIFF, HEIC/HEIF
- **Batch processing**: point it at a folder to process all images at once
- **100% local**: no files are uploaded anywhere; all processing happens on your machine
- **Rich output**: clean, colored terminal output with progress bars and summary tables

---

## Installation

**Requirements:** Python 3.9+

```bash
pip install aistrip
```

Or install from source:

```bash
git clone https://github.com/yourusername/aistrip.git
cd aistrip
pip install -e .
```

---

## Usage

### Scan a single file

Check if a photo has AI metadata without modifying it:

```bash
aistrip --scan photo.jpg
```

**Example output:**
```
╭─────────────────────────────────────────────────╮
│ aistrip v0.1.0  •  mode: scan  •  target: photo.jpg │
╰─────────────────────────────────────────────────╯
  Found 1 image(s) to process.

╭────────────────── Scan Results ──────────────────╮
│ File      │ C2PA    │ EXIF │ XMP │ AI Software   │
├───────────┼─────────┼──────┼─────┼───────────────┤
│ photo.jpg │ ✖ YES   │  ●   │  ●  │ canva         │
╰───────────────────────────────────────────────────╯

⚠  1/1 file(s) contain AI metadata that may trigger
   Instagram's 'AI Info' label.
   Run: aistrip --clean photo.jpg
```

### Clean a single file

Remove all AI metadata. Saves a new file as `photo_clean.jpg` (original untouched):

```bash
aistrip --clean photo.jpg
```

### Batch scan a folder

```bash
aistrip --scan ./my-photos/
```

### Batch clean a folder

```bash
aistrip --clean ./my-photos/
```

All cleaned files will be saved next to the originals with a `_clean` suffix.

---

## How it works

Modern AI editing tools embed metadata using the [**C2PA (Coalition for Content Provenance and Authenticity)**](https://c2pa.org/) standard. This creates a JUMBF manifest embedded in the image's binary data separate from the visible pixels.

`aistrip` works in two steps:

1. **Byte-level stripping** for JPEG files, it scans the file's marker structure and removes APP11 (C2PA/JUMBF), APP1 (EXIF/XMP), APP13 (IPTC), and Comment markers.
2. **Pillow re-encode** re-saves the image via Pillow, which naturally drops any remaining metadata chunks.

> **Note:** This tool removes **metadata-based** AI signals. It cannot remove pixel-level watermarks like Google's SynthID, which are embedded directly into the image pixels.

---

## Format support

| Format      | Scan | Clean | Notes                              |
|-------------|:----:|:-----:|------------------------------------|
| JPEG / JPG  | ✅   | ✅    | Full byte-level C2PA stripping     |
| PNG         | ✅   | ✅    | Removes caBX, eXIf, tEXt chunks   |
| WebP        | ✅   | ✅    | Re-encoded via Pillow              |
| TIFF        | ✅   | ✅    | Re-encoded via Pillow              |
| HEIC / HEIF | ✅   | ✅    | Requires `pillow-heif`; output: JPEG |

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Contributing

pr are welcome, just open issue first ig

---

## Disclaimer

This tool is intended for **legitimate use cases** such as protecting your privacy or removing metadata from photos you own. Please respect platform terms of service and applicable laws. Removing metadata does not grant you rights to content you don't own.

---

## License

MIT — see [LICENSE](LICENSE) for details.
