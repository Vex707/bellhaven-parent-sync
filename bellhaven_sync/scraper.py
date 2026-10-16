"""Scrape every Bellhaven community from the public website.

The directory is paginated, and it does not list every community: new ones can
be announced on the home page before they reach the directory. So we collect
community links from the directory pages AND the home/about pages, then read
each detail page as the source of truth.
"""
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

COMMUNITY_PATH = re.compile(r"^/communities/[\w-]+/?$")


@dataclass
class Location:
    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: list[str]
    administrator: str = ""
    phone: str = ""
    found_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _get(session: requests.Session, url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _community_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        if COMMUNITY_PATH.match(urlparse(url).path) and url not in links:
            links.append(url.rstrip("/"))
    return links


def _parse_detail(soup: BeautifulSoup, url: str) -> Location:
    fields = {}
    for dt in soup.select("dl.detail dt"):
        dd = dt.find_next_sibling("dd")
        fields[dt.get_text(strip=True).lower()] = dd
    addr_lines = [s.strip() for s in fields["address"].stripped_strings]
    m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", addr_lines[-1])
    if not m:
        raise ValueError(f"Unparseable address on {url}: {addr_lines}")
    city, state, zip_code = m.groups()
    offerings = [b.get_text(strip=True) for b in fields["care offerings"].select(".badge")] if "care offerings" in fields else []
    return Location(
        slug=urlparse(url).path.rstrip("/").split("/")[-1],
        url=url,
        name=soup.find("h1").get_text(strip=True),
        street=" ".join(addr_lines[:-1]),
        city=city.strip(),
        state=state,
        zip=zip_code,
        care_offerings=offerings,
        administrator=fields["administrator"].get_text(strip=True) if "administrator" in fields else "",
        phone=fields["phone"].get_text(strip=True) if "phone" in fields else "",
    )


def scrape(base_url: str = config.BASE_URL) -> list[Location]:
    session = requests.Session()
    sources: dict[str, list[str]] = {}

    def note(links, where):
        for link in links:
            sources.setdefault(link, []).append(where)

    # Walk the directory by following "Next" links until there are none.
    page_url, seen_pages = f"{base_url}/communities", set()
    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        soup = _get(session, page_url)
        note(_community_links(soup, page_url), "directory")
        nxt = soup.find("a", string=re.compile(r"Next"))
        page_url = urljoin(page_url, nxt["href"]) if nxt else None

    for extra in ("/", "/about"):
        soup = _get(session, f"{base_url}{extra}")
        note(_community_links(soup, f"{base_url}{extra}"), f"page {extra}")

    locations = []
    for url, found_on in sources.items():
        loc = _parse_detail(_get(session, url), url)
        loc.found_on = sorted(set(found_on))
        locations.append(loc)
    return sorted(locations, key=lambda l: l.name)
