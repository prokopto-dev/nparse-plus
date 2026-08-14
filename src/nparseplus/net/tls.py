"""The SSL context nParse+'s HTTP clients verify against (Qt-free).

httpx verifies against **certifi** by default: a fixed list of trust anchors
and nothing else. That is correct until a server sends an incomplete chain,
which is what wiki.project1999.com does (#116) — its leaf is issued by
SSL.com while the intermediates it serves are Sectigo's, so OpenSSL cannot
build a path to any root and every request fails with
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``.
Browsers and curl do not notice, because they fetch the missing intermediate
from the URL in the leaf's Authority Information Access extension. OpenSSL
never has.

``truststore`` hands verification to the platform instead — Security.framework
on macOS, Schannel on Windows — and both of those do the AIA fetch, so the
chain completes. It is what pip uses for the same problem.

**Linux is not fixed by this** and the code says so out loud: truststore
falls back to OpenSSL there, which still will not chain-build. The actual
repair is the wiki serving its intermediate; this is what makes the feature
work for the platforms that can cope in the meantime.

Deliberately NOT applied to every client in ``net/`` at once. The updater
verifies release downloads against a digest carried over the same TLS
session, so how that session is trusted is a security-relevant path and
changes to it belong in their own review — not folded into a fix for one
misconfigured wiki. Callers opt in.
"""

from __future__ import annotations

import logging
import ssl

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import-time, exercised by the frozen app
    import truststore
except Exception:  # pragma: no cover - dependency missing (source checkout)
    truststore = None  # type: ignore[assignment]


def os_trust_context() -> ssl.SSLContext | None:
    """An SSL context backed by the OS trust store, or None to use certifi.

    Never raises: a platform where truststore cannot start is a platform
    where the caller should quietly keep httpx's default, not one where the
    app fails to build its clients.
    """
    if truststore is None:
        logger.debug("truststore unavailable; verifying against certifi")
        return None
    try:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        logger.warning("could not use the OS trust store; falling back to certifi", exc_info=True)
        return None


def verify_option() -> ssl.SSLContext | bool:
    """What to pass as httpx's ``verify=``.

    ``True`` is httpx's own default (certifi) — the fallback, not a
    weakening: verification stays on either way.
    """
    context = os_trust_context()
    return context if context is not None else True
