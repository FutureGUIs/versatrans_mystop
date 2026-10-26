"""Complete Lake Orion's R1 chain to an already trusted GoDaddy G2 root."""
import hashlib
from pathlib import Path
import ssl
from urllib.parse import urlsplit

CROSS_SHA256 = "7bcb0f2f2d1031a6af8d61baa835d2835a3b8bcc26d94a3b048b1655fb81298c"


def complete_chain(context):
    """Add a cross-signed intermediate, never an independent trust anchor."""
    pem = (Path(__file__).parent / "certificates" / "gd_tls_root-r1-cross-g2.crt.pem").read_text()
    der = ssl.PEM_cert_to_DER_cert(pem)
    if hashlib.sha256(der).hexdigest() != CROSS_SHA256:
        raise ValueError("Bundled GoDaddy cross-certificate checksum mismatch")
    context.load_verify_locations(cadata=pem)
    # Python 3.13+ defaults may allow an intermediate as a trust anchor. Require
    # a complete chain to a self-signed root already in the default trust store.
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def context_for_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "mystop.lok12.org" or parsed.port not in (None, 443):
        return None
    context = ssl.create_default_context()
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    return complete_chain(context)


async def create_client(hass, config):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from .api import DistrictApiClient
    context = await hass.async_add_executor_job(context_for_url, config["url"])
    return DistrictApiClient(async_get_clientsession(hass), config, ssl_context=context)
