#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx",
#     "rich",
#     "typer",
#     "certifi",
#     "truststore",
# ]
# ///
"""
GAMS (General Algebraic Modeling System) installer script that downloads and installs GAMS
"""

import platform
import shutil
import subprocess
from pathlib import Path
import os
import ssl
import certifi
import truststore
import typer
import httpx
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm
from rich.panel import Panel

# Configure console
console = Console()

app = typer.Typer(name=__name__, help=__doc__, add_completion=False)


GAMS_VERSION_NUMBER = "50.2.0"
SCRIPT_NAME = f"./{Path(__file__).name}"


def create_ssl_context(
    verify: bool = True,
    use_system_certs: bool = False,
    ca_cert_file: str | None = None,
    ca_cert_dir: str | None = None,
) -> ssl.SSLContext | bool:
    """
    Create an SSL context with the specified verification settings.
    """
    if not verify:
        console.print(
            "⚠️  SSL verification disabled - connection may be insecure!",
            style="yellow bold",
        )
        return False

    if ca_cert_file is None:
        ca_cert_file = os.environ.get("SSL_CERT_FILE")
    if ca_cert_file is None:
        user_ca_cert = Path.home() / ".config" / "certs" / "cacert.pem"
        if user_ca_cert.exists():
            ca_cert_file = str(user_ca_cert)
            console.print(
                f"🔒 Found user CA certificate: {ca_cert_file}", style="cyan dim"
            )
    if ca_cert_dir is None:
        ca_cert_dir = os.environ.get("SSL_CERT_DIR")
    if ca_cert_file and not Path(ca_cert_file).exists():
        console.print(
            f"❌ CA certificate file not found: {ca_cert_file}", style="red bold"
        )
        raise typer.Exit(1)
    if ca_cert_dir and not Path(ca_cert_dir).exists():
        console.print(
            f"❌ CA certificate directory not found: {ca_cert_dir}", style="red bold"
        )
        raise typer.Exit(1)
    if use_system_certs:
        if truststore is not None:
            console.print("🔒 Using system certificate store", style="cyan")
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        else:
            console.print("❌ truststore not available for system certs", style="red")
            raise typer.Exit(1)
    else:
        cafile = ca_cert_file or certifi.where()
        ctx = ssl.create_default_context(cafile=cafile, capath=ca_cert_dir)
        if ca_cert_file:
            console.print(
                f"🔒 Using custom CA certificate: {ca_cert_file}", style="cyan"
            )
        elif ca_cert_dir:
            console.print(f"🔒 Using CA certificates from: {ca_cert_dir}", style="cyan")
        else:
            console.print("🔒 Using certifi CA bundle", style="cyan")
    return ctx


class GAMSManager:
    BASE_URL = "https://d37drm4t2jghv5.cloudfront.net/distributions/"

    def __init__(self, version: str = GAMS_VERSION_NUMBER):
        if version.startswith("v"):
            version = version[1:]
        self.version = version
        self.console = console
        self.uname = platform.uname()
        self.os_type = platform.system().lower()

        # Setup installation directories
        install_dir = Path("~/local/bin").expanduser()
        _, info = self._get_download_info()
        self.info = info

        if self.is_windows():
            install_dir = install_dir / f"gams_{version.replace('.', '_')}"
            self.binary_install_dir = install_dir
        elif self.is_linux() or self.is_macos():
            install_dir = install_dir / f"gams_{version.replace('.', '_')}"
            self.binary_install_dir = install_dir / self.info

        install_dir.mkdir(parents=True, exist_ok=True)
        self.install_dir = install_dir
        self.download_filename = self._get_download_filename()

    def is_windows(self) -> bool:
        return self.os_type == "windows"

    def is_linux(self) -> bool:
        return self.os_type == "linux"

    def is_macos(self) -> bool:
        return self.os_type == "darwin"

    def is_installed(self) -> str | None:
        """Check if GAMS is installed and return path"""
        return shutil.which("gams")

    def get_installed_version(self) -> str | None:
        """Get currently installed GAMS version"""
        if not self.is_installed():
            return None

        try:
            # Run gams without arguments to get version info
            result = subprocess.run(
                ["gams"],
                capture_output=True,
                text=True,
                timeout=10,
                input="\n",  # Send enter to exit GAMS quickly
            )

            # Parse version from output (look for patterns like "49.6.1")
            import re

            # Look for version pattern in the output
            version_pattern = r"\b(\d+\.\d+\.\d+)\b"

            # Check both stdout and stderr as GAMS might output to either
            output_text = result.stdout + result.stderr

            matches = re.findall(version_pattern, output_text)
            if matches:
                # Return the first version found (they should all be the same)
                return matches[0]

            return "Unknown"

        except Exception:
            return "Unknown"

    def _get_download_info(self) -> tuple[str, str]:
        """Return GAMS source URL fragment and path fragment for extracted files"""
        if self.is_macos():
            arch = {"arm64": "arm64", "x86_64": "x64_64"}[self.uname.machine]
            sfx = "_sfx" if self.version < "43" else ""
            filename, fragment = f"macosx/osx_{arch}_sfx.exe", f"osx_{arch}{sfx}"
        elif self.is_linux():
            arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64_64"}[
                self.uname.machine
            ]
            sfx = "_sfx" if self.version < "43" else ""
            filename, fragment = f"linux/linux_{arch}_sfx.exe", f"linux_{arch}{sfx}"
        elif self.is_windows():
            filename, fragment = "windows/windows_x64_64.exe", "windows_x64_64"
        else:
            console.print(f"❌ Unsupported platform: {self.uname}", style="red")
            raise typer.Exit(1)

        v_major, v_minor, _ = self.version.split(".", maxsplit=2)
        return filename, f"gams{v_major}.{v_minor}_{fragment}"

    def _get_url(self) -> str:
        """Get download URL for GAMS installer"""
        filename, _ = self._get_download_info()
        return f"{self.BASE_URL}{self.version}/{filename}"

    def _get_download_filename(self) -> Path:
        """Get local filename for downloaded installer"""
        downloads_dir = Path("~/Downloads").expanduser()

        if self.is_macos():
            return downloads_dir / "gams_osx_sfx.exe"
        elif self.is_linux():
            return downloads_dir / "gams_linux_sfx.exe"
        elif self.is_windows():
            return downloads_dir / "gams_windows.exe"
        else:
            console.print(f"❌ Unsupported platform: {self.uname}", style="red")
            raise typer.Exit(1)

    def download_with_progress(
        self,
        url: str,
        destination: Path,
        force: bool = False,
        ssl_context: ssl.SSLContext | bool = True,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> bool:
        """Download file from URL to destination with progress bar and SSL support"""
        if destination.exists() and not force:
            console.print(f"⚠️  File already exists: {destination}", style="yellow")
            if not Confirm.ask("Do you want to redownload it?"):
                console.print("Download skipped", style="blue")
                return True
        retry_count = 0
        last_error = None
        while retry_count < max_retries:
            try:
                client = httpx.Client(verify=ssl_context, timeout=timeout)
                with client.stream("GET", url, follow_redirects=True) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get("content-length", 0))
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        DownloadColumn(),
                        TransferSpeedColumn(),
                        console=console,
                    ) as progress:
                        download_task = progress.add_task(
                            f"Downloading {destination.name}", total=total_size
                        )
                        with open(destination, "wb") as f:
                            for chunk in response.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                                progress.update(download_task, advance=len(chunk))
                console.print(f"✅ Downloaded: {destination}", style="green")
                return True
            except httpx.ConnectError as e:
                if "certificate" in str(e).lower():
                    console.print(f"❌ SSL Certificate error: {e}", style="red")
                    console.print("💡 Tips:", style="yellow")
                    console.print(
                        "   • Try --use-system-certs if in a corporate environment",
                        style="dim",
                    )
                    console.print(
                        "   • Use --ca-cert-file with your organization's CA certificate",
                        style="dim",
                    )
                    console.print(
                        "   • Use --no-verify-ssl as a last resort (insecure!)",
                        style="dim",
                    )
                    return False
                else:
                    last_error = e
                    retry_count += 1
                    if retry_count < max_retries:
                        console.print(
                            f"⚠️  Connection error, retrying ({retry_count}/{max_retries})...",
                            style="yellow",
                        )
                        import time

                        time.sleep(2**retry_count)
            except httpx.HTTPError as e:
                console.print(f"❌ HTTP error: {e}", style="red")
                console.print(f"[dim]Failed to download from: {url}[/dim]", style="red")
                return False
            except Exception as e:
                console.print(f"❌ Download failed: {e}", style="red")
                return False
            finally:
                if "client" in locals():
                    client.close()
        console.print(
            f"❌ Failed after {max_retries} attempts: {last_error}", style="red"
        )
        return False

    def install_gams(self) -> bool:
        """Install GAMS from downloaded installer"""
        if not self.download_filename.exists():
            console.print(
                f"❌ Installer not found: {self.download_filename}", style="red"
            )
            return False

        console.print(f"🔧 Installing GAMS to {self.install_dir}...", style="yellow")

        try:
            if self.is_windows():
                with console.status("[yellow]Installing GAMS on Windows..."):
                    result = subprocess.run(
                        [
                            str(self.download_filename),
                            "/SP-",
                            "/VERYSILENT",
                            "/NORESTART",
                            f"/DIR={self.install_dir}",
                        ],
                        check=False,
                        timeout=300,
                    )
            else:
                # Unix-like systems (Linux/macOS)
                with console.status("[yellow]Installing GAMS on Unix..."):
                    # Make executable
                    subprocess.run(
                        ["chmod", "+x", str(self.download_filename)], check=True
                    )
                    # Install
                    result = subprocess.run(
                        [
                            str(self.download_filename),
                            "-d",
                            str(self.install_dir),
                            "-o",
                        ],
                        check=False,
                        timeout=300,
                    )

            if result.returncode == 0:
                console.print(
                    "✅ GAMS installation completed successfully!", style="green"
                )
                return True
            else:
                console.print(
                    f"❌ Installation failed with exit code: {result.returncode}",
                    style="red",
                )
                return False

        except subprocess.TimeoutExpired:
            console.print("❌ Installation timed out after 5 minutes", style="red")
            return False
        except Exception as e:
            console.print(f"❌ Installation failed: {e}", style="red")
            return False

    def add_to_path(self, shell: str) -> None:
        """Add GAMS to system PATH for specified shell"""
        console.print(f"🔧 Adding GAMS to PATH for {shell}...", style="yellow")

        if shell in ["bash", "zsh"]:
            profile_path = Path.home() / (
                ".bash_profile" if shell == "bash" else ".zshrc"
            )
            content = f'export PATH="{self.binary_install_dir}:$PATH"'
            self._add_to_profile(profile_path, content)

        elif shell == "powershell":
            profile_path = (
                Path.home() / "Documents" / "WindowsPowerShell" / "profile.ps1"
            )
            content = f'$env:Path += ";{self.binary_install_dir}"'
            self._add_to_profile(profile_path, content)

        else:
            console.print(f"❌ Unsupported shell: {shell}", style="red")

    def _add_to_profile(self, profile_path: Path, content: str) -> None:
        """Add content to shell profile with managed block"""
        block_start = "# >>> gams initialize >>>"
        block_end = "# <<< gams initialize <<<"

        block_content = f"""
{block_start}
# !! Contents within this block are managed by GAMS installer script !!
{content}
{block_end}
"""

        if profile_path.exists():
            with profile_path.open("r") as f:
                existing_content = f.read()
            if block_start in existing_content and block_end in existing_content:
                console.print(
                    f"⚠️  GAMS block already exists in {profile_path}", style="yellow"
                )
                return

        try:
            with profile_path.open("a") as f:
                f.write(block_content)
            console.print(f"✅ Added GAMS to {profile_path}", style="green")
            console.print("🔄 Restart your terminal to apply changes", style="blue")
        except Exception as e:
            console.print(f"❌ Failed to update {profile_path}: {e}", style="red")


# CLI Commands
@app.command()
def check(version: str = typer.Option(GAMS_VERSION_NUMBER, help="Version of GAMS")):
    """Check GAMS installation status and show version info"""
    console.print("🔍 Checking GAMS installation...", style="cyan")

    gams = GAMSManager(version=version)

    if gams.is_installed():
        v = gams.get_installed_version()
        if v is None:
            console.print("GAMS is not installed")
        install_path = gams.is_installed()

        console.print("✅ GAMS is installed", style="green")
        if v:
            console.print(f"📦 Version: [green]{v}[/green]")
        if install_path:
            console.print(f"📂 Location: [dim]{install_path}[/dim]")

        console.print("\n💡 Available actions:", style="blue")
        console.print(
            f"   • '{SCRIPT_NAME} install --force' - Reinstall GAMS", style="dim"
        )
        console.print(
            f"   • '{SCRIPT_NAME} add-to-path --shell bash' - Add to PATH", style="dim"
        )
    else:
        console.print("❌ GAMS is not installed", style="red")
        console.print(f"💡 Run '{SCRIPT_NAME} install' to install GAMS", style="blue")
        console.print(
            f"💡 Run '{SCRIPT_NAME} download' to download installer only", style="blue"
        )


@app.command()
def info(version: str = typer.Option(GAMS_VERSION_NUMBER, help="Version of GAMS")):
    """Show information about GAMS version and download details"""
    console.print(Panel.fit("📋 GAMS Information", style="cyan bold"))

    gams = GAMSManager(version=version)

    info_text = f"""[bold]Version:[/bold] {gams.version}
[bold]Download URL:[/bold] {gams._get_url()}
[bold]Installer Name:[/bold] {gams.download_filename.name}
[bold]Install Directory:[/bold] {gams.install_dir}
[bold]Binary Directory:[/bold] {gams.binary_install_dir}"""

    console.print(Panel(info_text, border_style="dim"))
    console.print(
        "\n💡 GAMS is a General Algebraic Modeling System for optimization",
        style="blue",
    )


@app.command()
def download(
    version: str = typer.Option(GAMS_VERSION_NUMBER, help="Version of GAMS to install"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
    no_verify_ssl: bool = typer.Option(
        False, "--no-verify-ssl", help="Disable SSL verification (insecure!)"
    ),
    use_system_certs: bool = typer.Option(
        False, "--use-system-certs", help="Use system certificate store"
    ),
    ca_cert_file: str | None = typer.Option(
        None, "--ca-cert-file", help="Path to custom CA certificate file"
    ),
    ca_cert_dir: str | None = typer.Option(
        None, "--ca-cert-dir", help="Path to directory with CA certificates"
    ),
    timeout: float = typer.Option(
        60.0, "--timeout", help="Download timeout in seconds"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="Maximum retry attempts"),
):
    """Download GAMS installer with SSL/TLS support"""
    console.print(Panel.fit("📥 GAMS Downloader", style="bold cyan"))

    gams = GAMSManager(version=version)

    console.print(f"📥 Downloading GAMS {gams.version}...", style="cyan")
    console.print(f"[dim]URL: {gams._get_url()}[/dim]")
    console.print(f"[dim]Output: {gams.download_filename}[/dim]")

    ssl_context = create_ssl_context(
        verify=not no_verify_ssl,
        use_system_certs=use_system_certs,
        ca_cert_file=ca_cert_file,
        ca_cert_dir=ca_cert_dir,
    )
    if gams.download_with_progress(
        gams._get_url(),
        gams.download_filename,
        force,
        ssl_context=ssl_context,
        timeout=timeout,
        max_retries=max_retries,
    ):
        console.print("✅ Successfully downloaded GAMS installer", style="green")
        console.print(f"📂 Saved to: [green]{gams.download_filename}[/green]")
        console.print(f"💡 Run '{SCRIPT_NAME} install' to install", style="blue")
    else:
        raise typer.Exit(1)


@app.command()
def install(
    version: str = typer.Option(GAMS_VERSION_NUMBER, help="Version of GAMS to install"),
    force: bool = typer.Option(
        False, "--force", help="Force reinstall even if GAMS exists"
    ),
    no_verify_ssl: bool = typer.Option(
        False, "--no-verify-ssl", help="Disable SSL verification (insecure!)"
    ),
    use_system_certs: bool = typer.Option(
        False, "--use-system-certs", help="Use system certificate store"
    ),
    ca_cert_file: str | None = typer.Option(
        None, "--ca-cert-file", help="Path to custom CA certificate file"
    ),
    ca_cert_dir: str | None = typer.Option(
        None, "--ca-cert-dir", help="Path to directory with CA certificates"
    ),
    timeout: float = typer.Option(
        60.0, "--timeout", help="Download timeout in seconds"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="Maximum retry attempts"),
):
    """Download and install GAMS with SSL/TLS support"""
    console.print(Panel.fit("🚀 GAMS Installer", style="bold blue"))

    gams = GAMSManager(version=version)

    # Check if already installed
    if gams.is_installed() and not force:
        current_version = gams.get_installed_version()
        console.print("⚠️  GAMS is already installed!", style="yellow")

        version_info = ""
        if current_version:
            version_info += f"📦 Current version: [yellow]{current_version}[/yellow]\n"
        version_info += f"📥 Target version: [blue]{gams.version}[/blue]"

        console.print(version_info)

        if not Confirm.ask("Do you want to continue anyway?"):
            console.print("Installation cancelled", style="blue")
            return

    ssl_context = create_ssl_context(
        verify=not no_verify_ssl,
        use_system_certs=use_system_certs,
        ca_cert_file=ca_cert_file,
        ca_cert_dir=ca_cert_dir,
    )
    # Download if needed
    if not gams.download_filename.exists():
        console.print("🔍 Downloading GAMS installer...", style="cyan")
        if not gams.download_with_progress(
            gams._get_url(),
            gams.download_filename,
            ssl_context=ssl_context,
            timeout=timeout,
            max_retries=max_retries,
        ):
            raise typer.Exit(1)
    else:
        console.print(
            f"⚠️  Installer already exists: {gams.download_filename}", style="yellow"
        )
        if not force and Confirm.ask("Do you want to redownload it?"):
            if not gams.download_with_progress(
                gams._get_url(),
                gams.download_filename,
                force=True,
                ssl_context=ssl_context,
                timeout=timeout,
                max_retries=max_retries,
            ):
                raise typer.Exit(1)

    # Install
    if gams.install_gams():
        # Add to PATH for common shells
        if gams.is_windows():
            gams.add_to_path("powershell")
        else:
            gams.add_to_path("bash")
            gams.add_to_path("zsh")

        # Verify installation
        if gams.is_installed():
            new_version = gams.get_installed_version()
            if new_version:
                console.print(
                    f"🎉 Verified: GAMS version [green]{new_version}[/green] is installed"
                )
            else:
                console.print("🎉 Verified: GAMS is installed", style="green")
        else:
            console.print(
                "⚠️  Installation completed but verification failed", style="yellow"
            )
    else:
        raise typer.Exit(1)


@app.command()
def add_to_path(
    version: str = typer.Option(GAMS_VERSION_NUMBER, help="Version of GAMS to install"),
    shell: str = typer.Option(
        ..., "--shell", help="Shell type (bash, zsh, powershell)"
    ),
):
    """Add GAMS installation to system PATH"""
    console.print(Panel.fit("🔧 PATH Configuration", style="bold yellow"))

    gams = GAMSManager(version=GAMS_VERSION_NUMBER)

    if not gams.is_installed():
        console.print("❌ GAMS is not installed", style="red")
        console.print(f"💡 Run '{SCRIPT_NAME} install' first", style="blue")
        raise typer.Exit(1)

    valid_shells = ["bash", "zsh", "powershell"]
    if shell not in valid_shells:
        console.print(
            f"❌ Invalid shell. Choose from: {', '.join(valid_shells)}", style="red"
        )
        raise typer.Exit(1)

    gams.add_to_path(shell)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """
    VS Code installer script that downloads and installs Visual Studio Code for Windows
    with enhanced SSL/TLS support for corporate environments
    """
    if ctx.invoked_subcommand is None:
        # No command provided, show help
        ctx.get_help()
        raise typer.Exit(0)


if __name__ == "__main__":
    app()
