#!/usr/bin/env python3
"""Collect public AI signals and extract readable text/links from public HTML pages."""

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 8_000_000
FEEDS = {
    "habr-ml": "https://habr.com/ru/rss/hubs/machine_learning/articles/?fl=ru",
    "habr-ai": "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru",
    "ainews": "https://news.smol.ai/rss.xml",
}
SOURCES = ("github", "github-trending", *FEEDS, "hf-models", "hf-spaces", "hn", "yc")
DEADLINE = None  # time.monotonic() value after which fetch() refuses new requests


def output_root():
    """Directory that --output may write into: the Ouroboros skill state dir, else the working directory."""
    return Path(os.environ.get("OUROBOROS_SKILL_STATE_DIR") or Path.cwd()).resolve()


def write_new(path, text):
    """Create a new file inside output_root(); relative paths resolve against it, existing files are preserved."""
    root = output_root()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"--output must stay inside {root}")
    with target.open("x", encoding="utf-8") as stream:
        stream.write(text)


def records(values, build, errors):
    """Build one item per record; a malformed record is reported in errors instead of failing the whole source."""
    items = []
    for value in values:
        try:
            item = build(value)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            errors.append(f"skipped record: {error}")
        else:
            if item is not None:
                items.append(item)
    return items


def canonical(url):
    if not isinstance(url, str):
        raise ValueError("URL must be a string")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected a public HTTP(S) URL without credentials")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def public_url(url):
    canonical(url)
    parts = urlsplit(url)
    if parts.port not in (None, 80, 443):
        raise ValueError("Only standard public web ports are supported")
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private, local and reserved network addresses are unavailable")


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    timeout = 15
    if DEADLINE is not None:
        timeout = min(timeout, DEADLINE - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("collection time budget exhausted")
    public_url(url)
    request = Request(url, headers={"User-Agent": "ai-b2b-radar/1.0", "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.8"})
    with build_opener(PublicRedirect()).open(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeds 8 MB")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            return body.decode(charset, errors="replace"), response.geturl()
        except LookupError as error:
            raise ValueError(f"Unsupported response charset: {charset}") from error


def date(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class Page(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.text, self.links, self.title = base, [], [], []
        self.hidden, self.in_title, self.anchor = 0, False, None
        self.in_heading, self.heading_links = False, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "template"):
            self.hidden += 1
        if self.hidden:
            return
        if tag == "title":
            self.in_title = True
        if tag == "h2":
            self.in_heading = True
        if tag == "a":
            href = dict(attrs).get("href", "")
            try:
                self.anchor = {"url": canonical(urljoin(self.base, href)), "text": ""} if href else None
            except ValueError:
                self.anchor = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template"):
            self.hidden = max(0, self.hidden - 1)
        if self.hidden:
            return
        if tag == "title":
            self.in_title = False
        if tag == "h2":
            self.in_heading = False
        if tag == "a" and self.anchor is not None:
            self.links.append(self.anchor)
            if self.in_heading:
                self.heading_links.append(self.anchor)
            self.anchor = None

    def handle_data(self, data):
        if self.hidden:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.text.append(cleaned)
            if self.in_title:
                self.title.append(cleaned)
            if self.anchor is not None:
                self.anchor["text"] += (" " if self.anchor["text"] else "") + cleaned


def parse_page(html, url):
    page = Page(url)
    page.feed(html)
    links = {item["url"]: item for item in page.links}
    text = "\n".join(page.text)
    return {"url": canonical(url), "title": " ".join(page.title), "text": text[:40000],
            "truncated": len(text) > 40000, "links": list(links.values())[:300]}


def signal(title, url, kind, published=None, modified=None, excerpt=""):
    if not isinstance(title, str) or not isinstance(excerpt, str):
        raise ValueError("Signal title and excerpt must be strings")
    return {"title": " ".join(title.split()), "url": canonical(url), "kind": kind,
            "published_at": date(published), "modified_at": date(modified), "excerpt": excerpt[:2000]}


def parse_feed(body):
    root = ET.fromstring(body)
    if root.tag not in ("rss", "{http://www.w3.org/2005/Atom}feed"):
        raise ValueError("Expected an RSS or Atom feed")
    atom = {"a": "http://www.w3.org/2005/Atom"}

    def rss_item(entry):
        url = entry.findtext("link")
        if url:
            description = parse_page(unescape(entry.findtext("description", "")), url)["text"]
            return signal(entry.findtext("title", ""), url, "article", entry.findtext("pubDate"), excerpt=description)

    def atom_entry(entry):
        link = next((x.get("href") for x in entry.findall("a:link", atom)
                     if x.get("rel", "alternate") == "alternate" and x.get("href")), None)
        if link:
            return signal(entry.findtext("a:title", "", atom), link, "article",
                          entry.findtext("a:published", None, atom), entry.findtext("a:updated", None, atom))

    errors = []
    items = records(root.findall("./channel/item"), rss_item, errors) + records(root.findall("a:entry", atom), atom_entry, errors)
    return items, errors


def gather(source, since, limit, queries=None):
    def read(url):
        return fetch(url)[0]

    if source in FEEDS:
        return parse_feed(read(FEEDS[source]))
    if source == "github":
        queries = queries or [f"topic:{topic} pushed:>={since[:10]} archived:false"
                              for topic in ("ai-agents", "workflow-automation", "document-processing", "machine-learning")]
        items, errors = [], []
        for query in queries:
            per_query = max(1, (limit + len(queries) - 1) // len(queries))
            url = "https://api.github.com/search/repositories?" + urlencode({"q": query, "sort": "updated", "per_page": per_query})
            try:
                data = json.loads(read(url))
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise ValueError("GitHub response must contain an items array")
                items.extend(records(data["items"], lambda x: signal(x["full_name"], x["html_url"], "repository", x.get("created_at"),
                                                                      x.get("pushed_at"), x.get("description") or ""), errors))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"{query}: {error}")
        return items, errors
    if source.startswith("hf-"):
        kind = source.removeprefix("hf-")
        data = json.loads(read(f"https://huggingface.co/api/{kind}?sort=lastModified&direction=-1&limit={limit}"))
        if not isinstance(data, list):
            raise ValueError("Hugging Face response must be an array of objects")
        prefix = "spaces/" if kind == "spaces" else ""
        errors = []
        return records(data, lambda x: signal(x["id"], "https://huggingface.co/" + prefix + x["id"], kind,
                                              x.get("createdAt"), x.get("lastModified")), errors), errors
    if source == "hn":
        ids = json.loads(read("https://hacker-news.firebaseio.com/v0/showstories.json"))
        if not isinstance(ids, list):
            raise ValueError("HN response must be an array of item ids")
        items, errors = [], []
        for item_id in ids[:limit]:
            if type(item_id) is not int or item_id < 0:
                errors.append(f"skipped invalid HN item id {item_id!r}")
                continue
            try:
                item = json.loads(read(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"))
                if item is not None and not isinstance(item, dict):
                    raise ValueError("HN item must be an object or null")
                if item and item.get("type") == "story" and not item.get("deleted") and not item.get("dead"):
                    discussion = f"https://news.ycombinator.com/item?id={item_id}"
                    row = signal(item.get("title", ""), item.get("url") or discussion, "launch", item.get("time"))
                    row["discussion_url"] = discussion
                    items.append(row)
            except (OSError, ValueError) as error:
                errors.append(f"item {item_id}: {error}")
        return items, errors
    url = "https://github.com/trending?since=weekly" if source == "github-trending" else "https://www.ycombinator.com/launches"
    body, final_url = fetch(url)
    return parse_discovery(body, final_url, source)


def parse_discovery(body, url, source):
    if source == "yc" and body.lstrip().startswith("{"):
        data = json.loads(body)
        if not isinstance(data.get("hits"), list):
            raise ValueError("YC response must contain a hits array")

        def launch(hit):
            if not isinstance(hit, dict) or not isinstance(hit.get("slug"), str):
                raise ValueError("YC launch must contain a slug")
            launch_url = "https://www.ycombinator.com/launches/" + quote(hit["slug"], safe="")
            row = signal(hit.get("title", hit["slug"]), launch_url, "launch", hit.get("created_at"), excerpt=hit.get("tagline") or "")
            company = hit.get("company")
            if isinstance(company, dict) and company.get("url"):
                row["official_url"] = canonical(company["url"])
            return row

        errors = []
        return records(data["hits"], launch, errors), errors
    page = Page(url)
    page.feed(body)

    def discovery(link):
        parts = urlsplit(link["url"])
        if source == "github-trending":
            # ponytail: public Trending markup; use search/browser if GitHub changes this layout.
            accepted = parts.hostname == "github.com" and re.fullmatch(r"/[^/]+/[^/]+", parts.path)
        else:
            accepted = parts.hostname == "www.ycombinator.com" and re.match(r"/(launches|companies)/[^/]+", parts.path)
        if accepted:
            return signal(link["text"] or parts.path, link["url"], "discovery")

    errors = []
    items = records(page.heading_links if source == "github-trending" else page.links, discovery, errors)
    items = list({item["url"]: item for item in items}.values())
    return items, errors if items else errors + ["No product links extracted; inspect the page with a browser"]


def normalize(items, since, until, limit):
    unique = {}
    for row in items:
        dates = [x for x in (row["published_at"], row["modified_at"]) if x]
        newest = max(dates, default=None)
        row = dict(row, period_status="unknown" if newest is None else
                   "future" if newest > until else "in_window" if newest >= since else "older")
        existing = unique.get(row["url"])
        if existing is None or (newest or "") > max(existing["published_at"] or "", existing["modified_at"] or ""):
            unique[row["url"]] = row
    ordered = sorted(unique.values(), key=lambda x: max(x["published_at"] or "", x["modified_at"] or ""), reverse=True)
    eligible = [x for x in ordered if x["period_status"] in ("in_window", "unknown")]
    latest = max((max(x["published_at"] or "", x["modified_at"] or "") for x in ordered), default="") or None
    return {"latest_seen": latest, "scanned": len(items), "unique": len(unique),
            "older": sum(x["period_status"] == "older" for x in ordered),
            "future": sum(x["period_status"] == "future" for x in ordered),
            "items": eligible[:limit], "has_more": len(eligible) > limit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, action="append", help="Repeatable; default: all sources")
    parser.add_argument("--since", help="ISO date/time, default: seven days ago")
    parser.add_argument("--limit", type=int, default=30, help="Per-source sample, 1–100; default: 30")
    parser.add_argument("--query", action="append", help="Repeatable GitHub repository search; replaces default topic searches")
    parser.add_argument("--url", help="Extract a single public page instead of collecting signals")
    parser.add_argument("--html", type=Path, help="Parse saved HTML with --url as its base, without fetching")
    parser.add_argument("--output", type=Path, help="New JSON file inside the working directory (or OUROBOROS_SKILL_STATE_DIR); existing files are preserved")
    parser.add_argument("--budget", type=int, default=240,
                        help="Seconds for all network requests, 10–3600; default 240. On exhaustion the collected part is saved")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    if not 10 <= args.budget <= 3600:
        parser.error("--budget must be between 10 and 3600 seconds")
    global DEADLINE
    DEADLINE = time.monotonic() + args.budget
    if args.query and (args.url or args.html or (args.source and "github" not in args.source)):
        parser.error("--query requires GitHub collection")
    now = datetime.now(timezone.utc)
    since = date(args.since) if args.since else date((now - timedelta(days=7)).date().isoformat())
    if since is None or since > now.isoformat():
        parser.error("--since must be a valid date no later than now")
    if args.html and not args.url:
        parser.error("--html requires --url as the document base")
    if args.url and args.source:
        parser.error("Use either --url or --source")
    try:
        if args.url:
            if args.html:
                if args.html.stat().st_size > MAX_BYTES:
                    raise ValueError("HTML file exceeds 8 MB")
                body, url = args.html.read_text(encoding="utf-8"), args.url
            else:
                body, url = fetch(args.url)
            result = dict(parse_page(body, url), checked_at=now.isoformat())
        else:
            result = {"checked_at": now.isoformat(), "since": since, "sources": []}
            for source in dict.fromkeys(args.source or SOURCES):
                try:
                    items, errors = gather(source, since, args.limit, args.query if source == "github" else None)
                    batch = normalize(items, since, datetime.now(timezone.utc).isoformat(), args.limit)
                    if batch["future"]:
                        errors.append(f"{batch['future']} records have future timestamps; verify dates")
                    stale = bool(batch["latest_seen"] and batch["latest_seen"] < since)
                    result["sources"].append(dict(source=source, status="partial" if errors else "stale" if stale else "ok", errors=errors, **batch))
                except (OSError, ValueError, ET.ParseError, KeyError, TypeError) as error:
                    result["sources"].append({"source": source, "status": "unavailable", "error": str(error), "items": []})
            result["coverage"] = "bounded sample; publication/modification dates require event verification"
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            write_new(args.output, encoded)
        else:
            print(encoded, end="")
        if "sources" in result and all(x["status"] == "unavailable" for x in result["sources"]):
            return 1
        return 0
    except (OSError, ValueError) as error:
        print(f"collect: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
