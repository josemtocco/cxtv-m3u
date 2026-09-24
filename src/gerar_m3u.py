#!/usr/bin/env python3

import asyncio
import hashlib
import html
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.cxtv.com.br/tv/paises/tvs-brasil"
OUTPUT_FILE = Path("listas/cxtv-brasil.m3u")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)

PAGE_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8, sock_read=12)

MAX_PAGE_CONCURRENCY = 12
MAX_STREAM_CONCURRENCY = 40
MAX_PAGES = 250
MAX_CHANNELS = 2000

HLS_RE = re.compile(r'https?://[^"\'<>\s\\]+?\.m3u8(?:\?[^"\'<>\s\\]*)?', re.I)
MP4_RE = re.compile(r'https?://[^"\'<>\s\\]+?\.mp4(?:\?[^"\'<>\s\\]*)?', re.I)
TS_RE = re.compile(r'https?://[^"\'<>\s\\]+?\.ts(?:\?[^"\'<>\s\\]*)?', re.I)

IGNORED_SCHEMES = ("javascript:", "mailto:", "tel:", "#")
BLOCKED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".css", ".js", ".ico", ".woff", ".woff2", ".ttf",
)


def clean(value):
    if not value:
        return ""
    value = html.unescape(str(value))
    value = value.replace("\\/", "/")
    return re.sub(r"\s+", " ", value).strip()


def absolute(base, value):
    value = clean(value)
    if not value or value.startswith(IGNORED_SCHEMES):
        return ""
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base, value)


def canonical_url(url):
    p = urlparse(url)
    return p._replace(fragment="").geturl()


def probable_stream(url):
    if not url:
        return False
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    low = url.lower()
    if any(low.split("?")[0].endswith(x) for x in BLOCKED_EXTENSIONS):
        return False
    return (
        ".m3u8" in low
        or ".mp4" in low
        or ".ts" in low
        or "manifest" in low
        or "playlist" in low
        or "m3u8" in low
    )


def channel_link(url):
    return "/tv-ao-vivo/" in urlparse(url).path


def same_domain(url):
    return urlparse(url).netloc.lower().endswith("cxtv.com.br")


def channel_name_from_link(tag):
    for attr in ("data-name", "title", "aria-label"):
        value = clean(tag.get(attr))
        if value:
            return value
    return clean(tag.get_text(" ", strip=True))


def extract_channel_links(page_url, page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    result = OrderedDict()

    for tag in soup.find_all("a", href=True):
        href = absolute(page_url, tag["href"])
        if not channel_link(href) or not same_domain(href):
            continue
        name = channel_name_from_link(tag)
        if not name:
            continue
        result[canonical_url(href)] = name

    return result


def extract_navigation_links(page_url, page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    links = []

    # Paginação tradicional.
    for tag in soup.find_all("a", href=True):
        href = absolute(page_url, tag["href"])
        text = clean(tag.get_text(" ", strip=True)).lower()
        aria = clean(tag.get("aria-label", "")).lower()
        title = clean(tag.get("title", "")).lower()

        if not same_domain(href):
            continue

        marker = f"{text} {aria} {title}"
        if any(x in marker for x in (
            "próxima", "proxima", "next", "mais", "carregar", "load more"
        )):
            links.append(href)

        # URLs de paginação comuns.
        if re.search(r"([?&](page|pagina|p)=\d+|/page/\d+)", href, re.I):
            links.append(href)

    # URLs presentes em scripts/JSON que possam apontar para páginas
    # da própria listagem.
    for match in re.findall(
        r'https?://[^"\']*cxtv\.com\.br[^"\']+',
        page_html,
        re.I,
    ):
        href = html.unescape(match).replace("\\/", "/")
        if same_domain(href) and not channel_link(href):
            if any(x in href.lower() for x in ("paises/tvs-brasil", "page=", "pagina=")):
                links.append(href)

    return list(dict.fromkeys(links))


def extract_streams(page_url, page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    found = []

    for tag in soup.find_all(["source", "video", "iframe", "embed"]):
        for attr in ("src", "data-src", "data-url", "data-stream", "data-hls"):
            value = tag.get(attr)
            if value:
                url = absolute(page_url, value)
                if probable_stream(url):
                    found.append(url)

    for tag in soup.find_all(True):
        for attr in ("src", "data-src", "data-url", "data-stream", "data-hls"):
            value = tag.get(attr)
            if value:
                url = absolute(page_url, value)
                if probable_stream(url):
                    found.append(url)

    for regex in (HLS_RE, MP4_RE, TS_RE):
        for match in regex.findall(page_html):
            found.append(canonical_url(html.unescape(match)))

    # Strings JSON/JS.
    for match in re.findall(r'["\'](https?://[^"\']+)["\']', page_html, re.I):
        url = canonical_url(html.unescape(match))
        if probable_stream(url):
            found.append(url)

    return list(dict.fromkeys(x for x in found if x))


def extract_metadata(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    categories = []
    language = ""

    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "")
        text = clean(tag.get_text(" ", strip=True))
        low = href.lower()

        if "/tv/categorias/" in low and text:
            if text not in categories:
                categories.append(text)

        if "/tv/idiomas/" in low and text and not language:
            language = text

    # Fallbacks para páginas que exibem metadados em texto.
    text = clean(soup.get_text(" ", strip=True))

    if not language:
        m = re.search(r"(?:Idioma|Language)\s*[:\-]\s*([A-Za-zÀ-ÿ ]+)", text, re.I)
        if m:
            language = clean(m.group(1))

    return categories, language


def acceptable_portuguese(language, page_html):
    if not language:
        # Se a página não expõe idioma, não descartamos o canal:
        # a fonte já é a listagem "TVs Brasil".
        return True

    low = clean(language).lower()
    return "portugu" in low


async def fetch(session, url):
    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                return None
            return await response.text(errors="ignore")
    except Exception:
        return None


async def discover_all_channels(session):
    queue = [SOURCE_URL]
    seen_pages = set()
    channels = OrderedDict()

    while queue and len(seen_pages) < MAX_PAGES and len(channels) < MAX_CHANNELS:
        batch = []
        while queue and len(batch) < MAX_PAGE_CONCURRENCY:
            url = canonical_url(queue.pop(0))
            if url not in seen_pages:
                seen_pages.add(url)
                batch.append(url)

        if not batch:
            continue

        print(f"Descobrindo páginas: {len(seen_pages)} | canais: {len(channels)}")

        results = await asyncio.gather(*(fetch(session, u) for u in batch))

        for page_url, page_html in zip(batch, results):
            if not page_html:
                continue

            for url, name in extract_channel_links(page_url, page_html).items():
                channels.setdefault(url, name)

            for nav in extract_navigation_links(page_url, page_html):
                if nav not in seen_pages and nav not in queue:
                    queue.append(nav)

    return channels


async def test_stream(session, url, semaphore):
    async with semaphore:
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Referer": "https://www.cxtv.com.br/",
            }

            async with session.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=STREAM_TIMEOUT,
            ) as response:

                if response.status not in (200, 206):
                    return False

                content_type = response.headers.get("Content-Type", "").lower()
                path = urlparse(url).path.lower()
                is_hls = (
                    ".m3u8" in path
                    or "mpegurl" in content_type
                    or "vnd.apple.mpegurl" in content_type
                )

                data = await response.content.read(512 * 1024)

                if not data:
                    return False

                if is_hls:
                    text = data.decode("utf-8", errors="ignore")
                    return (
                        "#EXTM3U" in text
                        and (
                            "#EXTINF" in text
                            or "#EXT-X-STREAM-INF" in text
                            or ".m3u8" in text
                            or ".ts" in text
                        )
                    )

                return True

        except Exception:
            return False


def safe_m3u(value):
    return clean(value).replace('"', "'").replace("\r", " ").replace("\n", " ")


def tvg_id(name):
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


async def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    connector = aiohttp.TCPConnector(limit=80, ssl=False)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=PAGE_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as session:

        print("=" * 70)
        print("CXTV BRASIL - GERADOR M3U")
        print("=" * 70)

        channels = await discover_all_channels(session)

        print(f"\nTotal de canais descobertos: {len(channels)}")

        semaphore = asyncio.Semaphore(MAX_STREAM_CONCURRENCY)
        final_entries = []

        for index, (channel_url, name) in enumerate(channels.items(), 1):
            print(f"[{index}/{len(channels)}] {name}")

            page_html = await fetch(session, channel_url)
            if not page_html:
                print("  -> página indisponível")
                continue

            categories, language = extract_metadata(page_html)

            if not acceptable_portuguese(language, page_html):
                print(f"  -> idioma ignorado: {language}")
                continue

            streams = extract_streams(channel_url, page_html)

            if not streams:
                print("  -> nenhum stream detectado")
                continue

            tests = await asyncio.gather(
                *(test_stream(session, s, semaphore) for s in streams)
            )

            valid = list(dict.fromkeys(
                s for s, ok in zip(streams, tests) if ok
            ))

            if not valid:
                print("  -> nenhum stream funcionando")
                continue

            if not categories:
                categories = ["Sem categoria"]

            # Uma entrada por categoria, como solicitado.
            for stream in valid:
                for category in categories:
                    final_entries.append({
                        "name": name,
                        "stream": stream,
                        "category": category,
                    })

            print(f"  -> {len(valid)} stream(s) OK | categorias: {', '.join(categories)}")

        # Remove duplicados por canal + categoria + stream.
        unique = OrderedDict()
        for item in final_entries:
            key = (
                item["name"].casefold(),
                item["category"].casefold(),
                canonical_url(item["stream"]),
            )
            unique.setdefault(key, item)

        entries = list(unique.values())
        entries.sort(key=lambda x: (x["category"].casefold(), x["name"].casefold()))

        lines = ["#EXTM3U"]

        for item in entries:
            name = safe_m3u(item["name"])
            category = safe_m3u(item["category"])
            stream = item["stream"]

            lines.append(
                f'#EXTINF:-1 tvg-id="{tvg_id(name)}" '
                f'tvg-name="{name}" '
                f'tvg-country="BR" '
                f'tvg-language="Português" '
                f'group-title="{category}",{name}'
            )
            lines.append(stream)

        OUTPUT_FILE.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

        print("\n" + "=" * 70)
        print(f"Entradas M3U válidas: {len(entries)}")
        print(f"Arquivo: {OUTPUT_FILE}")
        print("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
