import ssl
from mystop_test.tls import complete_chain, context_for_url

def test_cross_certificate_keeps_full_verification():
    c=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    complete_chain(c)
    assert c.verify_mode==ssl.CERT_REQUIRED
    assert c.check_hostname
    assert not c.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    # Only the cross-signed certificate was added, no new self-signed root.
    certs=c.get_ca_certs()
    assert len(certs)==1
    assert certs[0]['subject']!=certs[0]['issuer']

def test_tls_fix_scoped_to_lake_orion():
    assert context_for_url('https://example.org/') is None
    assert context_for_url('http://mystop.lok12.org/') is None
    assert context_for_url('https://mystop.lok12.org:444/') is None
    c=context_for_url('https://mystop.lok12.org/MyStopWebAPI/')
    assert c.verify_mode==ssl.CERT_REQUIRED and c.check_hostname
    assert c.verify_flags & ssl.VERIFY_X509_STRICT
