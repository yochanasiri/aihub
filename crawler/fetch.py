import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import requests
from .policy import CrawlConfig, normalize_url, require_public_dns


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    headers: dict


class Fetcher:
    def __init__(self, config: CrawlConfig):
        self.config = config
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": config.user_agent})
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def close(self):
        self.session.close()

    def _request(self, url: str, headers: dict) -> FetchResult:
        require_public_dns(url)
        host = urlsplit(url).netloc
        robot = self.robots.get(host)
        delay = max(self.config.request_delay_seconds,
                    (robot.crawl_delay(self.config.user_agent) or 0) if robot else 0)
        time.sleep(max(0, delay - (time.monotonic() - self.last_request.get(host, 0))))
        self.last_request[host] = time.monotonic()
        with self.session.get(url, headers=headers, timeout=(5, self.config.timeout_seconds),
                              allow_redirects=False, stream=True) as response:
            data = bytearray()
            deadline = time.monotonic() + self.config.timeout_seconds
            if response.status_code == 200:
                for chunk in response.iter_content(65536):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Download time budget exceeded")
                    data.extend(chunk)
                    if len(data) > self.config.max_bytes:
                        raise ValueError("Response exceeds download limit")
            return FetchResult(url, response.status_code, bytes(data), requests.structures.CaseInsensitiveDict(response.headers))

    def _allowed_by_robots(self, url: str):
        host = urlsplit(url).netloc
        if host not in self.robots:
            robot_url = f"https://{host}/robots.txt"
            # Robots redirects are independently domain-checked. Fail closed on transport failures.
            for _ in range(6):
                result = self._request(robot_url, {})
                if result.status in (301, 302, 303, 307, 308):
                    robot_url = normalize_url(result.headers.get("Location", ""), self.config.allowed_domains, robot_url)
                    continue
                break
            else:
                raise ValueError("Too many robots redirects")
            robot = RobotFileParser()
            if result.status == 200:
                robot.parse(result.body.decode("utf-8", errors="replace").splitlines())
            elif result.status in (404, 410):
                robot.parse(["User-agent: *", "Disallow:"])
            else:
                raise ValueError("Robots policy unavailable; crawl deferred")
            self.robots[host] = robot
        if not self.robots[host].can_fetch(self.config.user_agent, url):
            raise ValueError("Blocked by robots.txt")

    def fetch(self, url: str, headers: dict | None = None) -> FetchResult:
        url = normalize_url(url, self.config.allowed_domains)
        for _ in range(6):
            self._allowed_by_robots(url)
            result = self._request(url, headers or {})
            if result.status not in (301, 302, 303, 307, 308):
                return result
            location = result.headers.get("Location")
            if not location:
                raise ValueError("Redirect missing Location")
            url = normalize_url(urljoin(url, location), self.config.allowed_domains)
            headers = {}  # Never forward validators to a different resource.
        raise ValueError("Too many redirects")
