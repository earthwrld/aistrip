"""
cli.py — Click-based CLI for aistrip.

Usage:
    aistrip --scan  <file_or_folder>
    aistrip --clean <file_or_folder>
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich import box

from aistrip import __version__
from aistrip.scanner import ScanResult, scan_file
from aistrip.cleaner import CleanResult, clean_file

console = Console()

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".heic", ".heif"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_images(target: Path) -> list[Path]:
    """Return a list of supported image files from a file or directory."""
    if target.is_file():
        if target.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [target]
        raise click.BadParameter(
            f"Unsupported file type '{target.suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if target.is_dir():
        files = [
            f for f in sorted(target.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            raise click.UsageError(f"No supported image files found in '{target}'.")
        return files
    raise click.BadParameter(f"'{target}' is not a valid file or directory.")


def _render_scan_table(results: list[ScanResult]) -> None:
    """Render a rich table summarising scan results."""
    table = Table(
        title="Scan Results",
        box=box.ROUNDED,
        highlight=True,
        show_lines=True,
    )
    table.add_column("File", style="cyan", no_wrap=False, max_width=40)
    table.add_column("C2PA", justify="center")
    table.add_column("EXIF", justify="center")
    table.add_column("XMP", justify="center")
    table.add_column("IPTC", justify="center")
    table.add_column("AI Software", style="yellow")
    table.add_column("Meta Bytes", justify="right", style="dim")

    for r in results:
        c2pa_icon = "[red]✖ YES[/red]" if r.has_c2pa else "[green]✔ no[/green]"
        exif_icon  = "[yellow]●[/yellow]" if r.has_exif else "[dim]–[/dim]"
        xmp_icon   = "[yellow]●[/yellow]" if r.has_xmp  else "[dim]–[/dim]"
        iptc_icon  = "[yellow]●[/yellow]" if r.has_iptc else "[dim]–[/dim]"
        ai_tags    = ", ".join(r.ai_software_tags) if r.ai_software_tags else "[dim]none[/dim]"
        meta_bytes = f"{r.total_metadata_bytes:,}" if r.total_metadata_bytes else "[dim]–[/dim]"

        table.add_row(
            r.path.name,
            c2pa_icon,
            exif_icon,
            xmp_icon,
            iptc_icon,
            ai_tags,
            meta_bytes,
        )

    console.print(table)

    # Summary
    ai_count = sum(1 for r in results if r.has_ai_metadata)
    if ai_count:
        console.print(
            f"\n[bold red]⚠  {ai_count}/{len(results)} file(s) contain AI metadata "
            f"that may trigger Instagram's 'AI Info' label.[/bold red]"
        )
        console.print("   Run [bold cyan]aistrip --clean <path>[/bold cyan] to remove it.\n")
    else:
        console.print(
            f"\n[bold green]✔  No AI metadata detected in {len(results)} file(s).[/bold green]\n"
        )


def _render_clean_table(results: list[CleanResult]) -> None:
    """Render a rich table summarising clean results."""
    table = Table(
        title="Clean Results",
        box=box.ROUNDED,
        highlight=True,
        show_lines=True,
    )
    table.add_column("Source File", style="cyan", no_wrap=False, max_width=35)
    table.add_column("Output File", style="magenta", no_wrap=False, max_width=35)
    table.add_column("Status", justify="center")
    table.add_column("Bytes Removed", justify="right", style="dim")

    for r in results:
        status = "[green]✔ cleaned[/green]" if r.success else f"[red]✖ failed[/red]"
        out_name = r.output.name if r.output else "[dim]–[/dim]"
        removed = f"{r.bytes_removed:,}" if r.success else (r.error or "")

        table.add_row(r.source.name, out_name, status, removed)

    console.print(table)

    ok = sum(1 for r in results if r.success)
    fail = len(results) - ok
    if ok:
        console.print(f"\n[bold green]✔  {ok} file(s) cleaned successfully.[/bold green]")
    if fail:
        console.print(f"[bold red]✖  {fail} file(s) failed.[/bold red]")
    console.print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", message="aistrip %(version)s")
@click.option(
    "--scan", "mode",
    flag_value="scan",
    help="Scan file(s) for AI / C2PA metadata without modifying them.",
)
@click.option(
    "--clean", "mode",
    flag_value="clean",
    help="Remove all metadata and save as <name>_clean.<ext>.",
)
@click.argument(
    "target",
    type=click.Path(exists=True, path_type=Path),
    required=True,
)
def main(mode: str | None, target: Path) -> None:
    """
    \b
    aistrip — Remove AI metadata from images so platforms like Instagram
    won't auto-label your photos as "AI content".

    \b
    Examples:
      aistrip --scan  photo.jpg
      aistrip --scan  ./photos/
      aistrip --clean photo.jpg
      aistrip --clean ./photos/
    """
    if mode is None:
        raise click.UsageError("You must specify either --scan or --clean.")

    console.print(
        Panel.fit(
            f"[bold cyan]aistrip[/bold cyan] [dim]v{__version__}[/dim]  •  "
            f"mode: [bold]{mode}[/bold]  •  target: [italic]{target}[/italic]",
            border_style="cyan",
        )
    )

    images = _collect_images(target)
    console.print(f"  Found [bold]{len(images)}[/bold] image(s) to process.\n")

    if mode == "scan":
        scan_results: list[ScanResult] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Scanning…", total=len(images))
            for img in images:
                result = scan_file(img)
                scan_results.append(result)
                progress.advance(task)

        _render_scan_table(scan_results)

    elif mode == "clean":
        clean_results: list[CleanResult] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Cleaning…", total=len(images))
            for img in images:
                result = clean_file(img)
                clean_results.append(result)
                progress.advance(task)

        _render_clean_table(clean_results)
