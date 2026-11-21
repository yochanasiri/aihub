import socket
import pytest
from crawler.policy import CrawlConfig, normalize_url, require_public_dns
DOMAINS = ['iiti.ac.in', 'www.iiti.ac.in', 'academic.iiti.ac.in']

@pytest.mark.parametrize('url', ['https://evil.com/', 'https://iiti.ac.in.evil.com/',
 'https://eviliiti.ac.in/', 'https://unapproved.iiti.ac.in/', 'https://iiti.ac.in@evil.com/',
 'https://user:pass@iiti.ac.in/', 'file:///etc/passwd', 'javascript:alert(1)',
 'https://iiti.ac.in:8080/', 'https://127.0.0.1/', 'https://[::1]/', 'https://iiti.ac.in./',
 'https://iiti.ac.in\\@evil.com/', 'https://iiti.ac.in/\npath'])
def test_reject_url(url):
    with pytest.raises(ValueError): normalize_url(url, DOMAINS)

def test_canonicalize():
    assert normalize_url('http://IITI.ac.in:80/a?x=1&utm_source=test#s', DOMAINS) == 'https://iiti.ac.in/a?x=1'
    assert normalize_url('../a.pdf', DOMAINS, 'https://academic.iiti.ac.in/a/b') == 'https://academic.iiti.ac.in/a.pdf'

@pytest.mark.parametrize('address', ['127.0.0.1','10.0.0.1','169.254.169.254','::1','192.168.1.2'])
def test_dns(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(0,0,0,'',(address,443))])
    with pytest.raises(ValueError): require_public_dns('https://iiti.ac.in/')

def test_config():
    with pytest.raises(ValueError): CrawlConfig(allowed_domains=['evil.com'], seeds=[{'url':'https://evil.com/'}])
