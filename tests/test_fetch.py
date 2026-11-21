import pytest
from crawler.fetch import Fetcher, FetchResult
from crawler.policy import CrawlConfig

def fetcher():
    return Fetcher(CrawlConfig(allowed_domains=['iiti.ac.in'], seeds=[{'url':'https://iiti.ac.in/'}]))

def test_external_redirect(monkeypatch):
    f, visited = fetcher(), []
    monkeypatch.setattr(f, '_allowed_by_robots', lambda url: None)
    def request(url, headers):
        visited.append(url)
        return FetchResult(url, 302, b'', {'Location':'https://evil.com/'})
    monkeypatch.setattr(f, '_request', request)
    with pytest.raises(ValueError): f.fetch('https://iiti.ac.in/')
    assert visited == ['https://iiti.ac.in/']
    f.close()

def test_robots(monkeypatch):
    f = fetcher()
    monkeypatch.setattr(f, '_request', lambda url, headers: FetchResult(url,200,b'User-agent: *\nDisallow: /private',{}))
    with pytest.raises(ValueError, match='robots'): f._allowed_by_robots('https://iiti.ac.in/private')
    f.close()
