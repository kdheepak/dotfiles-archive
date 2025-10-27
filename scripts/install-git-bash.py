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
Git installer script that downloads and installs Git for Windows
"""

import os
import subprocess
from pathlib import Path
import tempfile
import ssl

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

import certifi
import truststore

# Configure console
console = Console()

app = typer.Typer(
    name=__name__, no_args_is_help=True, help=__doc__, add_completion=False
)


SCRIPT_NAME = f"./{Path(__file__).name}"

GIT_VERSION = "2.50.0"


def get_default_config(
    version: str = GIT_VERSION, editor: str = "VisualStudioCode"
) -> dict[str, str]:
    """Get default Git installation configuration"""
    username = os.environ.get("USERNAME", os.environ.get("USER", "User"))

    return {
        "version": version,
        "install_dir": f"C:\\Users\\{username}\\AppData\\Local\\Programs\\Git",
        "editor": editor,
        "default_branch": "main",
        "components": "icons,icons\\desktop,icons\\quicklaunch,ext,ext\\shellhere,ext\\guihere,gitlfs,assoc,assoc_sh,autoupdate,windowsterminal",
        "setup_type": "default",
        "path_option": "Cmd",
        "ssh_option": "OpenSSH",
        "curl_option": "OpenSSL",
        "crlf_option": "LFOnly",
        "bash_terminal": "MinTTY",
        "pull_behavior": "Rebase",
        "credential_manager": "Enabled",
        "performance_tweaks": "Enabled",
        "enable_symlinks": "Enabled",
        "enable_pseudo_console": "Enabled",
        "enable_fs_monitor": "Enabled",
    }


def check_git_installed() -> bool:
    """Check if Git is already installed"""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_git_download_url(version: str) -> str:
    """Get the download URL for a specific Git version"""
    return f"https://github.com/git-for-windows/git/releases/download/v{version}.windows.1/Git-{version}-64-bit.exe"


def get_installer_filename(version: str) -> str:
    """Get the installer filename for a specific Git version"""
    return f"Git-{version}-64-bit.exe"


def create_ssl_context(
    verify: bool = True,
    use_system_certs: bool = False,
    ca_cert_file: str | None = None,
    ca_cert_dir: str | None = None,
) -> ssl.SSLContext | bool:
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
        cafile = ca_cert_file or (certifi.where() if certifi else None)
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


def download_file_with_progress(
    url: str,
    destination: Path,
    ssl_context: ssl.SSLContext | bool = True,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> bool:
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
            console.print(f"✅ Downloaded: {destination.name}", style="green")
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
                    "   • Use --no-verify-ssl as a last resort (insecure!)", style="dim"
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
    console.print(f"❌ Failed after {max_retries} attempts: {last_error}", style="red")
    return False


def create_git_config(temp_dir: Path, config: dict[str, str]) -> Path:
    """Create Git installation configuration file"""
    config_content = f"""[Setup]
Dir={config["install_dir"]}
Group=Git
NoIcons=0
SetupType={config["setup_type"]}
Components={config["components"]}
Tasks=
EditorOption={config["editor"]}
DefaultBranchOption={config["default_branch"]}
PathOption={config["path_option"]}
SSHOption={config["ssh_option"]}
TortoiseOption=false
CURLOption={config["curl_option"]}
CRLFOption={config["crlf_option"]}
BashTerminalOption={config["bash_terminal"]}
GitPullBehaviorOption={config["pull_behavior"]}
UseCredentialManager={config["credential_manager"]}
PerformanceTweaksFSCache={config["performance_tweaks"]}
EnableSymlinks={config["enable_symlinks"]}
EnablePseudoConsoleSupport={config["enable_pseudo_console"]}
EnableFSMonitor={config["enable_fs_monitor"]}
"""

    config_path = temp_dir / "git.info"
    config_path.write_text(config_content, encoding="utf-8")

    console.print(f"[dim]Created config file: {config_path}[/dim]")
    return config_path


def install_git_from_installer(installer_path: Path, config_path: Path) -> int:
    """Install Git using the installer and configuration"""
    console.print("🔧 Installing Git...", style="yellow")

    try:
        with console.status("[yellow]Installing Git... This may take a few minutes"):
            result = subprocess.run(
                [
                    str(installer_path),
                    "/VERYSILENT",
                    "/NORESTART",
                    "/NOCANCEL",
                    f"/LOADINF={config_path}",
                    "/RESTARTEXITCODE=3",
                ],
                check=False,
                timeout=300,
            )  # 5 minute timeout

        return result.returncode

    except subprocess.TimeoutExpired:
        console.print("❌ Installation timed out after 5 minutes", style="red")
        return 1
    except Exception as e:
        console.print(f"❌ Installation failed: {e}", style="red")
        return 1


def get_git_install_location() -> Path | None:
    """Get the Git installation directory"""
    try:
        # Try to find git.exe location
        result = subprocess.run(
            ["where", "git"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            git_path = Path(result.stdout.strip().split("\n")[0])
            # Navigate up from git.exe to installation root
            # Typical path: C:\Users\User\AppData\Local\Programs\Git\cmd\git.exe
            install_dir = git_path.parent.parent
            return install_dir
    except Exception:
        pass

    # Fallback: check common installation directories
    username = os.environ.get("USERNAME", os.environ.get("USER", "User"))
    common_paths = [
        Path(f"C:\\Users\\{username}\\AppData\\Local\\Programs\\Git"),
        Path("C:\\Program Files\\Git"),
        Path("C:\\Program Files (x86)\\Git"),
    ]

    for path in common_paths:
        if path.exists() and (path / "bin" / "git.exe").exists():
            return path

    return None


def get_current_git_version() -> str | None:
    """Get the currently installed Git version"""
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version_line = result.stdout.strip()
            version_match = version_line.split()
            if len(version_match) >= 3:
                return version_match[2].split(".windows")[0]
    except Exception:
        pass
    return None


def find_git_uninstaller() -> Path | None:
    """Find the Git uninstaller executable"""
    install_dir = get_git_install_location()
    if not install_dir:
        return None

    # Look for uninstaller in installation directory
    uninstaller_patterns = ["unins*.exe", "Uninstall*.exe"]

    for pattern in uninstaller_patterns:
        for uninstaller in install_dir.glob(pattern):
            return uninstaller

    return None


@app.command()
def download(
    version: str = typer.Option(
        GIT_VERSION, "--version", help="Git version to download"
    ),
    output_dir: str = typer.Option(
        Path("."), "--output", help="Output directory", exists=True, file_okay=False
    ),
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
    """Download Git installer for Windows with SSL/TLS support"""
    console.print(Panel.fit("📥 Git Downloader", style="bold cyan"))
    output_path = Path(output_dir)
    if not output_path.exists():
        console.print(f"❌ Output directory does not exist: {output_path}", style="red")
        raise typer.Exit(1)
    installer_filename = get_installer_filename(version)
    installer_path = output_path / installer_filename
    if installer_path.exists() and not force:
        console.print(f"⚠️  File already exists: {installer_path}", style="yellow")
        if not Confirm.ask("Do you want to overwrite it?"):
            console.print("Download cancelled.", style="blue")
            return
    git_url = get_git_download_url(version)
    ssl_context = create_ssl_context(
        verify=not no_verify_ssl,
        use_system_certs=use_system_certs,
        ca_cert_file=ca_cert_file,
        ca_cert_dir=ca_cert_dir,
    )
    console.print(f"📥 Downloading Git {version}...", style="cyan")
    console.print(f"[dim]URL: {git_url}[/dim]")
    console.print(f"[dim]Output: {installer_path}[/dim]")
    if download_file_with_progress(
        git_url,
        installer_path,
        ssl_context=ssl_context,
        timeout=timeout,
        max_retries=max_retries,
    ):
        console.print(
            f"✅ Successfully downloaded Git {version} installer", style="green"
        )
        console.print(f"📂 Saved to: [green]{installer_path}[/green]")
        console.print(
            f"💡 Run '{SCRIPT_NAME} install --installer-path \"{installer_path}\"' to install",
            style="blue",
        )
    else:
        raise typer.Exit(1)


@app.command()
def install(
    version: str = typer.Option(
        GIT_VERSION, "--version", help="Git version to install"
    ),
    editor: str = typer.Option("VisualStudioCode", "--editor", help="Default editor"),
    install_dir: str | None = typer.Option(
        None, "--install-dir", help="Custom installation directory"
    ),
    installer_path: str | None = typer.Option(
        None, "--installer-path", help="Path to existing installer file"
    ),
    force: bool = typer.Option(
        False, "--force", help="Force reinstall even if Git exists"
    ),
    default_branch: str = typer.Option(
        "main", "--default-branch", help="Default branch name"
    ),
    include_desktop_icon: bool = typer.Option(
        True, "--desktop-icon/--no-desktop-icon", help="Include desktop icon"
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
    """Install Git for Windows with SSL/TLS support"""
    console.print(Panel.fit("🚀 Git Installer for Windows", style="bold blue"))
    config = get_default_config(version, editor)
    config["default_branch"] = default_branch
    if install_dir:
        config["install_dir"] = install_dir
    if not include_desktop_icon:
        config["components"] = config["components"].replace("icons\\desktop,", "")
    if check_git_installed() and not force:
        console.print("⚠️  Git is already installed!", style="yellow")
        if not Confirm.ask("Do you want to continue anyway?"):
            console.print("Installation cancelled.", style="blue")
            return
    with tempfile.TemporaryDirectory(prefix="git_installer_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        console.print(f"[dim]Using temporary directory: {temp_dir}[/dim]")
        if installer_path:
            installer_file = Path(installer_path)
            if not installer_file.exists():
                console.print(
                    f"❌ Installer file not found: {installer_file}", style="red"
                )
                raise typer.Exit(1)
            console.print(
                f"📦 Using existing installer: [green]{installer_file}[/green]"
            )
        else:
            git_url = get_git_download_url(version)
            installer_file = temp_dir / get_installer_filename(version)
            ssl_context = create_ssl_context(
                verify=not no_verify_ssl,
                use_system_certs=use_system_certs,
                ca_cert_file=ca_cert_file,
                ca_cert_dir=ca_cert_dir,
            )
            console.print(f"📥 Downloading Git {version}...", style="cyan")
            if not download_file_with_progress(
                git_url,
                installer_file,
                ssl_context=ssl_context,
                timeout=timeout,
                max_retries=max_retries,
            ):
                raise typer.Exit(1)
        content = f"""
    Installer: [green]{installer_file}[/green]
    Version: [green]{config["version"]}[/green]
    Editor: [green]{config["editor"]}[/green]
    Install Dir: [green]{config["install_dir"]}[/green]
    Default Branch: [green]{config["default_branch"]}[/green]
    Desktop Icon: [green]{"Yes" if include_desktop_icon else "No"}[/green]
    """
        config_panel = Panel(
            content,
            title="📋 Installation Configuration",
            title_align="left",
        )
        console.print(config_panel)
        console.print("⚙️  Creating installation configuration...", style="cyan")
        config_path = create_git_config(temp_dir, config)
        exit_code = install_git_from_installer(installer_file, config_path)
        if exit_code == 3:
            console.print(
                Panel(
                    "🔄 Installation complete! You must restart your machine to finish setup.",
                    style="yellow",
                )
            )
        elif exit_code == 0:
            console.print("✅ Git installation completed successfully!", style="green")
            if check_git_installed():
                result = subprocess.run(
                    ["git", "--version"], capture_output=True, text=True
                )
                console.print(f"🎉 Verified: [green]{result.stdout.strip()}[/green]")
            else:
                console.print(
                    "⚠️  Installation completed but Git verification failed",
                    style="yellow",
                )
        else:
            console.print(
                f"❌ Installation failed with exit code: {exit_code}", style="red"
            )
            raise typer.Exit(1)


@app.command()
def check():
    """Check if Git is installed and show version info"""
    console.print("🔍 Checking Git installation...", style="cyan")

    if check_git_installed():
        try:
            result = subprocess.run(
                ["git", "--version"], capture_output=True, text=True, timeout=5
            )
            console.print(
                f"✅ Git is installed: [green]{result.stdout.strip()}[/green]"
            )

            # Try to get install path
            try:
                where_result = subprocess.run(
                    ["where", "git"], capture_output=True, text=True, timeout=5
                )
                if where_result.returncode == 0:
                    git_path = where_result.stdout.strip().split("\n")[0]
                    console.print(f"📂 Location: [dim]{git_path}[/dim]")
            except Exception as _:
                pass

        except subprocess.TimeoutExpired:
            console.print(
                "✅ Git is installed but version check timed out", style="yellow"
            )
    else:
        console.print("❌ Git is not installed", style="red")
        console.print(f"💡 Run '{SCRIPT_NAME} install' to install Git", style="blue")


@app.command()
def uninstall(
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompts"),
    keep_config: bool = typer.Option(
        False, "--keep-config", help="Keep Git configuration files"
    ),
):
    """Uninstall Git for Windows"""

    console.print(Panel.fit("🗑️ Git Uninstaller", style="bold red"))

    # Check if Git is installed
    if not check_git_installed():
        console.print("❌ Git is not installed", style="red")
        return

    # Get current version for confirmation
    current_version = get_current_git_version()
    if current_version:
        console.print(f"📦 Found Git version: [yellow]{current_version}[/yellow]")

    # Find uninstaller
    uninstaller = find_git_uninstaller()
    if not uninstaller:
        console.print("❌ Could not find Git uninstaller", style="red")
        console.print("💡 Try manual uninstall through Windows Settings", style="blue")
        raise typer.Exit(1)

    console.print(f"🔍 Found uninstaller: [dim]{uninstaller}[/dim]")

    # Confirmation
    if not force:
        if not Confirm.ask("Are you sure you want to uninstall Git?"):
            console.print("Uninstall cancelled.", style="blue")
            return

    # Run uninstaller
    console.print("🗑️ Uninstalling Git...", style="yellow")

    try:
        uninstall_args = [str(uninstaller), "/SILENT"]

        # Add /NORESTART to prevent automatic restart
        uninstall_args.append("/NORESTART")

        with console.status("[yellow]Uninstalling Git..."):
            result = subprocess.run(
                uninstall_args,
                check=False,
                timeout=120,  # 2 minute timeout
            )

        if result.returncode == 0:
            console.print("✅ Git uninstalled successfully!", style="green")

            # Verify uninstallation
            if not check_git_installed():
                console.print("🎉 Verified: Git has been removed", style="green")
            else:
                console.print(
                    "⚠️ Git command still available - restart may be required",
                    style="yellow",
                )

        else:
            console.print(
                f"❌ Uninstall failed with exit code: {result.returncode}", style="red"
            )
            raise typer.Exit(1)

    except subprocess.TimeoutExpired:
        console.print("❌ Uninstall timed out", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"❌ Uninstall failed: {e}", style="red")
        raise typer.Exit(1)


@app.command()
def config():
    """Show default configuration that will be used"""
    default_config = get_default_config()

    console.print(
        Panel.fit("📋 Default Git Installation Configuration", style="cyan bold")
    )

    config_text = ""
    for key, value in default_config.items():
        formatted_key = key.replace("_", " ").title()
        config_text += f"[bold]{formatted_key:<25}:[/bold] {value}\n"

    console.print(Panel(config_text.rstrip(), border_style="dim"))

    console.print(
        "\n💡 Use command line options to customize these settings", style="blue"
    )


if __name__ == "__main__":
    app()
