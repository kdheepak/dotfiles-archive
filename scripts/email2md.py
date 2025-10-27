#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "extract-msg",
#     "html2text",
#     "typer",
#     "rich",
# ]
# ///
import re
import extract_msg
import html2text
import typer
from rich.console import Console
from rich.progress import track
from pathlib import Path

console = Console()
app = typer.Typer(add_completion=False)


def clean_text(text: str) -> str:
    """Thoroughly clean text of null bytes and non-printable characters."""
    if not text:
        return ""

    # Remove null bytes first
    text = text.replace("\x00", "")

    # Remove other problematic control characters but keep newlines, tabs, carriage returns
    cleaned_chars = []
    for char in text:
        # Keep printable characters and essential whitespace
        if char.isprintable() or char in "\n\r\t":
            cleaned_chars.append(char)
        # Replace other control characters with space
        elif ord(char) < 32:
            cleaned_chars.append(" ")

    return "".join(cleaned_chars)


def sanitize_filename(name: str) -> str:
    """Remove invalid characters for filenames."""
    if not name:
        return "untitled"
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name).strip("_") or "untitled"


def make_unique_filename(filepath: Path, existing_files: set) -> Path:
    """Generate a unique filename to avoid overwrites."""
    if str(filepath) not in existing_files:
        return filepath

    stem = filepath.stem
    suffix = filepath.suffix
    parent = filepath.parent
    counter = 1

    while True:
        new_name = f"{stem}_{counter}{suffix}"
        new_path = parent / new_name
        if str(new_path) not in existing_files:
            return new_path
        counter += 1


def safe_decode_html(html_content) -> str:
    """Safely decode HTML content to string."""
    if isinstance(html_content, bytes):
        # Try multiple encodings
        for encoding in ["utf-8", "utf-16", "latin-1", "cp1252"]:
            try:
                decoded = html_content.decode(encoding, errors="ignore")
                # Clean immediately after decoding
                return clean_text(decoded)
            except (UnicodeDecodeError, LookupError):
                continue
        # Fallback: force decode with replacement
        return clean_text(html_content.decode("utf-8", errors="replace"))

    return clean_text(str(html_content))


def convert_msg_to_md(
    msg_path: Path,
    output_md: Path,
    assets_folder: Path = Path("assets"),
):
    # Load message
    msg = extract_msg.Message(str(msg_path))
    subject = clean_text(msg.subject or "No Subject")
    sender = clean_text(msg.sender or "Unknown Sender")
    date = clean_text(str(msg.date) if msg.date else "")

    # Get the best available body content
    body = ""
    if msg.htmlBody:
        # HTML body takes precedence, decode it safely
        body = safe_decode_html(msg.htmlBody)
    elif msg.body:
        # Fallback to plain text body
        body = clean_text(msg.body)

    # Ensure assets folder exists
    assets_folder.mkdir(parents=True, exist_ok=True)

    # Extract attachments and build CID mapping
    attachment_links = {}
    cid_to_filename = {}
    existing_files = set()

    for att in track(msg.attachments, description="Extracting attachments..."):
        # Get original filename
        original_filename = att.longFilename or att.shortFilename or "attachment"
        filename = sanitize_filename(original_filename)

        # Handle empty or generic filenames
        if not filename or filename == "untitled":
            # Try to determine file extension from data
            ext = ""
            if hasattr(att, "mimeType") and att.mimeType:
                if "png" in att.mimeType.lower():
                    ext = ".png"
                elif "jpeg" in att.mimeType.lower() or "jpg" in att.mimeType.lower():
                    ext = ".jpg"
                elif "gif" in att.mimeType.lower():
                    ext = ".gif"

            # Generate a name based on content ID or index
            if hasattr(att, "cid") and att.cid:
                base_name = sanitize_filename(
                    att.cid.replace("@", "").replace("<", "").replace(">", "")
                )
                filename = f"{base_name}{ext}"
            else:
                filename = f"attachment_{len(attachment_links) + 1}{ext}"

        # Ensure unique filename
        filepath = assets_folder / filename
        filepath = make_unique_filename(filepath, existing_files)
        existing_files.add(str(filepath))

        try:
            with open(filepath, "wb") as f:
                f.write(att.data)

            # Map both filename and CID
            attachment_links[filepath.name] = filepath

            # Build CID mapping for inline images
            if hasattr(att, "cid") and att.cid:
                # Clean the CID (remove angle brackets)
                clean_cid = att.cid.strip("<>")
                cid_to_filename[clean_cid] = filepath.name
                # Also map without @ symbol variations
                cid_to_filename[clean_cid.replace("@", "")] = filepath.name

        except Exception as e:
            console.print(
                f"[yellow]Warning: Could not save attachment {filename}: {e}[/yellow]"
            )

    # Replace inline images in HTML body using CID mapping
    if body and "<html" in body.lower() and cid_to_filename:
        console.print("[blue]Replacing CIDs in HTML content...[/blue]")

        # Replace various CID formats
        for cid, filename in cid_to_filename.items():
            replacement_path = f"./{assets_folder.name}/{filename}"

            # Common CID patterns in HTML
            patterns = [
                # Standard cid: references
                (rf"cid:{re.escape(cid)}", replacement_path),
                (
                    rf"cid:{re.escape(cid.replace(' ', '%20'))}",
                    replacement_path,
                ),  # URL encoded
                # src attribute patterns
                (
                    rf'src\s*=\s*["\']cid:{re.escape(cid)}["\']',
                    f'src="{replacement_path}"',
                ),
                (
                    rf'src\s*=\s*["\']cid:{re.escape(cid.replace(" ", "%20"))}["\']',
                    f'src="{replacement_path}"',
                ),
                # Without quotes
                (rf"src\s*=\s*cid:{re.escape(cid)}", f'src="{replacement_path}"'),
                # Background image patterns
                (
                    rf'background-image\s*:\s*url\(["\']?cid:{re.escape(cid)}["\']?\)',
                    f'background-image: url("{replacement_path}")',
                ),
            ]

            for pattern, replacement in patterns:
                old_body = body
                body = re.sub(pattern, replacement, body, flags=re.IGNORECASE)
                if old_body != body:
                    console.print(f"[green]Replaced CID {cid} → {filename}[/green]")

        # Also try to catch any remaining cid: references that might have been missed
        remaining_cids = re.findall(r'cid:([^"\'\s>]+)', body, re.IGNORECASE)
        if remaining_cids:
            console.print(
                f"[yellow]Warning: Found unreplaced CIDs: {remaining_cids}[/yellow]"
            )
            # Try to match them with available filenames
            for remaining_cid in remaining_cids:
                # Look for similar filenames
                for filename in attachment_links.keys():
                    if (
                        remaining_cid.lower() in filename.lower()
                        or filename.lower() in remaining_cid.lower()
                    ):
                        replacement_path = f"./{assets_folder.name}/{filename}"
                        body = re.sub(
                            rf"cid:{re.escape(remaining_cid)}",
                            replacement_path,
                            body,
                            flags=re.IGNORECASE,
                        )
                        console.print(
                            f"[green]Matched remaining CID {remaining_cid} → {filename}[/green]"
                        )
                        break

    # Convert HTML to Markdown if it's HTML content
    if body and (
        "<html" in body.lower() or "<div" in body.lower() or "<p" in body.lower()
    ):
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.body_width = 0  # Don't wrap lines

        try:
            body = h.handle(body)
        except Exception as e:
            console.print(f"[yellow]Warning: HTML conversion failed: {e}[/yellow]")
            # Fallback: strip HTML tags manually
            body = re.sub(r"<[^>]+>", "", body)

    # Final comprehensive cleanup
    body = clean_text(body)

    # Check for any remaining CID references in the final markdown
    remaining_cids = re.findall(r'cid:([^"\'\s\)]+)', body, re.IGNORECASE)
    if remaining_cids:
        console.print(
            f"[yellow]Warning: Markdown still contains CID references: {remaining_cids}[/yellow]"
        )
        # Remove any remaining cid: references as a last resort
        body = re.sub(
            r'cid:[^\s\)\]"\']+', "[MISSING_IMAGE]", body, flags=re.IGNORECASE
        )

    # Remove excessive whitespace but preserve paragraph breaks
    body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body)  # Multiple newlines to double
    body = re.sub(r"[ \t]+", " ", body)  # Multiple spaces/tabs to single space
    body = body.strip()

    # Check if file exists and ask before overwriting
    if output_md.exists():
        overwrite = typer.confirm(
            f"File '{output_md.name}' already exists. Overwrite?", default=False
        )
        if not overwrite:
            console.print("[yellow]Aborted by user.[/yellow]")
            raise typer.Exit()

    # Write markdown file with explicit encoding and error handling
    try:
        with open(output_md, "w", encoding="utf-8", errors="replace") as f:
            f.write(f"# {subject}\n\n")
            f.write(f"**From:** {sender}  \n")
            f.write(f"**Date:** {date}\n\n")
            if attachment_links:
                f.write("## Attachments\n\n")
                for filename, filepath in attachment_links.items():
                    f.write(f"- [{filename}](./{assets_folder.name}/{filename})\n")
                f.write("\n")
            f.write("## Content\n\n")
            f.write(body)
            f.write("\n")
    except Exception as e:
        console.print(f"[red]Error writing file: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"✅ [green]Converted[/green] {msg_path} → {output_md}")


@app.command()
def main(
    msg_file: Path = typer.Argument(..., help="The Outlook .msg file to convert."),
    md_file: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output markdown file (default: sanitized subject or msg filename)",
    ),
    assets: Path = typer.Option("assets", "--assets", "-a", help="Assets folder"),
):
    """
    Convert an Outlook .msg email into a Markdown file with extracted images.
    """
    if not msg_file.exists():
        console.print(f"[red]Error: File {msg_file} does not exist.[/red]")
        raise typer.Exit(1)

    # Default filename: based on subject, otherwise msg filename
    try:
        msg = extract_msg.Message(str(msg_file))
        base_name = sanitize_filename(msg.subject or msg_file.stem)
    except Exception as e:
        console.print(f"[red]Error reading MSG file: {e}[/red]")
        raise typer.Exit(1)

    md_file = md_file or Path.cwd() / f"{base_name}.md"

    try:
        convert_msg_to_md(msg_file, md_file, assets)
    except Exception as e:
        console.print(f"[red]Conversion failed: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
