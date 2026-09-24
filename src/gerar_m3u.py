import asyncio
import hashlib
import html
import re
import sys
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

SOURCE = "https://www.cxtv.com.br/tv/paises/tvs-brasil"
OUTPUT = Path("listas/cxtv-brasil.m3u")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
STREAM_RE = re.compile(r'https?://[^"\'<>\s\\]+?(?:\.m3u8|\.mp4|\.ts)(?:\?[^"\'<>\s\\]*)?', re.I)

def clean(x):
    return re.sub(r"\s+", " ", html.unescape(str(x or "")).replace("\\/", "/")).strip()

def absurl(base, x):
    x = clean(x)
    if x.startswith("//"):
        return "https:" + x
    return urljoin(base, x)

def canonical(x):
    p = urlparse(x)
    return p._replace(fragment="").geturl()

def probable(x):
    y = x.lower()
    return urlparse(x).scheme in ("http","https") and any(k in y for k in (".m3u8",".mp4",".ts","m3u8","manifest","playlist"))

def parse_channels(markup):
    soup = BeautifulSoup(markup, "html.parser")
    out = OrderedDict()
    for a in soup.find_all("a", href=True):
        u = absurl(SOURCE, a["href"])
        if "/tv-ao-vivo/" in urlparse(u).path and "cxtv.com.br" in urlparse(u).netloc:
            name = clean(a.get("title") or a.get("aria-label") or a.get_text(" ", strip=True))
            if name:
                out[canonical(u)] = name
    return out

def metadata(markup):
    soup = BeautifulSoup(markup, "html.parser")
    cats, lang = [], ""
    for a in soup.find_all("a", href=True):
        h, t = a["href"].lower(), clean(a.get_text(" ", strip=True))
        if "/tv/categorias/" in h and t and t not in cats:
            cats.append(t)
        if "/tv/idiomas/" in h and t and not lang:
            lang = t
    return cats, lang

async def discover(browser):
    page = await browser.new_page(user_agent=UA)
    await page.goto(SOURCE, wait_until="domcontentloaded", timeout=40000)
    await page.wait_for_timeout(2500)
    channels = OrderedDict()
    old = -1
    for n in range(200):
        channels.update(parse_channels(await page.content()))
        print(f"Descoberta {n+1}: {len(channels)} canais")
        if len(channels) >= 1500 or len(channels) == old:
            break
        old = len(channels)
        buttons = page.locator("button, a, input")
        clicked = False
        for i in range(await buttons.count()):
            try:
                e = buttons.nth(i)
                marker = clean((await e.inner_text()) + " " + (await e.get_attribute("aria-label") or "") + " " + (await e.get_attribute("value") or "")).lower()
                if ("carregar mais" in marker or "load more" in marker or marker.strip() == "mais") and await e.is_visible():
                    await e.scroll_into_view_if_needed()
                    await e.click(timeout=5000)
                    await page.wait_for_timeout(1800)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break
    await page.close()
    return channels

async def inspect(browser, url, name):
    page = await browser.new_page(user_agent=UA)
    found = set()
    async def response(r):
        try:
            if probable(r.url):
                found.add(canonical(r.url))
        except Exception:
            pass
    page.on("response", response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(5000)
        markup = await page.content()
        cats, lang = metadata(markup)
        dom = await page.locator("iframe[src],video[src],source[src],[data-src],[data-url],[data-stream],[data-hls]").evaluate_all(
            """els => els.flatMap(e => [e.src,e.currentSrc,e.dataset?.src,e.dataset?.url,e.dataset?.stream,e.dataset?.hls]).filter(Boolean)"""
        )
        for u in dom:
            if probable(u): found.add(canonical(u))
        for u in STREAM_RE.findall(markup):
            found.add(canonical(html.unescape(u)))
        if lang and "portugu" not in lang.lower():
            found.clear()
        return name, cats, list(found)
    except Exception as e:
        print("  erro:", e)
        return name, [], []
    finally:
        await page.close()

async def inspect_all(browser, channels):
    sem = asyncio.Semaphore(6)
    async def one(u,n):
        async with sem:
            return await inspect(browser,u,n)
    tasks = [asyncio.create_task(one(u,n)) for u,n in channels.items()]
    return [await t for t in asyncio.as_completed(tasks)]

async def test_stream(session, url, sem):
    async with sem:
        try:
            async with session.get(url, headers={"User-Agent":UA,"Referer":"https://www.cxtv.com.br/"}, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=18,connect=7,sock_read=10)) as r:
                if r.status not in (200,206): return False
                data = await r.content.read(512*1024)
                if not data: return False
                ct = r.headers.get("Content-Type","").lower()
                if ".m3u8" in url.lower() or "mpegurl" in ct:
                    s = data.decode("utf-8","ignore")
                    return "#EXTM3U" in s and any(x in s for x in ("#EXTINF","#EXT-X-STREAM-INF",".m3u8",".ts"))
                return True
        except Exception:
            return False

async def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        channels = await discover(browser)
        if not channels:
            OUTPUT.write_text("#EXTM3U\n", encoding="utf-8")
            await browser.close()
            raise SystemExit("Nenhum canal descoberto.")
        print("Canais descobertos:", len(channels))
        inspected = await inspect_all(browser, channels)
        await browser.close()

    candidates, allstreams = [], set()
    for name, cats, streams in inspected:
        for s in streams:
            allstreams.add(s)
            candidates.append((name, cats, s))

    print("Streams detectados:", len(allstreams))
    if not allstreams:
        OUTPUT.write_text("#EXTM3U\n", encoding="utf-8")
        raise SystemExit("Nenhum stream capturado. A CXTV pode ter alterado o player.")

    sem = asyncio.Semaphore(30)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=60,ssl=False)) as session:
        ok = await asyncio.gather(*(test_stream(session,s,sem) for s in allstreams))
    valid = {s for s,v in zip(allstreams,ok) if v}
    print("Streams funcionando:", len(valid))
    if not valid:
        OUTPUT.write_text("#EXTM3U\n", encoding="utf-8")
        raise SystemExit("Nenhum stream passou no teste.")

    entries = OrderedDict()
    for name,cats,s in candidates:
        if s not in valid: continue
        for cat in (cats or ["Sem categoria"]):
            entries.setdefault((name.casefold(),cat.casefold(),s),(name,cat,s))

    lines = ["#EXTM3U"]
    for name,cat,s in sorted(entries.values(), key=lambda x:(x[1].casefold(),x[0].casefold())):
        tid = hashlib.sha1(name.encode()).hexdigest()[:12]
        name,cat = clean(name).replace('"',"'"), clean(cat).replace('"',"'")
        lines += [f'#EXTINF:-1 tvg-id="{tid}" tvg-name="{name}" tvg-country="BR" tvg-language="Português" group-title="{cat}",{name}',s]
    OUTPUT.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("Entradas M3U:", len(entries))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
