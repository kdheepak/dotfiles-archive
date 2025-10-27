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
SSL connection tester - Test HTTPS connections with various certificate configurations
"""

import os
import ssl
import socket
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import typer
import httpx
import certifi
import truststore
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Configure console
console = Console()

app = typer.Typer(help=__doc__, add_completion=False, pretty_exceptions_enable=False)


def create_ssl_context(
    verify: bool = True,
    use_system_certs: bool = False,
    ca_cert_file: str | None = None,
    ca_cert_dir: str | None = None,
) -> ssl.SSLContext | bool:
    """
    Create an SSL context with the specified verification settings.

    Args:
        verify: Whether to verify SSL certificates
        use_system_certs: Use system certificate store instead of certifi
        ca_cert_file: Path to custom CA certificate file
        ca_cert_dir: Path to directory containing CA certificates

    Returns:
        SSL context or False for no verification
    """
    if not verify:
        console.print(
            "⚠️  SSL verification disabled - connection may be insecure!",
            style="yellow bold",
        )
        return False

    # Check environment variables if no custom certs specified
    if ca_cert_file is None:
        ca_cert_file = os.environ.get("SSL_CERT_FILE")

    # Check user config directory for CA certificate if not specified
    if ca_cert_file is None:
        user_ca_cert = Path.home() / ".config" / "certs" / "cacert.pem"
        if user_ca_cert.exists():
            ca_cert_file = str(user_ca_cert)
            console.print(
                f"🔒 Found user CA certificate: {ca_cert_file}", style="cyan dim"
            )

    if ca_cert_dir is None:
        ca_cert_dir = os.environ.get("SSL_CERT_DIR")

    # Validate certificate paths exist
    if ca_cert_file and not Path(ca_cert_file).exists():
        console.print(
            f"❌ CA certificate file not found: {ca_cert_file}", style="red bold"
        )
        console.print("\n💡 Troubleshooting tips:", style="yellow")
        console.print(
            "   • Check the file path is correct and the file exists", style="dim"
        )
        console.print(
            "   • Ensure you have read permissions for the certificate file",
            style="dim",
        )
        console.print(
            "   • For corporate environments, ask your IT team for the CA certificate",
            style="dim",
        )
        console.print("   • Save your corporate CA certificate to:", style="dim")
        console.print(
            f"     {Path.home() / '.config' / 'certs' / 'cacert.pem'}",
            style="dim green",
        )
        console.print("   • Common locations:", style="dim")
        console.print(
            "     - Windows: '%USERPROFILE%/.config/certs/company-ca.pem'", style="dim"
        )
        console.print("     - MacOS: '$HOME/.config/certs/company-ca.pem'", style="dim")
        console.print("     - Linux: '/etc/ssl/certs/company-ca.pem'", style="dim")
        console.print(
            "   • Try using `--use-system-certs` instead if certificates are in system store",
            style="dim",
        )
        raise typer.Exit(1)

    if ca_cert_dir and not Path(ca_cert_dir).exists():
        console.print(
            f"❌ CA certificate directory not found: {ca_cert_dir}", style="red bold"
        )
        console.print("\n💡 Troubleshooting tips:", style="yellow")
        console.print("   • Check the directory path is correct", style="dim")
        console.print(
            "   • Ensure the directory contains properly named certificate files",
            style="dim",
        )
        console.print(
            "   • Certificate files must be named with OpenSSL hash format (e.g., abc123.0)",
            style="dim",
        )
        console.print("   • Common system certificate directories:", style="dim")
        console.print("     - Windows: '%USERPROFILE%/.config/certs'", style="dim")
        console.print("     - MacOS: '$HOME/.config/certs'", style="dim")
        console.print("     - Linux: '/etc/ssl/certs'", style="dim")
        console.print(
            "   • Consider using `--ca-cert-file` with a single bundle file instead",
            style="dim",
        )
        console.print(
            "   • Try using `--use-system-certs` for automatic system certificate detection",
            style="dim",
        )
        raise typer.Exit(1)

    if use_system_certs:
        console.print("🔒 Using system certificate store", style="cyan")
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        # Create default context with certifi or custom certificates
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


def get_ssl_info(
    hostname: str, port: int = 443, ssl_context: ssl.SSLContext | None = None
) -> dict | None:
    """Get SSL certificate information from a host"""
    try:
        # Create a socket and wrap it with SSL
        with socket.create_connection((hostname, port), timeout=10) as sock:
            if ssl_context is None:
                ssl_context = ssl.create_default_context()

            with ssl_context.wrap_socket(sock, server_hostname=hostname) as ssock:
                # Get certificate info
                cert = ssock.getpeercert()
                cipher = ssock.cipher()
                version = ssock.version()

                return {
                    "cert": cert,
                    "cipher": cipher,
                    "version": version,
                }
    except Exception as e:
        console.print(f"❌ Failed to get SSL info: {e}", style="red")
        return None


def format_certificate_info(cert_info: dict) -> Table:
    """Format certificate information as a rich table"""
    table = Table(title="📜 Certificate Information", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    cert = cert_info["cert"]

    # Subject
    subject = dict(x[0] for x in cert.get("subject", []))
    if subject:
        table.add_row("Subject CN", subject.get("commonName", "N/A"))
        if "organizationName" in subject:
            table.add_row("Organization", subject.get("organizationName"))

    # Issuer
    issuer = dict(x[0] for x in cert.get("issuer", []))
    if issuer:
        table.add_row("Issuer CN", issuer.get("commonName", "N/A"))
        if "organizationName" in issuer:
            table.add_row("Issuer Org", issuer.get("organizationName"))

    # Validity dates
    not_before = cert.get("notBefore", "N/A")
    not_after = cert.get("notAfter", "N/A")
    table.add_row("Valid From", not_before)
    table.add_row("Valid Until", not_after)

    # Check expiration
    if not_after != "N/A":
        try:
            expiry_date = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            days_left = (expiry_date - datetime.now()).days
            if days_left < 0:
                table.add_row("Status", "[red]EXPIRED[/red]")
            elif days_left < 30:
                table.add_row("Status", f"[yellow]Expires in {days_left} days[/yellow]")
            else:
                table.add_row("Status", f"[green]Valid ({days_left} days left)[/green]")
        except Exception as _:
            pass

    # SSL/TLS info
    table.add_row("Protocol", cert_info["version"])
    if cert_info["cipher"]:
        cipher_name, protocol, strength = cert_info["cipher"]
        table.add_row("Cipher Suite", f"{cipher_name} ({protocol})")
        table.add_row("Key Strength", f"{strength} bits")

    # SANs
    san_list = []
    for ext in cert.get("subjectAltName", []):
        if ext[0] == "DNS":
            san_list.append(ext[1])
    if san_list:
        table.add_row(
            "Alt Names", ", ".join(san_list[:3]) + ("..." if len(san_list) > 3 else "")
        )

    return table


def print_ssl_troubleshooting():
    """Print SSL troubleshooting tips"""
    console.print("\n💡 Troubleshooting tips:", style="yellow")
    console.print(
        "   1. Try --use-system-certs if in a corporate environment", style="dim"
    )
    console.print(
        "   2. Export your organization's CA certificate and use --ca-cert-file",
        style="dim",
    )
    console.print(
        "   3. Set SSL_CERT_FILE environment variable to your CA certificate",
        style="dim",
    )
    console.print("   4. Use --show-cert to see certificate details", style="dim")
    console.print(
        "   5. As a last resort, use --no-verify-ssl (NOT RECOMMENDED)", style="dim"
    )
    console.print("\n📚 Common issues:", style="yellow")
    console.print("   • Self-signed certificates", style="dim")
    console.print("   • Corporate proxy with SSL interception", style="dim")
    console.print("   • Expired certificates", style="dim")
    console.print("   • Missing intermediate certificates", style="dim")


@app.command()
def main(
    url: str = typer.Argument(
        "https://www.google.com", help="URL to test SSL connection"
    ),
    no_verify_ssl: bool = typer.Option(
        False, "--no-verify-ssl", help="Disable SSL verification"
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
        10.0, "--timeout", help="Connection timeout in seconds"
    ),
    no_show_cert: bool = typer.Option(
        False, "--no-show-cert", help="Show detailed certificate information"
    ),
    check_only: bool = typer.Option(
        False, "--check-only", help="Only check connection, don't perform HTTP request"
    ),
):
    """
    SSL Connection Tester - Test HTTPS connections with various certificate configurations

    Examples:
        ./test-ssl.py https://github.com
        ./test-ssl.py https://example.com --show-cert
        ./test-ssl.py https://internal.company.com --use-system-certs
        ./test-ssl.py https://self-signed.local --ca-cert-file /path/to/ca.pem
    """

    console.print(Panel.fit(f"🔒 Testing SSL Connection to {url}", style="cyan bold"))

    # Parse URL to get hostname and port
    parsed = urlparse(url)
    hostname = parsed.hostname or url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    show_cert = not no_show_cert

    # Create SSL context
    ssl_context = create_ssl_context(
        verify=not no_verify_ssl,
        use_system_certs=use_system_certs,
        ca_cert_file=ca_cert_file,
        ca_cert_dir=ca_cert_dir,
    )

    # First, try to get certificate info
    if show_cert and isinstance(ssl_context, ssl.SSLContext):
        console.print(
            f"\n🔍 Retrieving certificate information from {hostname}:{port}...\n",
            style="cyan",
        )
        cert_info = get_ssl_info(hostname, port, ssl_context)
        if cert_info:
            console.print(format_certificate_info(cert_info))

    if check_only:
        console.print(
            f"\n🔍 Checking SSL connection to {hostname}:{port}...", style="cyan"
        )
        try:
            with socket.create_connection((hostname, port), timeout=timeout) as sock:
                if isinstance(ssl_context, ssl.SSLContext):
                    with ssl_context.wrap_socket(
                        sock, server_hostname=hostname
                    ) as ssock:
                        console.print("✅ SSL connection successful!", style="green")
                        console.print(f"🔒 Protocol: {ssock.version()}")
                else:
                    console.print(
                        "✅ Connection successful (no SSL verification)", style="green"
                    )
        except ssl.SSLError as e:
            console.print(f"❌ SSL Error: {e}", style="red")
            print_ssl_troubleshooting()
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"❌ Connection error: {e}", style="red")
            raise typer.Exit(1)
    else:
        # Perform full HTTP request
        try:
            console.print(f"\n🔍 Performing HTTPS request to {url}...\n", style="cyan")

            client = httpx.Client(verify=ssl_context, timeout=timeout)
            response = client.get(url)
            response.raise_for_status()

            console.print("✅ SSL connection successful!", style="green")
            console.print(f"📄 Status code: {response.status_code}")
            console.print(f"🔒 HTTP version: {response.http_version}")

            # Show response headers
            table = Table(title="📋 Response Headers", show_header=True)
            table.add_column("Header", style="cyan")
            table.add_column("Value", style="green")

            for header, value in list(response.headers.items())[:10]:
                table.add_row(header, value[:80] + ("..." if len(value) > 80 else ""))

            console.print(table)

        except httpx.ConnectError as e:
            if "certificate" in str(e).lower():
                console.print(f"❌ SSL Certificate error: {e}", style="red")
                print_ssl_troubleshooting()
            else:
                console.print(f"❌ Connection error: {e}", style="red")
            raise typer.Exit(1)
        except httpx.HTTPError as e:
            console.print(f"❌ HTTP error: {e}", style="red")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"❌ Error: {e}", style="red")
            raise typer.Exit(1)
        finally:
            if "client" in locals():
                client.close()  # noqa: F821


if __name__ == "__main__":
    app()
