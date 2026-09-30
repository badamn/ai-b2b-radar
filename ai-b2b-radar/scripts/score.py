#!/usr/bin/env python3
"""Validate evidence-backed criterion values and calculate sellability scores."""

import argparse
import json
import sys
from pathlib import Path

CRITERIA = ("pain", "buyer", "value", "repeatability", "timing")
CONFIDENCE = {"low": 0, "medium": 1, "high": 2}


def score(records):
    if not isinstance(records, list):
        raise ValueError("Input must be a JSON array of product hypotheses")
    ranked, needs_data, seen = [], [], set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each hypothesis must be an object")
        for field in ("id", "technology", "product", "buyer", "confidence_reason"):
            value = record.get(field)
            if field in ("product", "buyer") and isinstance(value, dict):
                value = value.get("opinion")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Missing nonempty text: {field}")
        if record["id"] in seen:
            raise ValueError(f"Duplicate hypothesis id: {record['id']}")
        seen.add(record["id"])
        confidence = record.get("confidence")
        if not isinstance(confidence, str) or confidence not in CONFIDENCE:
            raise ValueError("confidence must be low, medium or high")
        criteria = record.get("criteria")
        if not isinstance(criteria, dict) or set(criteria) != set(CRITERIA):
            raise ValueError(f"criteria must contain exactly {', '.join(CRITERIA)}")
        missing, total = [], 0
        for name in CRITERIA:
            item = criteria[name]
            if not isinstance(item, dict) or "value" not in item:
                raise ValueError(f"{name}: expected an object with value")
            value = item["value"]
            if value is not None and (type(value) is not int or value not in (0, 5, 10, 15, 20)):
                raise ValueError(f"{name}: value must be null, 0, 5, 10, 15 or 20")
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError(f"{name}: reason is required, including for unknown values")
            if item.get("kind") not in ("fact", "vendor_claim", "hypothesis", "unknown"):
                raise ValueError(f"{name}: invalid evidence kind")
            if (value is None) != (item["kind"] == "unknown"):
                raise ValueError(f"{name}: null value must use kind=unknown, and conversely")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or any(not isinstance(x, str) or not x.strip() for x in evidence):
                raise ValueError(f"{name}: evidence must be a list of URLs or supplied file references")
            if item["kind"] in ("fact", "vendor_claim") and not evidence:
                raise ValueError(f"{name}: sourced claims require evidence")
            if value is None:
                missing.append(name)
            else:
                total += value
        result = dict(record)
        result.update(score=total if not missing else None,
                      score_range=[total, total + 20 * len(missing)], missing=missing)
        if set(missing) & {"pain", "buyer", "value"}:
            result["confidence"] = "low"
            result["confidence_reason"] += "; core commercial criteria are unknown"
        (needs_data if missing else ranked).append(result)
    ranked.sort(key=lambda row: (-row["score"], -CONFIDENCE[row["confidence"]], row["id"]))
    needs_data.sort(key=lambda row: row["id"])
    return {"rubric": "sellability-v2", "ranked": ranked, "needs_data": needs_data}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="New JSON file; existing files are preserved")
    args = parser.parse_args()
    try:
        result = score(json.loads(args.input.read_text(encoding="utf-8")))
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        else:
            print(encoded, end="")
    except (ValueError, OSError) as error:
        print(f"score: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
