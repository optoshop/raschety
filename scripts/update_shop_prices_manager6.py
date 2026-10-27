#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обновление regular_price четырёхклапанных в shop_*.csv из логики manager-6.

Источник формул: https://raschet-op.vercel.app/manager-6
  (JS: /plugins/raschet-op/assets/raschet-op-6.js, CFG на странице).

Правила: см. docs/pricing-rules.md
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SHEETS = [
    (2200, 1200),
    (2000, 1100),
    (1500, 850),
    (1400, 700),
    (1900, 950),
    (1700, 900),
    (2070, 830),
]
LIMIT_SQM = 300.0
VAT = 0.2
BASE_COST = 1.0
PROD_U_COEFF = 1.73
SUMMARY_QTYS = [50, 100, 300, 500, 1000]

QTY_COEFF = [2.5, 1.68, 1.44, 1.42, 1.38, 1.35, 1.33, 1.32, 1.31, 1.25, 1.21, 1.2, 1.19, 1.18, 1.17]
AREA_COEFF = [1.3, 1.3, 1.17, 1.16, 1.14, 1.13, 1.12, 1.11, 1.1, 1.05, 1.02, 1.0, 0.97, 0.94, 0.89, 0.86]
MARKUP_QTY = [1.3, 1.18, 1.17, 1.16, 1.15, 1.14, 1.13, 1.12, 1.11, 1.1, 1.09, 1.08, 1.07, 1.06, 1.05]
MARKUP_AREA = [1.15, 1.1, 1.05, 1.09, 1.085, 1.08, 1.075, 1.07, 1.065, 1.06, 1.057, 1.055, 1.053, 1.051, 1.05, 1.049]
MARKUP_U_QTY = [2.4, 1.7, 1.5, 1.4, 1.3, 1.25, 1.2, 1.1, 1.0, 0.9, 0.8, 0.75, 0.73, 0.72, 1.0]
MARKUP_U_AREA = [
    1.9, 1.85, 1.8, 1.7, 1.65, 1.6, 1.55, 1.5, 1.45, 1.4, 1.35, 1.3, 1.2, 1.1, 1.09, 1.07, 1.06, 1.05, 1.04,
]

TIER_MAP = {
    "50-90 шт": 0,
    "100-290 шт": 1,
    "300-490 шт": 2,
    "500-990 шт": 3,
    "1000+ шт": 4,
    "1000 + шт": 4,
}

# Подписи тиров в post_excerpt (короткое описание на сайте)
EXCERPT_TIER_LABELS = [
    "50-90 шт",
    "100-290 шт",
    "300-490 шт",
    "500-990 шт",
    "1000 + шт",
]

# <strong>50-90 шт - 2,46 руб</strong> / «руб / шт» / «с НДС» / nbsp
_TIER_LINE_RES = [
    re.compile(r"<strong>\s*50-90\s*шт\s*[-–—]\s*[^<]*</strong>", re.I),
    re.compile(r"<strong>\s*100-290\s*шт\s*[-–—]\s*[^<]*</strong>", re.I),
    re.compile(r"<strong>\s*300-490\s*шт\s*[-–—]\s*[^<]*</strong>", re.I),
    re.compile(r"<strong>\s*500-990\s*шт\s*[-–—]\s*[^<]*</strong>", re.I),
    re.compile(r"<strong>\s*1000\s*\+\s*шт\s*[-–—]\s*[^<]*</strong>", re.I),
]

FOUR_VALVE = "Четырехклапанные"
SKIP_SKUS = {"K1034", "K59", "K60", "K61", "K62", "K63", "K64", "K65"}
# Исключение: 600×400×400 — цены с сайта не менять (см. pricing-rules.md)
EXCEPTION_DIMS = {(600, 400, 400)}

DIM_RE = re.compile(r"(\d+)\s*[xх×]\s*(\d+)\s*[xх×]\s*(\d+)", re.I)


def money(v: float) -> str:
    """Как toFixed(2) в калькуляторе (с НДС), без лишних нулей справа от точки."""
    s = f"{v + 1e-12:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fnum(s: str | None):
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(str(s).replace(",", ".").strip())
    except ValueError:
        return None


def get_box_area(L: int, W: int, H: int) -> float:
    return (2 * (L + W) + 60) * (W + H + 8) / 1_000_000


def blank_size(L: int, W: int, H: int) -> tuple[int, int]:
    return 2 * (L + W) + 60, W + H + 8


def can_u(L: int, W: int, H: int) -> bool:
    return L >= 125 and W >= 125 and H >= 63


def can_bm(L: int, W: int, H: int) -> bool:
    return L >= 240 and W >= 80 and H >= 80 and (W + H) >= 280


def get_quantity_coeff(q: int, coeffs: list[float]) -> float:
    if q < 50:
        return coeffs[0]
    if q <= 99:
        return coeffs[1]
    if q <= 199:
        return coeffs[2]
    if q <= 299:
        return coeffs[3]
    if q <= 399:
        return coeffs[4]
    if q <= 499:
        return coeffs[5]
    if q <= 699:
        return coeffs[6]
    if q <= 999:
        return coeffs[7]
    if q <= 1499:
        return coeffs[8]
    if q <= 1999:
        return coeffs[9]
    if q <= 2999:
        return coeffs[10]
    if q <= 4999:
        return coeffs[11]
    if q <= 6999:
        return coeffs[12]
    if q <= 9999:
        return coeffs[13]
    return coeffs[14]


def get_area_coeff(area: float, coeffs: list[float]) -> float:
    if area < 0.3:
        return coeffs[0]
    if area < 0.35:
        return coeffs[1]
    if area < 0.4:
        return coeffs[2]
    if area < 0.5:
        return coeffs[3]
    if area < 0.6:
        return coeffs[4]
    if area < 0.7:
        return coeffs[5]
    if area < 0.8:
        return coeffs[6]
    if area < 0.9:
        return coeffs[7]
    if area < 1.0:
        return coeffs[8]
    if area < 1.2:
        return coeffs[9]
    if area < 1.3:
        return coeffs[10]
    if area < 1.5:
        return coeffs[11]
    if area < 1.8:
        return coeffs[12]
    if area < 2.0:
        return coeffs[13]
    if area < 2.2:
        return coeffs[14]
    return coeffs[15]


def get_markup_quantity_coeff(q: int, coeffs: list[float]) -> float:
    if q < 50:
        return coeffs[0]
    if q <= 90:
        return coeffs[1]
    if q <= 190:
        return coeffs[2]
    if q <= 290:
        return coeffs[3]
    if q <= 390:
        return coeffs[4]
    if q <= 490:
        return coeffs[5]
    if q <= 690:
        return coeffs[6]
    if q <= 990:
        return coeffs[7]
    if q <= 1490:
        return coeffs[8]
    if q <= 1900:
        return coeffs[9]
    if q <= 2900:
        return coeffs[10]
    if q <= 4900:
        return coeffs[11]
    if q <= 6900:
        return coeffs[12]
    if q <= 9900:
        return coeffs[13]
    return coeffs[14]


def get_markup_u_area_coeff(area: float, coeffs: list[float]) -> float:
    if area < 0.15:
        return coeffs[0]
    if area < 0.2:
        return coeffs[1]
    if area < 0.25:
        return coeffs[2]
    if area < 0.3:
        return coeffs[3]
    if area < 0.35:
        return coeffs[4]
    if area < 0.4:
        return coeffs[5]
    if area < 0.5:
        return coeffs[6]
    if area < 0.6:
        return coeffs[7]
    if area < 0.7:
        return coeffs[8]
    if area < 0.8:
        return coeffs[9]
    if area < 0.9:
        return coeffs[10]
    if area < 1.0:
        return coeffs[11]
    if area < 1.2:
        return coeffs[12]
    if area < 1.3:
        return coeffs[13]
    if area < 1.5:
        return coeffs[14]
    if area < 1.8:
        return coeffs[15]
    if area < 2.0:
        return coeffs[16]
    if area < 2.2:
        return coeffs[17]
    return coeffs[18]


def best_fit(bw: int, bh: int, sw: int, sh: int):
    if bw > sw or bh > sh:
        return None
    cols = sw // bw
    rows = sh // bh
    return {
        "count": cols * rows,
        "cols": cols,
        "rows": rows,
        "bw": bw,
        "bh": bh,
        "sw": sw,
        "sh": sh,
        "sheetArea": (sw * sh) / 1_000_000,
    }


def waste_discount_fraction(w: float, h: float, area: float) -> float:
    big = (w >= 720 and h >= 320) or (w >= 700 and h >= 610) or (area > 1.0 and (w >= 500 or h >= 500))
    return 1.0 if big else 0.0


def calculate_all_with_waste(L: int, W: int, H: int, qty: int, cq: float, ca: float) -> float:
    bw, bh = blank_size(L, W, H)
    one = get_box_area(L, W, H)
    price_psqm = BASE_COST * cq * ca
    remaining = qty
    offcut_total = 0.0
    paid_waste_sheets = 0.0
    if remaining > 0:
        possibles = []
        for sw, sh in SHEETS:
            fit = best_fit(bw, bh, sw, sh)
            if not fit:
                continue
            per = fit["count"]
            full = remaining // per
            rem = remaining % per
            total_waste = 0.0
            total_discount = 0.0
            rw = None
            bwaste = None
            if fit["cols"] * fit["bw"] < sw:
                w = sw - fit["cols"] * fit["bw"]
                area = (w * sh) / 1_000_000
                disc = waste_discount_fraction(w, sh, area)
                rw = (w, sh, area, disc)
                total_waste += area
                total_discount += area * disc
            if fit["rows"] * fit["bh"] < sh:
                h = sh - fit["rows"] * fit["bh"]
                area = (fit["cols"] * fit["bw"] * h) / 1_000_000
                disc = waste_discount_fraction(fit["cols"] * fit["bw"], h, area)
                bwaste = (fit["cols"] * fit["bw"], h, area, disc)
                total_waste += area
                total_discount += area * disc
            if rw and bwaste and rw[3] == 0 and bwaste[3] > 0:
                new_bottom_w = sw
                new_bottom_h = bwaste[1]
                new_bottom_area = (new_bottom_w * new_bottom_h) / 1_000_000
                new_bottom_disc = waste_discount_fraction(new_bottom_w, new_bottom_h, new_bottom_area)
                new_right_w = rw[0]
                new_right_h = sh - bwaste[1]
                new_right_area = (new_right_w * new_right_h) / 1_000_000
                total_waste = new_bottom_area + new_right_area
                total_discount = new_bottom_area * new_bottom_disc
            paid = total_waste - total_discount
            useful = fit["sheetArea"] - total_waste
            cost_sheet = full * (useful * price_psqm + paid * BASE_COST) + rem * one * price_psqm
            total_paid = full * paid * BASE_COST
            possibles.append(
                {
                    "costPerBox": cost_sheet / remaining,
                    "totalCost": cost_sheet,
                    "totalPaidWasteCost": total_paid,
                }
            )
        if possibles:
            best = min(possibles, key=lambda x: x["costPerBox"])
            paid_waste_sheets = best["totalPaidWasteCost"]
    useful = qty * one
    total_waste_cost = offcut_total + paid_waste_sheets
    return useful * price_psqm + total_waste_cost


def calculate_all(L: int, W: int, H: int, qty: int, cq: float, ca: float) -> float:
    one = get_box_area(L, W, H)
    useful = one * qty
    if useful <= LIMIT_SQM:
        return calculate_all_with_waste(L, W, H, qty, cq, ca)
    price_psqm = BASE_COST * cq * ca
    return useful * price_psqm


def sale_prices_inc_vat(L: int, W: int, H: int) -> tuple[list[float] | None, str]:
    """5 цен «Продажа …» с НДС для qty 50/100/300/500/1000. source: BM|U|stamp."""
    if can_bm(L, W, H):
        source = "BM"
    elif can_u(L, W, H):
        source = "U"
    else:
        return None, "stamp"

    one = get_box_area(L, W, H)
    out: list[float] = []
    for q in SUMMARY_QTYS:
        if source == "BM":
            cq = get_quantity_coeff(q, QTY_COEFF)
            ca = get_area_coeff(one, AREA_COEFF)
            cost = calculate_all(L, W, H, q, cq, ca) / q
            markup = get_markup_quantity_coeff(q, MARKUP_QTY) * get_area_coeff(one, MARKUP_AREA)
            sale = cost * markup
        else:
            cost = one * BASE_COST * PROD_U_COEFF
            markup = get_markup_quantity_coeff(q, MARKUP_U_QTY) * get_markup_u_area_coeff(one, MARKUP_U_AREA)
            sale = cost * markup
        out.append(round(sale * (1 + VAT) + 1e-12, 2))
    return out, source


def parse_dims(title: str) -> tuple[int, int, int] | None:
    m = DIM_RE.search(title or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_four_valve(row: dict) -> bool:
    return FOUR_VALVE in (row.get("tax:product_cat") or "")


def has_sale(row: dict) -> bool:
    return bool((row.get("sale_price") or "").strip())


def norm_qty(q: str) -> str:
    return (q or "").strip().replace("1000 + шт", "1000+ шт")


def price_ru(price: str | float) -> str:
    """Цена для текста: запятая как десятичный разделитель."""
    if isinstance(price, float):
        s = money(price)
    else:
        s = str(price).strip().replace(",", ".")
        n = fnum(s)
        s = money(n) if n is not None else s
    return s.replace(".", ",")


def format_excerpt_tier_line(label: str, price: str | float) -> str:
    return f"<strong>{label} — {price_ru(price)} руб с НДС</strong>"


def sync_post_excerpt_tiers(excerpt: str, tier_prices: list[str | float]) -> tuple[str, int]:
    """Подставить актуальные цены тиров в post_excerpt. Возвращает (текст, сколько строк заменено)."""
    if not excerpt or len(tier_prices) != 5:
        return excerpt, 0
    text = excerpt
    replaced = 0
    for i, (rx, label) in enumerate(zip(_TIER_LINE_RES, EXCERPT_TIER_LABELS)):
        new_line = format_excerpt_tier_line(label, tier_prices[i])
        text, n = rx.subn(new_line, text, count=1)
        replaced += n
    return text, replaced


def verify_examples() -> None:
    cases = [
        ((150, 150, 250), "U", [1.62, 1.43, 1.24, 1.14, 0.95]),
        ((290, 225, 110), "BM", [1.13, 0.97, 0.91, 0.86, 0.80]),
        ((400, 200, 200), "BM", [1.51, 1.29, 1.21, 1.15, 1.11]),
    ]
    for dims, exp_src, exp in cases:
        got, src = sale_prices_inc_vat(*dims)
        assert src == exp_src, (dims, src, exp_src)
        assert got == [round(x, 2) for x in exp], (dims, got, exp)
    print("examples OK")


def process(shop_in: Path, shop_out: Path, stats_out: Path | None) -> dict:
    with shop_in.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise SystemExit("empty CSV")
        rows = list(reader)

    parents_by_sku: dict[str, dict] = {}
    children: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        sku = (row.get("sku") or "").strip()
        parent = (row.get("parent_sku") or "").strip()
        if parent:
            children[parent].append(row)
        elif sku:
            parents_by_sku[sku] = row

    sale_skus: set[str] = set()
    for sku, parent in parents_by_sku.items():
        if has_sale(parent) or any(has_sale(c) for c in children.get(sku, [])):
            sale_skus.add(sku)

    stats = {
        "rows_total": len(rows),
        "four_valve_parents": 0,
        "skipped_sale": [],
        "skipped_exception_600": [],
        "skipped_stamp": [],
        "skipped_skip_sku": [],
        "skipped_no_dims": [],
        "updated_parents": [],
        "source_BM": 0,
        "source_U": 0,
        "var_rows_changed": 0,
        "var_rows_numeric": 0,
        "var_rows_same": 0,
        "simple_skipped_or_n_a": 0,
        "excerpt_parents_updated": 0,
        "excerpt_lines_replaced": 0,
        "excerpt_missing_tiers": [],
        "examples": {},
        "unchanged_non_four_valve": True,
    }

    # precompute prices per parent sku we may update
    plan: dict[str, tuple[list[float], str, tuple[int, int, int]]] = {}
    for sku, parent in parents_by_sku.items():
        if not is_four_valve(parent):
            continue
        stats["four_valve_parents"] += 1
        if sku in SKIP_SKUS:
            stats["skipped_skip_sku"].append(sku)
            continue
        if sku in sale_skus:
            stats["skipped_sale"].append(sku)
            continue
        dims = parse_dims(parent.get("post_title") or "")
        if not dims:
            stats["skipped_no_dims"].append(sku)
            continue
        if dims in EXCEPTION_DIMS:
            stats["skipped_exception_600"].append(sku)
            continue
        prices, source = sale_prices_inc_vat(*dims)
        if prices is None:
            stats["skipped_stamp"].append({"sku": sku, "dims": dims, "title": parent.get("post_title")})
            continue
        plan[sku] = (prices, source, dims)
        if source == "BM":
            stats["source_BM"] += 1
        else:
            stats["source_U"] += 1

    for row in rows:
        parent_sku = (row.get("parent_sku") or "").strip()
        sku = (row.get("sku") or "").strip()
        # only variation rows of planned parents
        if not parent_sku or parent_sku not in plan:
            continue
        # never touch sale columns
        qty = norm_qty(row.get("meta:attribute_pa_colichestvo") or "")
        if qty not in TIER_MAP:
            continue
        prices, source, dims = plan[parent_sku]
        new_price = money(prices[TIER_MAP[qty]])
        old = (row.get("regular_price") or "").strip()
        old_n = fnum(old)
        new_n = fnum(new_price)
        if old == new_price:
            stats["var_rows_same"] += 1
            continue
        if old_n is not None and new_n is not None and abs(old_n - new_n) < 1e-9:
            # format-only
            row["regular_price"] = new_price
            stats["var_rows_changed"] += 1
        else:
            row["regular_price"] = new_price
            stats["var_rows_changed"] += 1
            stats["var_rows_numeric"] += 1
        if parent_sku not in stats["updated_parents"]:
            stats["updated_parents"].append(parent_sku)

    # post_excerpt: текст тиров только у parent, чьи regular_price реально меняли
    for sku in stats["updated_parents"]:
        parent = parents_by_sku.get(sku)
        if not parent:
            continue
        prices_by_tier = [None] * 5
        for child in children.get(sku, []):
            qty = norm_qty(child.get("meta:attribute_pa_colichestvo") or "")
            if qty not in TIER_MAP:
                continue
            prices_by_tier[TIER_MAP[qty]] = (child.get("regular_price") or "").strip()
        if any(p is None or p == "" for p in prices_by_tier):
            stats["excerpt_missing_tiers"].append(sku)
            continue
        new_ex, n = sync_post_excerpt_tiers(parent.get("post_excerpt") or "", prices_by_tier)
        if n:
            parent["post_excerpt"] = new_ex
            stats["excerpt_parents_updated"] += 1
            stats["excerpt_lines_replaced"] += n
        elif re.search(r"50-90\s*шт", parent.get("post_excerpt") or ""):
            stats["excerpt_missing_tiers"].append(sku)

    # examples for readme
    for label, dims in [
        ("150x150x250", (150, 150, 250)),
        ("290x225x110", (290, 225, 110)),
        ("400x200x200", (400, 200, 200)),
    ]:
        p, src = sale_prices_inc_vat(*dims)
        stats["examples"][label] = {"source": src, "prices": p}

    shop_out.parent.mkdir(parents=True, exist_ok=True)
    with shop_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # compact lists for JSON
    out_stats = {
        **stats,
        "updated_parents_count": len(stats["updated_parents"]),
        "skipped_sale_count": len(stats["skipped_sale"]),
        "skipped_stamp_count": len(stats["skipped_stamp"]),
        "skipped_exception_600_count": len(stats["skipped_exception_600"]),
    }
    if stats_out:
        stats_out.parent.mkdir(parents=True, exist_ok=True)
        stats_out.write_text(json.dumps(out_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shop-in", type=Path, default=Path("shop_27.10.csv"))
    ap.add_argument("--shop-out", type=Path, default=Path("shop_27.10-updated.csv"))
    ap.add_argument("--stats-out", type=Path, default=None)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args(argv)

    verify_examples()
    if args.verify_only:
        return 0

    stats = process(args.shop_in, args.shop_out, args.stats_out)
    print(
        json.dumps(
            {
                "out": str(args.shop_out),
                "var_rows_changed": stats["var_rows_changed"],
                "var_rows_numeric": stats["var_rows_numeric"],
                "updated_parents": stats["updated_parents_count"],
                "source_BM": stats["source_BM"],
                "source_U": stats["source_U"],
                "skipped_sale": stats["skipped_sale_count"],
                "skipped_stamp": stats["skipped_stamp_count"],
                "skipped_exception_600": stats["skipped_exception_600"],
                "excerpt_parents_updated": stats["excerpt_parents_updated"],
                "excerpt_lines_replaced": stats["excerpt_lines_replaced"],
                "excerpt_missing_tiers": stats["excerpt_missing_tiers"],
                "examples": stats["examples"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
