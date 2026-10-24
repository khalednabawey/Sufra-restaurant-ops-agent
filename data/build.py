"""Build the Sufra dataset: adapt the public sales data and derive everything else.

Reads data/raw/, data/mappings/ and data/external/ and writes one CSV per table to
data/build/, plus planted_facts.json with the answers the eval suite looks for. Every rule
is described in data/README.md, and a fixed seed makes every number reproducible.

    uv run python data/build.py --seed 42
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

DATA = Path(__file__).parent
RAW, MAPPINGS, EXTERNAL, BUILD = (DATA / d for d in ("raw", "mappings", "external", "build"))

# ─── Calendar ──────────────────────────────────────────────────────────
START, END = date(2026, 1, 1), date(2026, 6, 30)
YEAR_SHIFT = 3  # 2023 → 2026; neither is a leap year, so every date maps one to one
RAMADAN = (date(2026, 2, 18), date(2026, 3, 19))
WEEKEND = {4, 5}  # Friday and Saturday in both countries

BRANCHES = pd.DataFrame(
    [
        ("B1", "Olaya", "العليا", "Riyadh", "SA", "riyadh", "SAR", 8, date(2019, 3, 1)),
        ("B2", "Tahlia", "التحلية", "Jeddah", "SA", "jeddah", "SAR", 5, date(2020, 9, 15)),
        ("B3", "Smouha", "سموحة", "Alexandria", "EG", "alexandria", "EGP", 3, date(2021, 11, 1)),
        ("B4", "Al Malqa", "الملقا", "Riyadh", "SA", "riyadh", "SAR", None, date(2026, 4, 1)),
    ],
    columns=["branch_id", "name_en", "name_ar", "city", "country", "area", "currency",
             "source_store_id", "opened_on"],
).astype({"source_store_id": "Int64"})
BRANCH = BRANCHES.set_index("branch_id")

# ─── Sales rules ───────────────────────────────────────────────────────
PRICE_FACTOR = {"SAR": 3.75, "EGP": 25.0}  # local price per US dollar in the source
PRICE_STEP = {"SAR": 0.5, "EGP": 5.0}  # menu-style rounding

B4_SOURCE, B4_SHARE, B4_JITTER_MIN, B4_ID_OFFSET = "B1", 0.60, 10, 1_000_000

SOURCE_OPEN, SOURCE_CLOSE = 6 * 3600, 21 * 3600  # source trading hours, seconds after midnight
IFTAR = {"Riyadh": time(17, 50), "Jeddah": time(18, 25), "Alexandria": time(17, 45)}
RAMADAN_EVENING_SHARE = 0.80  # share of each Ramadan day's lines moved after iftar
RAMADAN_DAY_WINDOW = (8 * 3600, 16 * 3600)  # the rest is served 08:00–16:00

ICED_BASE_SHARE, HOT_DAY_C, COOL_DAY_C, HOT_FACTOR, COOL_FACTOR = 0.20, 40.0, 25.0, 1.5, 0.5

# ─── Inventory and waste rules ─────────────────────────────────────────
AVG_WINDOW = 14  # days of cover = closing stock ÷ average daily use over the last 14 days
PLAN_WINDOWS = (14, 28)  # orders plan on the larger of the 14- and 28-day averages
MIN_UNITS = 3  # goods counted in units always keep at least 3 on hand at the reorder point
VARIABILITY_Z = 2.0  # extra safety stock: 2 × daily standard deviation × √(lead time)
ORDER_CYCLE = {"milk": 3, "croissant_dough": 7, "espresso_beans": 7,
               "filter_coffee_beans": 7, "arabic_coffee_beans": 7}  # others: 14 days
PERISHABLE_SHELF_DAYS = 7
SAFETY_DAYS = {True: 3, False: 4}  # perishable?

WASTE_GROUP = {"milk": "fresh", "croissant_dough": "bake", "butter": "bake", "eggs": "bake"}
WASTE_RULES = {  # group: (share of use, chance of a waste entry on a given day, reason weights)
    "fresh": (0.030, 0.60, {"expired": 0.65, "quality": 0.20, "prep_error": 0.10, "damaged": 0.05}),
    "bake": (0.020, 0.30, {"prep_error": 0.40, "expired": 0.30, "quality": 0.30}),
    "dry": (0.003, 0.10, {"damaged": 0.50, "expired": 0.40, "quality": 0.10}),
}

# ─── Labour rules ──────────────────────────────────────────────────────
ORDERS_PER_STAFF_HOUR, MIN_STAFF, OVERTIME_FACTOR = 15, 2, 1.5
SHIFTS_NORMAL = [("morning", 6, 14), ("evening", 13, 21)]
SHIFTS_RAMADAN = [("day", 8, 16), ("night", 17, 25)]  # 25 = 01:00 the next day
WAGES = {  # hourly, local currency
    "SA": {"supervisor": 28, "baker": 20, "barista": 17, "cashier": 15},
    "EG": {"supervisor": 95, "baker": 70, "barista": 60, "cashier": 55},
}
ROSTER = {
    "B1": {"supervisor": 4, "baker": 2, "cashier": 2, "barista": 5},
    "B2": {"supervisor": 3, "baker": 2, "cashier": 2, "barista": 4},
    "B3": {"supervisor": 3, "baker": 2, "cashier": 2, "barista": 4},
    "B4": {"supervisor": 2, "baker": 1, "cashier": 2, "barista": 3},
}
NAMES = {
    "SA": (["Faisal", "Noura", "Abdullah", "Reem", "Khalid", "Sara", "Turki", "Lama", "Omar",
            "Hessa", "Majed", "Dana", "Rakan", "Joud", "Nawaf", "Shahad", "Bandar", "Ghada"],
           ["Al-Harbi", "Al-Qahtani", "Al-Otaibi", "Al-Shehri", "Al-Dosari", "Al-Ghamdi",
            "Al-Mutairi", "Al-Zahrani", "Al-Anazi", "Al-Shammari", "Al-Malki", "Al-Subaie"]),
    "EG": (["Mohamed", "Mariam", "Ahmed", "Nour", "Youssef", "Salma", "Mostafa", "Habiba",
            "Karim", "Farida", "Omar", "Malak", "Hassan", "Rana", "Ali", "Yasmin"],
           ["El-Sayed", "Hassan", "Abdel-Rahman", "Fahmy", "Mansour", "Soliman", "Ibrahim",
            "Shaker", "Naguib", "Ezzat", "Farouk", "Gamal"]),
}

# ─── Planted facts (the eval suite checks these) ───────────────────────
WASTE_SPIKE = {"branch": "B2", "start": date(2026, 5, 10), "end": date(2026, 5, 23),
               "shares": {"milk": 0.35, "croissant_dough": 0.50}, "reason": "prep_error"}
COST_INCREASE = {"ingredient": "pistachios", "valid_from": date(2026, 4, 1), "new_cost_usd": 0.068}
UNADJUSTED_RAMADAN_BRANCH = "B1"
# The first espresso-bean delivery to Olaya on or after 14 June is the last before the roaster's
# delay, and it arrives short: just enough to reach 1.6 days of cover on 26 June. After that the
# branch buys small local top-ups that hold stock at 1.2 days of cover.
LOW_STOCK_TRAP = {"branch": "B1", "ingredient": "espresso_beans", "from": date(2026, 6, 14),
                  "low_on": date(2026, 6, 26), "low_cover": 1.6, "floor_days": 1.2}
REVIEWS_PER_BRANCH = {"B1": 260, "B2": 240, "B3": 250, "B4": 110}


def days(first: date, last: date) -> list[date]:
    return [first + timedelta(n) for n in range((last - first).days + 1)]


def menu_price(usd, currency: str):
    step = PRICE_STEP[currency]
    return np.floor(np.asarray(usd) * PRICE_FACTOR[currency] / step + 0.5) * step


def rng_for(seed: int, step: str) -> np.random.Generator:
    """Independent stream per step, so changing one rule doesn't reshuffle the others."""
    return np.random.default_rng([seed, sum(map(ord, step))])


# ─── Sales ─────────────────────────────────────────────────────────────

def load_sales(menu: pd.DataFrame) -> pd.DataFrame:
    src = pd.read_csv(RAW / "coffee_shop_sales.csv")
    store_to_branch = {int(s): b for b, s in BRANCH.source_store_id.dropna().items()}
    sold_at = pd.to_datetime(src.transaction_date + " " + src.transaction_time)
    item_of = menu.dropna(subset=["source_product_id"]).set_index("source_product_id").item_id
    return pd.DataFrame({
        "sale_id": src.transaction_id,
        "branch_id": src.store_id.map(store_to_branch),
        "sold_at": sold_at + pd.DateOffset(years=YEAR_SHIFT),
        "source_product_id": src.product_id,
        "item_id": src.product_id.map(item_of),
        "qty": src.transaction_qty,
        "usd_price": src.unit_price,
    })


def add_new_branch(sales: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """B4 Al Malqa: about 60% of B1's lines from its opening day, times jittered ±10 minutes."""
    opened = pd.Timestamp(BRANCH.loc["B4", "opened_on"])
    base = sales[(sales.branch_id == B4_SOURCE) & (sales.sold_at >= opened)]
    b4 = base[rng.random(len(base)) < B4_SHARE].copy()
    jitter = pd.to_timedelta(rng.integers(-B4_JITTER_MIN * 60, B4_JITTER_MIN * 60 + 1, len(b4)), "s")
    day = b4.sold_at.dt.normalize()
    b4["sold_at"] = (b4.sold_at + jitter).clip(day + pd.Timedelta(SOURCE_OPEN, "s"),
                                               day + pd.Timedelta(SOURCE_CLOSE - 1, "s"))
    b4["branch_id"] = "B4"
    b4["sale_id"] = B4_ID_OFFSET + np.arange(1, len(b4) + 1)
    return pd.concat([sales, b4], ignore_index=True)


def apply_ramadan(sales: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Move 80% of each Ramadan day's lines into the evening after iftar, keeping their order."""
    day = sales.sold_at.dt.normalize()
    in_ramadan = (day >= pd.Timestamp(RAMADAN[0])) & (day <= pd.Timestamp(RAMADAN[1]))
    secs = (sales.sold_at - day).dt.total_seconds()
    frac = ((secs - SOURCE_OPEN) / (SOURCE_CLOSE - SOURCE_OPEN)).clip(0, 1 - 1e-9)

    iftar = sales.branch_id.map(BRANCH.city).map(
        {c: t.hour * 3600 + t.minute * 60 for c, t in IFTAR.items()})
    eve_start, eve_end = iftar + 30 * 60, 24 * 3600 - 1  # stay on the same business day
    evening = rng.random(len(sales)) < RAMADAN_EVENING_SHARE
    day_start, day_end = RAMADAN_DAY_WINDOW
    new_secs = np.where(evening, eve_start + frac * (eve_end - eve_start),
                        day_start + frac * (day_end - day_start))
    sales.loc[in_ramadan, "sold_at"] = (day + pd.to_timedelta(np.floor(new_secs), "s"))[in_ramadan]
    return sales


def apply_weather(sales: pd.DataFrame, weather: pd.DataFrame, menu: pd.DataFrame,
                  rng: np.random.Generator) -> pd.DataFrame:
    """Serve a share of eight hot drinks iced: 20% normally, ×1.5 at 40°C or above, ×0.5 below 25°C."""
    iced_of = menu.dropna(subset=["iced_of"]).set_index("iced_of").item_id
    temp = weather.set_index(["city", "obs_date"]).temp_max_c
    keys = list(zip(sales.branch_id.map(BRANCH.city), sales.sold_at.dt.date, strict=True))
    t = temp.reindex(keys).to_numpy()
    share = ICED_BASE_SHARE * np.where(t >= HOT_DAY_C, HOT_FACTOR, np.where(t < COOL_DAY_C, COOL_FACTOR, 1.0))
    switch = sales.item_id.isin(iced_of.index).to_numpy() & (rng.random(len(sales)) < share)
    sales.loc[switch, "item_id"] = sales.loc[switch, "item_id"].map(iced_of)
    return sales


def finish_sales(sales: pd.DataFrame) -> pd.DataFrame:
    sales["currency"] = sales.branch_id.map(BRANCH.currency)
    sales["unit_price"] = 0.0
    for cur in PRICE_FACTOR:
        rows = sales.currency == cur
        sales.loc[rows, "unit_price"] = menu_price(sales.loc[rows, "usd_price"], cur)
    sales = sales.sort_values(["branch_id", "sold_at", "sale_id"], ignore_index=True)
    sales["sold_on"] = sales.sold_at.dt.date
    return sales


# ─── Menu and costs ────────────────────────────────────────────────────

def build_menu(menu: pd.DataFrame, sales_src: pd.DataFrame):
    modal = sales_src.groupby("source_product_id").usd_price.agg(lambda s: s.mode().iloc[0])
    detail = pd.read_csv(RAW / "coffee_shop_sales.csv").groupby("product_id").product_detail.first()
    mapped = menu.dropna(subset=["source_product_id"]).astype({"source_product_id": int})
    product_map = pd.DataFrame({"source_product_id": mapped.source_product_id,
                                "source_product": mapped.source_product_id.map(detail),
                                "item_id": mapped.item_id})
    usd = mapped.set_index("item_id").source_product_id.map(modal)
    iced = menu.dropna(subset=["iced_of"]).set_index("item_id").iced_of
    usd = pd.concat([usd, iced.map(usd)])  # iced drinks cost the same as hot ones
    prices = pd.concat([
        pd.DataFrame({"item_id": usd.index, "currency": cur, "price": menu_price(usd.values, cur),
                      "valid_from": START})
        for cur in PRICE_FACTOR
    ])
    items = menu[["item_id", "name_en", "name_ar", "category"]].assign(is_active=True)
    return items, product_map, prices


def build_costs(ingredients: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ing in ingredients.itertuples():
        for cur, factor in PRICE_FACTOR.items():
            rows.append((ing.ingredient_id, cur, round(ing.base_cost_usd * factor, 4), START))
            if ing.ingredient_id == COST_INCREASE["ingredient"]:
                rows.append((ing.ingredient_id, cur, round(COST_INCREASE["new_cost_usd"] * factor, 4),
                             COST_INCREASE["valid_from"]))
    return pd.DataFrame(rows, columns=["ingredient_id", "currency", "unit_cost", "valid_from"])


# ─── Inventory and waste ───────────────────────────────────────────────

def simulate_inventory(sales, recipes, ingredients, lead_times, rng):
    use = (sales[["branch_id", "sold_on", "item_id", "qty"]].merge(recipes, on="item_id")
           .assign(used=lambda d: d.qty * d.qty_per_item)
           .groupby(["branch_id", "ingredient_id", "sold_on"]).used.sum())
    use_by = {k: g.droplevel([0, 1]) for k, g in use.groupby(level=[0, 1])}

    inventory, waste, facts = [], [], {"emergency_deliveries": 0}
    for branch in BRANCHES.branch_id:
        span = days(max(START, BRANCH.loc[branch, "opened_on"]), END)
        for ing in ingredients.itertuples():
            key = (branch, ing.ingredient_id)
            used = use_by.get(key, pd.Series(dtype=float)).reindex(span, fill_value=0.0).to_numpy()
            lead = int(lead_times[ing.supplier_id])
            cycle = ORDER_CYCLE.get(ing.ingredient_id, 14)
            safety = SAFETY_DAYS[ing.shelf_life_days <= PERISHABLE_SHELF_DAYS]
            whole = ing.unit == "unit"
            group = WASTE_GROUP.get(ing.ingredient_id, "dry")
            w_share, w_chance, w_reasons = WASTE_RULES[group]
            trap = key == (LOW_STOCK_TRAP["branch"], LOW_STOCK_TRAP["ingredient"])
            trap_day = None  # index of the short delivery

            first_avg = max(used[:28].mean(), 1e-6)
            closing = round((safety + cycle) * first_avg, 3)
            if whole:
                closing = max(math.ceil(closing), 2 * MIN_UNITS)
            arrivals: dict[int, float] = defaultdict(float)
            for i, d in enumerate(span):
                avg = max([first_avg] + [used[max(0, i - w):i].mean() for w in PLAN_WINDOWS if i])
                sigma = used[max(0, i - 28):i].std() if i > 1 else 0.0
                opening = closing
                delivered = round(arrivals.pop(i, 0.0), 3)
                u = round(used[i], 3)
                if trap and trap_day is None and d >= LOW_STOCK_TRAP["from"] and (
                        delivered or d == LOW_STOCK_TRAP["from"] + timedelta(6)):
                    j = span.index(LOW_STOCK_TRAP["low_on"])
                    target = LOW_STOCK_TRAP["low_cover"] * used[j - AVG_WINDOW + 1:j + 1].mean()
                    delivered = round(max(0.0, used[i:j + 1].sum() + target - opening), 3)
                    trap_day = i
                    arrivals.clear()  # the roaster's later deliveries don't come
                trap_active = trap_day is not None and i > trap_day

                wasted, events = 0.0, []
                if rng.random() < w_chance and u > 0:
                    qty = w_share * u / w_chance * rng.lognormal(0, 0.35)
                    reason = rng.choice(list(w_reasons), p=list(w_reasons.values()))
                    events.append((qty, str(reason)))
                spike = WASTE_SPIKE["shares"].get(ing.ingredient_id)
                if spike and branch == WASTE_SPIKE["branch"] and WASTE_SPIKE["start"] <= d <= WASTE_SPIKE["end"]:
                    events.append((spike * u * rng.uniform(0.85, 1.15), WASTE_SPIKE["reason"]))
                for qty, reason in events:
                    # whole units round up or down at random, so expected waste stays unbiased
                    qty = math.floor(qty) + (rng.random() < qty % 1) if whole else round(qty, 3)
                    qty = min(qty, max(opening + delivered - u - wasted, 0))
                    if qty > 0:
                        wasted = round(wasted + qty, 3)
                        waste.append((branch, ing.ingredient_id, d, qty, reason))

                closing = round(opening + delivered - u - wasted, 3)
                avg_14 = used[max(0, i - AVG_WINDOW + 1):i + 1].mean()  # as the tools compute it
                floor = LOW_STOCK_TRAP["floor_days"] * avg_14 if trap_active else 0.0
                if closing < floor:  # emergency or local top-up
                    top_up = round(floor - closing + (0 if trap_active else avg), 3)
                    if whole:
                        top_up = math.ceil(top_up)
                    delivered = round(delivered + top_up, 3)
                    closing = round(opening + delivered - u - wasted, 3)
                    facts["emergency_deliveries"] += not trap_active

                if trap_day is None:
                    position = closing + sum(arrivals.values())
                    reorder_point = (lead + safety) * avg + VARIABILITY_Z * sigma * math.sqrt(lead)
                    order_up_to = reorder_point + cycle * avg
                    if whole:
                        reorder_point = max(reorder_point, MIN_UNITS)
                        order_up_to = max(order_up_to, 2 * MIN_UNITS)
                    if position < reorder_point:
                        qty = order_up_to - position
                        arrivals[i + lead] += math.ceil(qty) if whole else round(qty, 3)

                inventory.append((branch, ing.ingredient_id, d, opening, delivered, u, wasted, closing))
            if trap:
                facts["low_stock_last_delivery"] = span[trap_day]

    inv = pd.DataFrame(inventory, columns=["branch_id", "ingredient_id", "stock_date", "opening_qty",
                                           "delivered_qty", "used_qty", "wasted_qty", "closing_qty"])
    waste_df = pd.DataFrame(waste, columns=["branch_id", "ingredient_id", "wasted_on", "qty", "reason"])
    waste_df.insert(0, "waste_id", np.arange(1, len(waste_df) + 1))
    return inv, waste_df, facts


# ─── Staff and shifts ──────────────────────────────────────────────────

def build_staff(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for branch, roles in ROSTER.items():
        country = BRANCH.loc[branch, "country"]
        first, last = NAMES[country]
        used: set[str] = set()
        n = 0
        for role, count in roles.items():
            for _ in range(count):
                while (name := f"{rng.choice(first)} {rng.choice(last)}") in used:
                    pass
                used.add(name)
                n += 1
                rows.append((f"{branch}-S{n:02d}", branch, name, role, WAGES[country][role],
                             BRANCH.loc[branch, "currency"]))
    return pd.DataFrame(rows, columns=["staff_id", "branch_id", "full_name", "job_role",
                                       "hourly_rate", "currency"])


def pick_staff(role: str, by_role: dict[str, list[str]], pointer: dict[str, int], working: set[str],
               crew: list[str]) -> str:
    """Next person in the role's rotation who isn't already working today; anyone free as a fallback."""
    pool = by_role.get(role, [])
    for _ in range(len(pool)):
        sid = pool[pointer[role] % len(pool)]
        pointer[role] += 1
        if sid not in working:
            return sid
    return next(s for s in crew if s not in working)


def build_shifts(sales: pd.DataFrame, staff: pd.DataFrame) -> pd.DataFrame:
    hourly = sales.groupby(["branch_id", "sold_on", sales.sold_at.dt.hour]).size()

    def need(branch, d, first_hour, last_hour, source_days=None):
        hours = [h % 24 for h in range(first_hour, last_hour)]
        if source_days is None:
            source_days = [d]
        total = sum(hourly.get((branch, sd, h), 0) for sd in source_days for h in hours)
        mean = total / (len(hours) * len(source_days))
        return max(MIN_STAFF, math.ceil(mean / ORDERS_PER_STAFF_HOUR))

    rows = []
    pre_ramadan = days(RAMADAN[0] - timedelta(14), RAMADAN[0] - timedelta(1))
    for branch in BRANCHES.branch_id:
        crew = staff[staff.branch_id == branch]
        by_role = {r: list(g.staff_id) for r, g in crew.groupby("job_role")}
        pointer = defaultdict(int)
        for d in days(max(START, BRANCH.loc[branch, "opened_on"]), END):
            ramadan = RAMADAN[0] <= d <= RAMADAN[1]
            plan = []  # (shift name, start hour, end hour, staff needed, overtime)
            if not ramadan:
                plan = [(n, a, b, need(branch, d, a, b), False) for n, a, b in SHIFTS_NORMAL]
            elif branch == UNADJUSTED_RAMADAN_BRANCH:
                # The normal schedule was kept as published; evenings were covered with overtime.
                plan = [(n, a, b, need(branch, d, a, b, pre_ramadan), False) for n, a, b in SHIFTS_NORMAL]
                n, a, b = SHIFTS_RAMADAN[1]
                plan.append((n, a, b, need(branch, d, a, b), True))
            else:
                plan = [(n, a, b, need(branch, d, a, b), False) for n, a, b in SHIFTS_RAMADAN]

            working: set[str] = set()
            for name, a, b, n_staff, overtime in plan:
                roles = ["supervisor"] + (["baker"] if name in ("morning", "day") else [])
                roles += ["cashier"] if n_staff >= 4 else []
                roles += ["barista"] * max(0, n_staff - len(roles))
                for role in roles[:max(n_staff, 2)]:
                    sid = pick_staff(role, by_role, pointer, working, list(crew.staff_id))
                    working.add(sid)
                    start = datetime.combine(d, time()) + timedelta(hours=a)
                    rows.append((sid, branch, d, start, start + timedelta(hours=b - a), overtime))

    shifts = pd.DataFrame(rows, columns=["staff_id", "branch_id", "shift_date", "starts_at", "ends_at",
                                         "overtime"])
    shifts.insert(0, "shift_id", np.arange(1, len(shifts) + 1))
    return shifts


# ─── Reviews, calendar, weather ────────────────────────────────────────

def build_reviews(rng: np.random.Generator) -> pd.DataFrame:
    src = pd.read_parquet(EXTERNAL / "ar_res_reviews.parquet")
    order = rng.permutation(len(src))
    rows, at = [], 0
    for branch, n in REVIEWS_PER_BRANCH.items():
        span = days(max(START, BRANCH.loc[branch, "opened_on"]), END)
        for idx in order[at:at + n]:
            text = " ".join(str(src.text.iloc[idx]).split())
            rows.append((branch, span[rng.integers(len(span))], text,
                         "positive" if src.polarity.iloc[idx] == 1 else "negative", int(idx)))
        at += n
    reviews = pd.DataFrame(rows, columns=["branch_id", "reviewed_on", "body_ar", "sentiment", "source_row"])
    reviews = reviews.sort_values(["branch_id", "reviewed_on", "source_row"], ignore_index=True)
    reviews.insert(0, "review_id", np.arange(1, len(reviews) + 1))
    return reviews


def build_calendar() -> pd.DataFrame:
    rows = []
    for country in ("SA", "EG"):
        names = holidays.country_holidays(country, years=START.year, language="en_US")
        for d in days(START, END):
            rows.append((d, country, d.weekday() in WEEKEND, RAMADAN[0] <= d <= RAMADAN[1], names.get(d)))
    return pd.DataFrame(rows, columns=["cal_date", "country", "is_weekend", "is_ramadan", "holiday_name"])


# ─── Planted-fact summary ──────────────────────────────────────────────

def summarise(sales, prices, recipes, costs, inv, waste, shifts, staff, facts) -> dict:
    revenue = sales.assign(rev=sales.qty * sales.unit_price)

    def labour(branch, first, last):
        s = shifts[(shifts.branch_id == branch) & (shifts.shift_date >= first) & (shifts.shift_date <= last)]
        if s.empty:
            return None
        rate = s.staff_id.map(staff.set_index("staff_id").hourly_rate)
        hours = (s.ends_at - s.starts_at).dt.total_seconds() / 3600
        cost = (hours * rate * np.where(s.overtime, OVERTIME_FACTOR, 1.0)).sum()
        rev = revenue[(revenue.branch_id == branch) & (revenue.sold_on >= first) & (revenue.sold_on <= last)].rev.sum()
        return round(cost / rev, 4) if rev else None

    def item_cost(item, cur, on):
        c = costs[(costs.currency == cur) & (costs.valid_from <= on)].sort_values("valid_from")
        c = c.groupby("ingredient_id").unit_cost.last()
        r = recipes[recipes.item_id == item]
        return round(float((r.qty_per_item * r.ingredient_id.map(c)).sum()), 4)

    item = "pistachio_maamoul"
    margin = {}
    for cur in PRICE_FACTOR:
        price = float(prices[(prices.item_id == item) & (prices.currency == cur)].price.iloc[0])
        before, after = item_cost(item, cur, date(2026, 3, 31)), item_cost(item, cur, END)
        margin[cur] = {"price": price, "cost_before": before, "cost_after": after,
                       "margin_before": round(price - before, 4), "margin_after": round(price - after, 4)}

    trap = LOW_STOCK_TRAP
    t = inv[(inv.branch_id == trap["branch"]) & (inv.ingredient_id == trap["ingredient"])].reset_index(drop=True)
    cover = t.closing_qty / t.used_qty.rolling(AVG_WINDOW, min_periods=1).mean()
    below = t.stock_date[cover < 2]

    spike = WASTE_SPIKE
    w = waste[(waste.branch_id == spike["branch"]) & (waste.wasted_on >= spike["start"])
              & (waste.wasted_on <= spike["end"]) & (waste.reason == spike["reason"])]
    return {
        "b2_waste_spike": {"window": [str(spike["start"]), str(spike["end"])],
                           "ingredients": sorted(spike["shares"]), "reason": spike["reason"],
                           "prep_error_entries_in_window": int(len(w))},
        "negative_margin": {"item_id": item, "ingredient": COST_INCREASE["ingredient"],
                            "cost_increase_from": str(COST_INCREASE["valid_from"]), "by_currency": margin},
        "b4_opened": {"opened_on": str(BRANCH.loc["B4", "opened_on"]),
                      "first_sale": str(sales[sales.branch_id == "B4"].sold_on.min())},
        "ramadan_labour_ratio": {b: labour(b, *RAMADAN) for b in ("B1", "B2", "B3")},
        "monthly_labour_ratio": {b: {m: labour(b, date(2026, m, 1),
                                               (date(2026, m + 1, 1) if m < 12 else date(2027, 1, 1)) - timedelta(1))
                                     for m in range(1, 7)} for b in BRANCHES.branch_id},
        "low_stock_trap": {"branch": trap["branch"], "ingredient": trap["ingredient"],
                           "last_delivery": str(facts["low_stock_last_delivery"]),
                           "first_day_below_2_days": str(below.iloc[0]) if len(below) else None,
                           "days_of_cover_on_last_day": round(float(cover.iloc[-1]), 2)},
        "emergency_deliveries": facts["emergency_deliveries"],
    }


def write(name: str, df: pd.DataFrame) -> None:
    df.to_csv(BUILD / f"{name}.csv", index=False, lineterminator="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    seed = parser.parse_args().seed
    BUILD.mkdir(exist_ok=True)

    menu = pd.read_csv(MAPPINGS / "menu_items.csv").astype({"source_product_id": "Int64"})
    ingredients = pd.read_csv(MAPPINGS / "ingredients.csv")
    suppliers = pd.read_csv(MAPPINGS / "suppliers.csv")
    recipes = pd.read_csv(MAPPINGS / "recipes.csv")
    usda = pd.read_csv(RAW / "usda_ingredients.csv").set_index("ingredient_id").fdc_id
    weather = pd.read_csv(EXTERNAL / "weather_daily.csv", parse_dates=["obs_date"])
    weather["obs_date"] = weather.obs_date.dt.date

    sales = load_sales(menu)
    items, product_map, prices = build_menu(menu, sales)
    sales = add_new_branch(sales, rng_for(seed, "b4"))
    sales = apply_ramadan(sales, rng_for(seed, "ramadan"))
    sales = apply_weather(sales, weather, menu, rng_for(seed, "weather"))
    sales = finish_sales(sales)

    ing = ingredients.assign(
        usda_fdc_id=ingredients.ingredient_id.map(usda).astype("Int64"),
        order_cycle_days=ingredients.ingredient_id.map(lambda i: ORDER_CYCLE.get(i, 14)),
        safety_days=(ingredients.shelf_life_days <= PERISHABLE_SHELF_DAYS).map(SAFETY_DAYS),
    )
    costs = build_costs(ingredients)
    lead_times = suppliers.set_index("supplier_id").lead_time_days
    inv, waste, facts = simulate_inventory(sales, recipes, ingredients, lead_times, rng_for(seed, "inventory"))
    staff = build_staff(rng_for(seed, "staff"))
    shifts = build_shifts(sales, staff)

    write("branches", BRANCHES)
    write("calendar", build_calendar())
    write("weather_daily", weather)
    write("menu_items", items)
    write("source_product_map", product_map)
    write("item_prices", prices)
    write("suppliers", suppliers)
    write("ingredients", ing[["ingredient_id", "name_en", "name_ar", "unit", "usda_fdc_id", "shelf_life_days",
                              "supplier_id", "order_cycle_days", "safety_days"]])
    write("ingredient_costs", costs)
    write("recipes", recipes)
    write("sales", sales[["sale_id", "branch_id", "sold_at", "item_id", "source_product_id", "qty",
                          "unit_price", "currency"]])
    write("inventory_daily", inv)
    write("waste_events", waste)
    write("staff", staff)
    write("shifts", shifts)
    write("reviews", build_reviews(rng_for(seed, "reviews")))

    summary = summarise(sales, prices, recipes, costs, inv, waste, shifts, staff, facts)
    (BUILD / "planted_facts.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"sales {len(sales):,} | inventory rows {len(inv):,} | waste entries {len(waste):,} | "
          f"shifts {len(shifts):,}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
