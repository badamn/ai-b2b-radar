#!/usr/bin/env python3
"""Offline behavioral checks: python3 -B scripts/check.py."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import collect
from score import CRITERIA, score


def main():
    record = {"id": "invoice", "technology": "Example OCR", "product": "Invoice processing",
              "buyer": "CFO", "confidence": "medium", "confidence_reason": "Illustrative test data",
              "criteria": {name: {"value": value, "reason": "Illustrative assumption",
                                  "kind": "hypothesis", "evidence": []}
                           for name, value in zip(CRITERIA, (15, 20, 10, 15, 10))}}
    original = copy.deepcopy(record)
    assert score([record])["ranked"][0]["score"] == 70
    assert record == original
    partial = copy.deepcopy(record)
    partial["criteria"]["value"].update(value=None, kind="unknown")
    result = score([partial])["needs_data"][0]
    assert result["score"] is None and result["score_range"] == [60, 80]
    assert result["confidence"] == "low"
    for criterion in partial["criteria"].values():
        criterion.update(value=None, kind="unknown")
    result = score([partial])["needs_data"][0]
    assert result["score_range"] == [0, 100] and len(result["missing"]) == 5
    for bad in (True, -5, 25, 2, 10.0, "15", float("nan")):
        invalid = copy.deepcopy(record)
        invalid["criteria"]["pain"]["value"] = bad
        try:
            score([invalid])
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid score {bad!r}")
    invalid = copy.deepcopy(record)
    invalid["criteria"]["pain"]["kind"] = "fact"
    try:
        score([invalid])
    except ValueError:
        pass
    else:
        raise AssertionError("Accepted a fact without an evidence reference")
    for invalid_input in ({}, [record, record], [None]):
        try:
            score(invalid_input)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted invalid input shape or duplicate id")
    better = copy.deepcopy(record)
    better.update(id="better", confidence="high")
    assert [x["id"] for x in score([record, better])["ranked"]] == ["better", "invoice"]

    html = '<title>Product</title><script>secret</script><style>hidden</style><p>Useful text</p><a href="/buy?id=7&utm_source=test">Price</a>'
    page = collect.parse_page(html, "https://example.com/start")
    assert page["title"] == "Product" and "Useful text" in page["text"]
    assert "secret" not in page["text"] and "hidden" not in page["text"]
    assert page["links"] == [{"url": "https://example.com/buy?id=7", "text": "Price"}]
    trending = '<a href="/enterprise/premium-support">24/7 support</a><h2><a href="/owner/product">owner / product</a></h2>'
    rows, errors = collect.parse_discovery(trending, "https://github.com/trending", "github-trending")
    assert not errors and len(rows) == 1 and rows[0]["url"] == "https://github.com/owner/product"
    yc = json.dumps({"hits": [{"slug": "demo-product", "title": "Example launch", "created_at": "2026-09-21T12:00:00Z", "company": {"url": "https://example.com"}}]})
    rows, errors = collect.parse_discovery(yc, "https://www.ycombinator.com/launches", "yc")
    assert rows[0]["official_url"] == "https://example.com/" and rows[0]["published_at"] == "2026-09-21T12:00:00+00:00"
    rss = '<rss><channel><item><title>Old</title><link>https://example.com/p?id=1</link><pubDate>Tue, 01 Sep 2026 12:00:00 GMT</pubDate></item><item><title>New</title><link>https://example.com/p?id=2</link><pubDate>Mon, 21 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>'
    items = collect.parse_feed(rss)
    items.append(dict(items[1], url=collect.canonical(items[1]["url"] + "&utm_source=other")))
    window = ("2026-09-15T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 10)
    normalized = collect.normalize(items, *window)
    assert normalized["scanned"] == 3 and normalized["unique"] == 2
    assert normalized["older"] == 1 and len(normalized["items"]) == 1
    assert normalized["latest_seen"] == "2026-09-21T12:00:00+00:00"
    future = collect.signal("Future", "https://example.com/future", "article", "2027-01-01")
    assert collect.normalize([future], *window)["future"] == 1
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom</title><link href="https://example.com/atom"/><updated>2026-09-21T12:00:00Z</updated></entry></feed>'
    assert collect.parse_feed(atom)[0]["modified_at"] == "2026-09-21T12:00:00+00:00"
    assert collect.date("not a date") is None
    assert collect.date({"invalid": "date"}) is None
    assert collect.date("2026-09-21T14:00:00+02:00") == "2026-09-21T12:00:00+00:00"
    original_resolver = collect.socket.getaddrinfo
    collect.socket.getaddrinfo = lambda *args: [(None, None, None, None, ("127.0.0.1", 443))]
    try:
        try:
            collect.public_url("https://example.com")
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted private address")
    finally:
        collect.socket.getaddrinfo = original_resolver

    original_fetch = collect.fetch
    def github_fetch(url):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(url).query).get("q", [""])[0]
        name = "invoice-tool" if "document-processing" in query else "agent-tool"
        return json.dumps({"items": [{"full_name": "example/" + name,
                                      "html_url": "https://github.com/example/" + name,
                                      "pushed_at": "2026-09-21T12:00:00Z"}]}), url
    collect.fetch = github_fetch
    try:
        rows, errors = collect.gather("github", window[0], 30)
        assert {row["title"] for row in rows} == {"example/invoice-tool", "example/agent-tool"}, "Discovery must cover business workflows as well as generic AI"
    finally:
        collect.fetch = original_fetch
    def fake_fetch(url):
        if "showstories" in url:
            return "[1, 2, 3]", url
        if "/item/1." in url:
            return json.dumps({"type": "story", "title": "Launch", "time": 1790000000}), url
        if "/item/2." in url:
            return '{"deleted": true}', url
        raise OSError("temporary source error")
    collect.fetch = fake_fetch
    try:
        rows, errors = collect.gather("hn", window[0], 3)
        assert len(rows) == 1 and rows[0]["url"].endswith("?id=1") and len(errors) == 1
    finally:
        collect.fetch = original_fetch

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        input_path, output_path = root / "in.json", root / "out.json"
        input_path.write_text(json.dumps([record]), encoding="utf-8")
        command = [sys.executable, "-B", str(Path(__file__).with_name("score.py")), str(input_path), "--output", str(output_path)]
        subprocess.run(command, check=True, capture_output=True)
        assert json.loads(output_path.read_text())["ranked"][0]["score"] == 70
        previous = output_path.read_bytes()
        assert subprocess.run(command, capture_output=True).returncode == 2
        assert output_path.read_bytes() == previous
        saved = root / "page.html"
        saved.write_text(html, encoding="utf-8")
        run = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("collect.py")), "--html", str(saved), "--url", "https://example.com"], check=True, capture_output=True, text=True)
        assert json.loads(run.stdout)["links"][0]["url"].endswith("?id=7")
        # One entity per card; models rank separately even if they outscore products.
        product = dict(record, category="products", family="invoice", url="https://example.com/product",
                       event_date="2026-09-21", description='Invoice checks <img src=x onerror=alert(1)> __SUMMARY__',
                       features=["Extracts line items", "Flags discrepancies"],
                       product={"facts": [{"text": "Vendor offers invoice extraction", "url": "https://example.com/features"}], "opinion": "Sell invoice checks"},
                       buyer={"facts": [], "opinion": "Finance teams"},
                       value_mechanism={"facts": [], "opinion": "Fewer manual checks"}, applications=["Invoice processing"],
                       competition={"summary": "Manual checks and one paid service",
                                    "alternatives": [
                                        {"name": "Manual checks", "url": None, "type": "manual", "market": "ru", "price": None, "facts": [],
                                         "covers": "Current process", "edge": "Faster", "gap": "No setup"},
                                        {"name": "Paid OCR", "url": "https://example.com/paid", "type": "commercial", "market": "global",
                                         "price": {"text": "$10/month", "url": "https://example.com/pricing"},
                                         "facts": [{"text": "Vendor lists invoice OCR", "url": "https://example.com/paid-features"}],
                                         "covers": "Extraction", "edge": "Russian documents", "gap": "Mature"}]},
                       validation="Measure errors on a held-out sample")
        model = copy.deepcopy(product)
        model.update(id="classifier", technology="Example classifier", category="models", family="classifier",
                     url="https://example.com/model", applications=["Routing", "Classification"])
        model["criteria"]["repeatability"]["value"] = 20
        digest = {"schema_version": "ai-b2b-digest/v4",
                  "meta": {"period_start": "2026-09-15", "period_end": "2026-09-22",
                           "generated_at": "2026-09-22T12:00:00Z", "market": "Russia", "segment": "B2B",
                           "summary": "Two independently ranked categories"},
                  "entries": [model, product],
                  "coverage": [{"source": "Example", "status": "checked", "checked": 2, "selected": 2, "note": "Test fixture"}]}
        digest_path = root / "digest.json"
        digest_path.write_text(json.dumps(digest), encoding="utf-8")
        report_command = [sys.executable, "-B", str(Path(__file__).with_name("report.py")), str(digest_path)]
        run = subprocess.run(report_command, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert run.stdout.startswith("<!doctype html>")
        assert run.stdout.index("Example OCR") < run.stdout.index("Example classifier")
        assert run.stdout.count('class="tag">№ 1') == 2  # Separate category ranks.
        assert run.stdout.count('<h4>Факты из источников</h4>') == 6
        assert run.stdout.count('<h4>Мнение агента</h4>') == 6
        assert run.stdout.count('class="no-facts"') == 4
        assert '<h3>О продукте</h3>' in run.stdout and '<h3>Главные функции</h3>' in run.stdout
        assert '<h3>Что появилось</h3>' not in run.stdout
        assert 'href="https://example.com/features"' in run.stdout
        assert '<img ' not in run.stdout and '&lt;img ' in run.stdout
        assert run.stdout.count('<h3>Конкуренты</h3>') == 2 and run.stdout.count('<table class="competitors">') == 2
        assert '<summary>Конкурентный ландшафт</summary>' in run.stdout 
        assert run.stdout.count('С российским аналогом: 1') == 2 and 'в нескольких карточках: нет' in run.stdout
        assert 'href="https://example.com/pricing"' in run.stdout and '2 конкурента' in run.stdout
        assert '__SUMMARY__' in run.stdout  # Input text never becomes a template instruction.
        assert 'data-filter="models"' in run.stdout and 'dialog.showModal()' in run.stdout
        assert score([product])["ranked"][0]["score"] == 70
        assert subprocess.run(report_command, capture_output=True, text=True, check=True).stdout == run.stdout
        report_path = root / 'digest.html'
        write_command = report_command + ['--output', str(report_path)]
        subprocess.run(write_command, check=True, capture_output=True)
        assert report_path.read_text() == run.stdout
        assert subprocess.run(write_command, capture_output=True).returncode == 2
        assert report_path.read_text() == run.stdout
        for mutation in ("changes", "category", "family", "date", "count", "facts", "fact_url", "fact_extra", "opinion", "features", "legacy", "fact_type",
                         "timing_alts", "alt_null_url", "alt_type", "confidence_alts", "timing_fact"):
            invalid = copy.deepcopy(digest)
            if mutation == "changes":
                invalid["changes"] = "Previous digest"
            elif mutation == "category":
                invalid["entries"][0]["category"] = "other"
            elif mutation == "family":
                sibling = copy.deepcopy(model)
                sibling.update(id="quantization", url="https://example.com/quantization")
                invalid["entries"].append(sibling)
            elif mutation == "date":
                invalid["entries"][0]["event_date"] = "2026-09-01"
            elif mutation == "count":
                invalid["coverage"][0]["selected"] = 3
            elif mutation == "facts":
                invalid['entries'][0]['product']['facts'][0].pop('url')
            elif mutation == "fact_url":
                invalid['entries'][0]['product']['facts'][0]['url'] = 'javascript:alert(1)'
            elif mutation == "fact_extra":
                invalid['entries'][0]['product']['facts'][0]['assumption'] = True
            elif mutation == "opinion":
                invalid['entries'][0]['buyer']['opinion'] = ' '
            elif mutation == "features":
                invalid['entries'][0]['features'] = []
            elif mutation == "timing_alts":
                invalid['entries'][0]['criteria']['timing']['value'] = 20
                invalid['entries'][0]['competition']['alternatives'].pop()
            elif mutation == "alt_null_url":
                invalid['entries'][0]['competition']['alternatives'][1]['url'] = None
            elif mutation == "alt_type":
                invalid['entries'][0]['competition']['alternatives'][1]['type'] = 'saas'
            elif mutation == "confidence_alts":
                invalid['entries'][0]['competition']['alternatives'] = []
            elif mutation == "timing_fact":
                invalid['entries'][0]['criteria']['timing'].update(kind='fact', evidence=['https://example.com/paid'])
                invalid['entries'][0]['competition']['alternatives'][1].update(price=None, facts=[])
            elif mutation == "legacy":
                invalid['entries'][0]['new_capability'] = 'A release'
            else:
                invalid['entries'][0]['buyer']['facts'] = 'No facts'
            digest_path.write_text(json.dumps(invalid), encoding="utf-8")
            run = subprocess.run(report_command, capture_output=True, text=True)
            assert run.returncode == 2 and not run.stdout, (mutation, run.stderr)
        from report import render
        unknown = copy.deepcopy(digest)
        for criterion in unknown['entries'][0]['criteria'].values():
            criterion.update(value=None, kind='unknown')
        unknown_html = render(unknown)
        assert 'недостаточно данных' in unknown_html and 'На проверку' in unknown_html
        twin = copy.deepcopy(product)
        twin.update(id="twin", url="https://example.com/twin")
        assert 'Paid OCR (2)' in render(dict(digest, entries=[product, twin]))
        empty = dict(digest, entries=[])
        assert 'Показано 0 из 0' in render(empty)
        # Exercise the entire collection loop with one failed and one healthy source.
        original_argv = sys.argv
        def partial_fetch(url):
            if "habr" in url:
                return rss, url
            raise OSError("source unavailable")
        collect.fetch = partial_fetch
        sys.argv = ["collect.py", "--source", "ainews", "--source", "habr-ml", "--since", "2020-01-01", "--output", str(root / "signals.json")]
        try:
            assert collect.main() == 0
            collected = json.loads((root / "signals.json").read_text())
            assert collected["sources"][0]["status"] == "unavailable"
            assert len(collected["sources"][1]["items"]) == 2
        finally:
            collect.fetch, sys.argv = original_fetch, original_argv
    print("PASS: scoring, collection, typed HTML, competition rules, separate facts/opinions, citations, escaping, deterministic output, category ranks and CLI file preservation")


if __name__ == "__main__":
    main()
