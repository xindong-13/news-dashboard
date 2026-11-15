"""有禮貌的 HTTP client：自訂 User-Agent、每個網域限速、遵守 robots.txt。"""

from __future__ import annotations

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from pipeline.config import HttpSettings


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    def __init__(self, settings: HttpSettings):
        self.settings = settings
        self._client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- 內部 --------------------------------------------------------------
    def _delay_for(self, host: str) -> float:
        for domain, delay in self.settings.domain_delays.items():
            if host == domain or host.endswith("." + domain):
                return delay
        return self.settings.per_domain_delay_seconds

    def _throttle(self, host: str) -> None:
        wait = self._last_hit.get(host, 0) + self._delay_for(host) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        key = f"{scheme}://{host}"
        if key not in self._robots:
            parser: RobotFileParser | None = None
            try:
                self._throttle(host)
                r = self._client.get(f"{key}/robots.txt")
                if r.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(r.text.splitlines())
                # 4xx：視為沒有限制；5xx／網路錯誤：也放行，交給實際請求決定
            except httpx.HTTPError:
                parser = None
            self._robots[key] = parser
        return self._robots[key]

    def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = self._robots_for(parts.scheme, parts.netloc)
        if parser is None:
            return True
        return parser.can_fetch(self.settings.user_agent, url)

    # -- 公開 --------------------------------------------------------------
    def get(self, url: str, *, ignore_robots: bool = False, **kwargs) -> httpx.Response:
        check = self.settings.respect_robots_txt and not ignore_robots
        if check and not self.robots_allowed(url):
            raise RobotsDisallowed(f"robots.txt 不允許抓取：{url}")
        self._throttle(urlsplit(url).netloc)
        r = self._client.get(url, **kwargs)
        r.raise_for_status()
        return r
