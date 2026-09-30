#!/usr/bin/env python3
"""Validate a typed digest and render a standalone HTML digest."""

import argparse
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from collect import canonical
from score import CRITERIA, score

CATEGORIES = {"products": "Продукты и бизнес-сервисы",
              "tools": "Инструменты и инфраструктура", "models": "Модели и методы ML"}
CONFIDENCE_LABELS = {"low": "низкая", "medium": "средняя", "high": "высокая"}
STATUS_LABELS = {"checked": "проверен", "partial": "частично", "unavailable": "недоступен",
                 "stale": "устарел", "not_checked": "не проверен"}
ENTRY_FIELDS = "id technology product buyer confidence confidence_reason criteria category family url event_date description features value_mechanism applications competition validation".split()
ANALYSIS = {"buyer": "Покупатели", "product": "Что продавать", "value_mechanism": "Экономический механизм"}
CRITERION_LABELS = dict(zip(CRITERIA, ("Боль", "Покупатель", "Ценность", "Повторяемость", "Момент")))
ALT_TYPES = {"manual": "Ручной процесс", "builtin": "Встроенная функция", "commercial": "Платный продукт", "open_source": "Open source"}
ALT_MARKETS = {"ru": "РФ", "global": "Зарубежный"}
ALT_FIELDS = ("name", "url", "type", "market", "price", "facts", "covers", "edge", "gap")
KIND_LABELS = {"fact": "Факт", "vendor_claim": "Заявление поставщика", "hypothesis": "Гипотеза", "unknown": "Нет данных"}


def fields(value, expected, path):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{path}: expected exactly {', '.join(expected)}")


def text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected nonempty text")


def fact(value, path):
    fields(value, ("text", "url"), path)
    text(value["text"], path + ".text")
    text(value["url"], path + ".url")
    canonical(value["url"])


def validate_competition(entry):
    competition = entry["competition"]
    fields(competition, ("summary", "alternatives"), "competition")
    text(competition["summary"], "competition.summary")
    alternatives = competition["alternatives"]
    if not isinstance(alternatives, list):
        raise ValueError("competition.alternatives must be an array")
    for alternative in alternatives:
        fields(alternative, ALT_FIELDS, "alternative")
        for key in ("name", "covers", "edge", "gap"):
            text(alternative[key], "alternative." + key)
        if alternative["type"] not in ALT_TYPES or alternative["market"] not in ALT_MARKETS:
            raise ValueError("alternative.type must be manual, builtin, commercial or open_source; market ru or global")
        if alternative["url"] is None:
            if alternative["type"] != "manual":
                raise ValueError("alternative.url may be null only for a manual process")
        else:
            text(alternative["url"], "alternative.url")
            canonical(alternative["url"])
        if alternative["price"] is not None:
            fact(alternative["price"], "alternative.price")
        if not isinstance(alternative["facts"], list):
            raise ValueError("alternative.facts must be an array")
        for item in alternative["facts"]:
            fact(item, "alternative.fact")
    timing = entry["criteria"].get("timing")
    if isinstance(timing, dict):
        value = timing.get("value")
        if isinstance(value, int) and value >= 15 and (len(alternatives) < 2 or all(x["type"] == "manual" for x in alternatives)):
            raise ValueError("timing >= 15 needs at least two alternatives, one of them not manual")
        if timing.get("kind") == "fact" and not any(x["facts"] or x["price"] for x in alternatives):
            raise ValueError("timing kind=fact needs an alternative with sourced facts or price")
    if entry["confidence"] in ("medium", "high") and not alternatives:
        raise ValueError("medium or high confidence needs at least one studied alternative")


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD")
    return date.fromisoformat(value)


def validate(document):
    fields(document, ("schema_version", "meta", "entries", "coverage"), "digest")
    if document["schema_version"] != "ai-b2b-digest/v4":
        raise ValueError("Expected schema_version=ai-b2b-digest/v4")
    meta = document["meta"]
    fields(meta, ("period_start", "period_end", "generated_at", "market", "segment", "summary"), "meta")
    for key, value in meta.items():
        text(value, "meta." + key)
    start, end = day(meta["period_start"]), day(meta["period_end"])
    generated = datetime.fromisoformat(meta["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None or start > end:
        raise ValueError("Use an ordered period and a generated_at timestamp with timezone")
    entries = document["entries"]
    if not isinstance(entries, list):
        raise ValueError("entries must be an array")
    urls, families = set(), set()
    for entry in entries:
        fields(entry, ENTRY_FIELDS, "entry")
        for key in set(ENTRY_FIELDS) - {"event_date", "criteria", "applications", "competition", "features", *ANALYSIS}:
            text(entry[key], "entry." + key)
        features = entry["features"]
        if not isinstance(features, list) or not 1 <= len(features) <= 5:
            raise ValueError("features must contain one to five main capabilities")
        for feature in features:
            text(feature, "feature")
        for key in ANALYSIS:
            analysis = entry[key]
            fields(analysis, ("facts", "opinion"), key)
            text(analysis["opinion"], key + ".opinion")
            if not isinstance(analysis["facts"], list):
                raise ValueError(key + ".facts must be an array")
            for item in analysis["facts"]:
                fact(item, key + ".fact")
        if entry["category"] not in CATEGORIES:
            raise ValueError("category must be products, tools or models")
        url = canonical(entry["url"]).rstrip("/")
        if url in urls:
            raise ValueError("Each technology appears once; put its use cases in applications")
        urls.add(url)
        if entry["category"] == "models":
            family = entry["family"].strip().casefold()
            if family in families:
                raise ValueError("Combine releases of the same model family into one card")
            families.add(family)
        if entry["event_date"] is not None and not start <= day(entry["event_date"]) <= end:
            raise ValueError("Event date is outside the digest period; use older material as an alternative")
        applications = entry["applications"]
        if not isinstance(applications, list) or not 1 <= len(applications) <= 3:
            raise ValueError("applications must contain one to three concrete workflows")
        for application in applications:
            text(application, "application")
        if not isinstance(entry["criteria"], dict):
            raise ValueError("criteria must be an object")
        for criterion in entry["criteria"].values():
            fields(criterion, ("value", "kind", "reason", "evidence"), "criterion")
        validate_competition(entry)
    ranked = score(entries)
    for entry in entries:
        for criterion in entry["criteria"].values():
            for ref in criterion["evidence"]:
                if ref.startswith(("http://", "https://")):
                    canonical(ref)
    coverage = document["coverage"]
    if not isinstance(coverage, list) or not coverage:
        raise ValueError("coverage must contain source status records")
    sources = set()
    for source in coverage:
        fields(source, ("source", "status", "checked", "selected", "note"), "coverage item")
        for key in ("source", "status", "note"):
            text(source[key], key)
        if source["source"] in sources or source["status"] not in STATUS_LABELS:
            raise ValueError("Coverage source names must be unique and statuses valid")
        sources.add(source["source"])
        if any(type(source[key]) is not int or source[key] < 0 for key in ("checked", "selected")):
            raise ValueError("Coverage counts must be nonnegative integers")
        if source["selected"] > source["checked"]:
            raise ValueError("Selected count cannot exceed checked count")
    return ranked


def esc(value):
    return html.escape(str(value), quote=True)


def link(label, url):
    return f'<a href="{esc(canonical(url))}" target="_blank" rel="noopener noreferrer">{esc(label)} ↗</a>'


def score_label(row):
    if row["score"] is not None:
        return f"{row['score']}/100"
    if len(row["missing"]) == 5:
        return "недостаточно данных"
    return f"{row['score_range'][0]}–{row['score_range'][1]}/100"


def plural(count, forms):
    tail = count % 100
    form = forms[2] if 11 <= tail <= 14 else forms[0] if count % 10 == 1 else forms[1] if 2 <= count % 10 <= 4 else forms[2]
    return f"{count} {form}"


def competition_section(competition):
    rows, facts = [], []
    for x in competition["alternatives"]:
        name = link(x["name"], x["url"]) if x["url"] else esc(x["name"])
        price = link(x["price"]["text"], x["price"]["url"]) if x["price"] else "не найдена"
        rows.append(f'<tr><th scope="row">{name}</th><td>{ALT_TYPES[x["type"]]}</td><td>{ALT_MARKETS[x["market"]]}</td><td>{price}</td>'
                    f'<td>{esc(x["covers"])}</td><td>{esc(x["edge"])}</td><td>{esc(x["gap"])}</td></tr>')
        facts += [f'<li><strong>{esc(x["name"])}:</strong> {esc(f["text"])} {link("Источник", f["url"])}</li>' for f in x["facts"]]
    table = ('<div class="table-scroll"><table class="competitors"><thead><tr><th scope="col">Конкурент</th><th scope="col">Тип</th><th scope="col">Рынок</th>'
             '<th scope="col">Цена</th><th scope="col">Закрывает</th><th scope="col">Преимущество идеи</th><th scope="col">Где сильнее</th></tr></thead><tbody>'
             + ''.join(rows) + '</tbody></table></div>') if rows else '<p class="no-facts">Проверенный конкурент пока не найден.</p>'
    facts = '<div class="analysis-block facts"><h4>Факты о конкурентах</h4><ul>' + ''.join(facts) + '</ul></div>' if facts else ''
    return f'<section class="prose-section"><h3>Конкуренты</h3><p>{esc(competition["summary"])}</p>{table}{facts}</section>'


def landscape(entries):
    blocks = []
    for category, title in CATEGORIES.items():
        rows = [x for x in entries if x["category"] == category]
        if not rows:
            continue
        alternatives = [a for x in rows for a in x["competition"]["alternatives"]]
        by_type = ', '.join(f'{label.lower()}: {n}' for key, label in ALT_TYPES.items() if (n := sum(a["type"] == key for a in alternatives)))
        seen = {}
        for x in rows:
            for key, name in {(canonical(a["url"]).rstrip("/") if a["url"] else a["name"].casefold()): a["name"] for a in x["competition"]["alternatives"]}.items():
                seen.setdefault(key, [name, 0])[1] += 1
        shared = ', '.join(f'{esc(name)} ({n})' for name, n in sorted(seen.values(), key=lambda v: (-v[1], v[0])) if n >= 2) or 'нет'
        blocks.append(f'<div class="landscape-row"><h4>{title}</h4><ul>'
                      f'<li>Карточек: {len(rows)}; конкурентов всего: {len(alternatives)}{" (" + by_type + ")" if by_type else ""}</li>'
                      f'<li>С российским аналогом: {sum(any(a["market"] == "ru" for a in x["competition"]["alternatives"]) for x in rows)}</li>'
                      f'<li>Без проверенного конкурента: {sum(not x["competition"]["alternatives"] for x in rows)}</li>'
                      f'<li>Встречаются в нескольких карточках: {shared}</li></ul></div>')
    return ''.join(blocks) or '<p>Карточек нет.</p>'


def render(document):
    ranked = validate(document)
    meta = document["meta"]
    counts = {category: sum(x["category"] == category for x in document["entries"]) for category in CATEGORIES}
    sections, templates = [], []
    for number, (category, title) in enumerate(CATEGORIES.items(), 1):
        complete = [x for x in ranked["ranked"] if x["category"] == category]
        pending = [x for x in ranked["needs_data"] if x["category"] == category]
        cards = []
        for index, row in enumerate(complete + pending, 1):
            template_id = f"item-{number}-{index}"
            position = f"№ {index}" if row["score"] is not None else "На проверку"
            rating = score_label(row)
            search = esc(json.dumps(row, ensure_ascii=False).lower())
            cards.append(f'''<button class="card" data-id="{esc(row['id'])}" data-template="{template_id}" data-search="{search}" aria-haspopup="dialog" aria-controls="item-dialog">
<span class="card-top"><span class="tag">{position} · {esc(row['event_date'] or 'Дата не подтверждена')}</span><span class="arrow" aria-hidden="true">↗</span></span>
<span class="card-title">{esc(row['technology'].partition(' — ')[0])}</span><span class="card-description">{esc(row['description'])}</span>
<span class="card-bottom"><span class="rating {'pending' if row['score'] is None else ''}" aria-label="Оценка потенциала: {rating}">{rating}</span><span class="competitor-count">{plural(len(row['competition']['alternatives']), ('конкурент', 'конкурента', 'конкурентов'))}</span><span class="open-label">Полный разбор →</span></span></button>''')
            parts = [f'''<header class="item-heading"><p class="eyebrow">{esc(title)} · {position}</p><h2 id="item-title" tabindex="-1">{esc(row['technology'])}</h2><div class="item-meta"><strong>{rating}</strong><span>Уверенность: {CONFIDENCE_LABELS[row['confidence']]}</span></div></header>
<div class="event"><span>Событие: {esc(row['event_date'] or 'дата не подтверждена')}</span>{link('Первоисточник', row['url'])}</div>
<section class="prose-section"><h3>О продукте</h3><p>{esc(row['description'])}</p></section>
<section class="prose-section"><h3>Главные функции</h3><ul>{''.join('<li>' + esc(x) + '</li>' for x in row['features'])}</ul></section>''']
            for key, heading in ANALYSIS.items():
                analysis = row[key]
                facts = ''.join(f'<li>{esc(fact["text"])} {link("Источник", fact["url"])}</li>' for fact in analysis['facts'])
                facts = '<ul>' + facts + '</ul>' if facts else '<p class="no-facts">В просмотренных источниках факты по этому пункту не найдены.</p>'
                parts.append(f'''<section class="prose-section" data-analysis="{key}"><h3>{heading}</h3>
<div class="analysis-block facts"><h4>Факты из источников</h4>{facts}</div>
<div class="analysis-block opinion"><h4>Мнение агента</h4><p>{esc(analysis['opinion'])}</p></div></section>''')
            parts.append('<section class="prose-section"><h3>Применения</h3><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in row['applications']) + '</ul></section>')
            parts.append(competition_section(row['competition']))
            scores, reasons = [], []
            for key, label in CRITERION_LABELS.items():
                criterion = row['criteria'][key]
                value = criterion['value'] if criterion['value'] is not None else 'н/д'
                scores.append(f'<div><span>{label}</span><strong>{value}<small> / 20</small></strong></div>')
                citations = '; '.join(link(ref, ref) if ref.startswith(('https://', 'http://')) else esc(ref) for ref in criterion['evidence'])
                reasons.append(f'<li><strong>{label} · {KIND_LABELS[criterion["kind"]]}</strong><p>{esc(criterion["reason"])}</p><p>{citations}</p></li>')
            parts.append('<section class="prose-section"><h3>Оценка потенциала</h3><div class="score-grid">' + ''.join(scores) + '</div><details class="reasons"><summary>Обоснования пяти оценок</summary><ul>' + ''.join(reasons) + '</ul></details></section>')
            for key, heading in (('confidence_reason', 'Уверенность в потенциале'), ('validation', 'Проверка спроса и ценности')):
                value = row[key].replace('; core commercial criteria are unknown', '. Не все ключевые коммерческие критерии оценены.')
                parts.append(f'<section class="prose-section"><h3>{heading}</h3><p>{esc(value)}</p></section>')
            evidence = list(dict.fromkeys([row['url']] + [ref for criterion in row['criteria'].values() for ref in criterion['evidence']] + [fact['url'] for key in ANALYSIS for fact in row[key]['facts']]
                                         + [ref for x in row['competition']['alternatives'] for ref in [x['url'], x['price'] and x['price']['url']] + [f['url'] for f in x['facts']] if ref]))
            citations = ''.join('<li>' + (link(ref, ref) if ref.startswith(('https://', 'http://')) else esc(ref)) + '</li>' for ref in evidence)
            parts.append('<section class="prose-section"><h3>Основания и источники</h3><ol class="sources">' + citations + '</ol></section>')
            templates.append(f'<template id="{template_id}"><article>' + ''.join(parts) + '</article></template>')
        sections.append(f'<section class="category" data-category="{category}" aria-labelledby="heading-{category}"><div class="section-heading"><h2 id="heading-{category}"><span>0{number}</span> {title}</h2><span class="section-count">{len(cards)} находок</span></div><div class="grid">' + ''.join(cards) + '</div></section>')
    coverage = ''.join(f'<details class="coverage-row"><summary><span>{esc(row["source"])}</span><small>{STATUS_LABELS[row["status"]]} · {row["checked"]} / {row["selected"]}</small></summary><p>{esc(row["note"])}</p></details>' for row in document['coverage'])
    filter_labels = {"all": "Все", "products": "Продукты", "tools": "Инструменты", "models": "Модели"}
    counts['all'] = len(document['entries'])
    filters = ''.join(f'<button class="filter" data-filter="{key}" aria-pressed="{str(key == "all").lower()}">{label} <small>{counts[key]}</small></button>' for key, label in filter_labels.items())
    values = {"PERIOD": esc(meta['period_start'] + ' — ' + meta['period_end']), "TOTAL": str(counts['all']),
              "MARKET": esc(meta['market']), "SEGMENT": esc(meta['segment']), "GENERATED": esc(meta['generated_at']),
              "SUMMARY": esc(meta['summary']), "SECTIONS": ''.join(sections), "TEMPLATES": ''.join(templates),
              "COVERAGE": coverage, "FILTERS": filters, "LANDSCAPE": landscape(document['entries'])}
    template = Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
    return re.sub(r'__([A-Z]+)__', lambda match: values[match[1]], template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = render(json.loads(args.input.read_text(encoding="utf-8")))
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(result)
        else:
            print(result, end="")
    except (OSError, ValueError) as error:
        print(f"report: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
