import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from pydantic import BaseModel, Field, model_validator


class Seed(BaseModel):
    url: str
    department: str = "Institute"


class CrawlConfig(BaseModel):
    allowed_domains: list[str]
    seeds: list[Seed]
    user_agent: str = "IITI-AIHUB/0.1"
    max_pages: int = Field(100, ge=1, le=10000)
    max_depth: int = Field(3, ge=0, le=10)
    request_delay_seconds: float = Field(1.5, ge=0.1)
    timeout_seconds: int = Field(20, ge=1, le=120)
    max_bytes: int = Field(15 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_pdf_pages: int = Field(300, ge=1, le=2000)
    min_text_characters: int = Field(120, ge=1)
    stale_after_days: int = Field(30, ge=1)

    @model_validator(mode="after")
    def validate_domains(self):
        if not self.allowed_domains or not self.seeds:
            raise ValueError("At least one allowed domain and seed are required")
        for domain in self.allowed_domains:
            if (domain != domain.lower() or not re.fullmatch(r"[a-z0-9.-]+", domain)
                    or not (domain == "iiti.ac.in" or domain.endswith(".iiti.ac.in"))
                    or ".." in domain):
                raise ValueError("Only explicit IIT Indore domains are permitted")
        for seed in self.seeds:
            normalize_url(seed.url, self.allowed_domains)
        return self


def normalize_url(url: str, allowed_domains: list[str], base: str | None = None) -> str:
    if re.search(r"[\x00-\x20\x7f\\]", url):
        raise ValueError("Unsafe URL characters")
    p = urlsplit(urljoin(base, url) if base else url)
    if p.scheme not in ("https", "http") or p.username or p.password:
        raise ValueError("Only unauthenticated HTTP(S) URLs are allowed")
    if p.hostname not in allowed_domains:
        raise ValueError("Domain is not explicitly whitelisted")
    if p.port not in (None, 443 if p.scheme == "https" else 80):
        raise ValueError("Nonstandard ports are forbidden")
    # Upgrade institute links; never fetch unencrypted institutional content.
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", p.hostname, p.path or "/", urlencode(query), ""))


def require_public_dns(url: str):
    host = urlsplit(url).hostname
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError("Non-public destination blocked")
