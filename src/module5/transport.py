"""Shared HTTPS plumbing for the outbound LLM call.

It goes over stdlib urllib so a Fabric Spark pool needs no extra install, and
it needs one thing from TLS: verified certificates, with a CA bundle that
actually resolves on whatever machine is running.
"""

from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext:
    """Verified TLS, with a working trust store on every platform.

    Certificates are always verified -- the fallback is about *finding* the CA
    bundle, not skipping the check. A python.org macOS install ships with an
    empty OpenSSL cert directory until `Install Certificates.command` is run,
    which fails the handshake against Azure endpoints; certifi's bundle covers
    it. Fabric and Linux images have a populated store and never reach the
    fallback.
    """
    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0):
        return context
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return context
