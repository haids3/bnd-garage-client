"""Connection setup for talking to the hub over its non-standard TLS.

The hub presents a self-signed certificate and only speaks legacy TLS with
weak ciphers - none of that is something this client chose; it's what's
needed to get a handshake to complete at all against this specific hardware.
"""

from __future__ import annotations

import asyncio
import socket
import ssl

from cryptography import x509

from .errors import HubUnreachableError


def hub_ssl_context() -> ssl.SSLContext:
    """Build the permissive SSLContext required to reach the hub at all."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1
    except AttributeError:
        pass
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    return context


def _read_hub_id(host: str, port: int, timeout: float) -> str:
    """Blocking: open a raw TLS socket and read the hub's ID from its cert CN."""
    context = hub_ssl_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw_socket,
            context.wrap_socket(raw_socket) as tls_socket,
        ):
            cert_der = tls_socket.getpeercert(binary_form=True)
    except OSError as err:
        raise HubUnreachableError(f"could not connect to {host}:{port}: {err}") from err

    if not cert_der:
        raise HubUnreachableError(f"hub at {host}:{port} presented no certificate")

    certificate = x509.load_der_x509_certificate(cert_der)
    common_names = certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    if not common_names:
        raise HubUnreachableError("hub certificate has no Common Name (hub ID)")
    return str(common_names[0].value)


async def read_hub_id(host: str, port: int = 8989, timeout: float = 10) -> str:
    """Connect to the hub and extract its hub ID from the certificate's Common Name.

    Done in a worker thread: with certificate verification disabled, asyncio's
    high-level TLS support has no way to hand back the peer certificate, so
    this falls back to the stdlib's blocking socket/ssl APIs.
    """
    return await asyncio.to_thread(_read_hub_id, host, port, timeout)
