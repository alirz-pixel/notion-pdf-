import html
import re
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (compatible; NotionPDFExport/1.0)"


def _parse_meta_tags(html_text):
    metas = {}
    for tag in re.findall(r"<meta\b[^>]*>", html_text, re.I):
        attrs = dict(re.findall(r'([a-zA-Z][a-zA-Z0-9:_-]*)\s*=\s*"([^"]*)"', tag))
        attrs.update(dict(re.findall(r"([a-zA-Z][a-zA-Z0-9:_-]*)\s*=\s*'([^']*)'", tag)))
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            metas[key.lower()] = html.unescape(attrs["content"])
    return metas


def _find_title(html_text):
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    return html.unescape(m.group(1)).strip() if m else None


def _find_favicon(html_text, base_url):
    m = re.search(r'<link[^>]+rel=["\'](?:shortcut icon|icon)["\'][^>]*>', html_text, re.I)
    if m:
        href_m = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I)
        if href_m:
            return urllib.parse.urljoin(base_url, href_m.group(1))
    parsed = urllib.parse.urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


_DOMAIN_FAVICON_CACHE = {}
_SKIP_FAVICON_HOSTS = ("notion.so", "notion.site")


def fetch_favicon_only(url, timeout=2.5):
    """Cheap, per-domain-cached favicon lookup for plain inline text links.

    Notion's own UI decorates ordinary hyperlinks with a small favicon next
    to the link text — but that's a live client-side enhancement, not
    something the public API exposes on the rich-text object, so we can't
    tell "this link had a favicon in Notion" from "this link didn't" by
    reading the data. We just recreate the effect for every external link:
    a HEAD request for /favicon.ico only (no full-page fetch, unlike
    fetch_link_metadata — this runs once per unique link in the whole
    document, so it needs to stay fast) and cache the result per domain,
    since favicons are practically always domain-wide."""
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme.startswith("http") or not parsed.netloc:
            return None
        if any(parsed.netloc.endswith(h) for h in _SKIP_FAVICON_HOSTS):
            return None
        domain_key = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None

    if domain_key in _DOMAIN_FAVICON_CACHE:
        return _DOMAIN_FAVICON_CACHE[domain_key]

    favicon_url = f"{domain_key}/favicon.ico"
    result = None
    try:
        req = urllib.request.Request(favicon_url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                result = favicon_url
    except Exception:
        result = None

    if result is None:
        # Plenty of sites (docs platforms especially) don't serve a bare
        # /favicon.ico — their icon is only declared via <link rel="icon">
        # in the page <head>. Fall back to a capped GET of the real page
        # and parse that tag out, same as the full bookmark-card crawl but
        # reading far less of the response since we only need the icon.
        try:
            req2 = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                content_type = resp2.headers.get("Content-Type", "")
                if "html" in content_type.lower():
                    raw = resp2.read(150_000)
                    charset = resp2.headers.get_content_charset() or "utf-8"
                    try:
                        text = raw.decode(charset, errors="replace")
                    except LookupError:
                        text = raw.decode("utf-8", errors="replace")
                    final_url = resp2.geturl()
                    m = re.search(r'<link[^>]+rel=["\'](?:shortcut icon|icon)["\'][^>]*>', text, re.I)
                    if m:
                        href_m = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I)
                        if href_m:
                            result = urllib.parse.urljoin(final_url, href_m.group(1))
        except Exception:
            result = None

    _DOMAIN_FAVICON_CACHE[domain_key] = result
    return result


def fetch_link_metadata(url, timeout=4, max_bytes=300_000):
    """Best-effort scrape of a URL's title/description/favicon/og:image, the
    same metadata Notion itself crawls when you paste a link as a bookmark.
    The public Notion API doesn't expose this (bookmark blocks only carry
    the raw url), so we fetch it ourselves at export time. Returns None on
    any failure — callers should fall back to a plain link."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None
            raw = resp.read(max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
            final_url = resp.geturl()
    except Exception:
        return None

    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    metas = _parse_meta_tags(text)
    title = metas.get("og:title") or _find_title(text) or url
    description = metas.get("og:description") or metas.get("description")
    image = metas.get("og:image")
    if image:
        image = urllib.parse.urljoin(final_url, image)

    return {
        "title": title.strip() if title else url,
        "description": description.strip() if description else None,
        "image": image,
        "favicon": _find_favicon(text, final_url),
        "url": url,
    }
