# Data

Sufra Café & Bakery (سُفرة) is a fictional four-branch chain built on public data. This file lists every source, its licence, and every rule used to derive what has no open source. If a number in the app doesn't come from a source below, it comes from a rule written here. All the code is in `build.py`, and `--seed 42` makes every number reproducible.

```text
raw/ (committed)        mappings/ (hand-written)      external/ (downloaded, not committed)
  coffee_shop_sales.csv   menu_items.csv                weather_daily.csv
  usda_ingredients.csv    ingredients.csv               ar_res_reviews.parquet
                          recipes.csv, suppliers.csv
            └─────────────── build.py --seed 42 ───────────────┘
                                    │
                    build/*.csv + planted_facts.json  ──  seed.py ──► PostgreSQL
                                                         checks.sql confirms the planted facts
```

## Sources

| Data | Source | Licence | In the repo? |
|---|---|---|---|
| Sales transactions | [Maven Analytics – Coffee Shop Sales](https://mavenanalytics.io/data-playground/coffee-shop-sales) | Public Domain | Yes: `raw/coffee_shop_sales.csv`, made by `fetch_sales.py` |
| Ingredient names and units | [USDA FoodData Central](https://fdc.nal.usda.gov/), SR Legacy (April 2018) | CC0 | Yes: `raw/usda_ingredients.csv`, made by `fetch_usda.py` |
| Daily max temperature | [Open-Meteo historical API](https://open-meteo.com/en/licence) | CC BY 4.0 (free API for non-commercial use) | No: fetched by `download.py` |
| Public holidays | Python [`holidays`](https://pypi.org/project/holidays/) package (KSA, Egypt); Ramadan dates by hand | MIT | Generated at build time |
| Arabic restaurant reviews | [`hadyelsahar/ar_res_reviews`](https://huggingface.co/datasets/hadyelsahar/ar_res_reviews) (ElSahar & El-Beltagy, 2015) | Unknown; released for scientific use | No: fetched by `download.py`, never committed |
| Food-safety extracts | [FDA Food Code 2022](https://www.fda.gov/food/fda-food-code/food-code-2022) | FDA website content is in the public domain ([FDA website policies](https://www.fda.gov/about-fda/about-website/website-policies)) | Short verbatim extracts in `sop/fda/`, each with its section number |
| Company SOPs | Written for this project, in Arabic and English | This repo's licence | Yes: `sop/en/`, `sop/ar/` |

## The sales file

`fetch_sales.py` downloads Maven's zip, checks its SHA-256, and converts the `.xlsx` inside to CSV without changing any values. Checked on 24 October 2026:

| Fact | Value |
|---|---|
| Rows | 149,116, no nulls, no duplicate `transaction_id` |
| Columns | `transaction_id, transaction_date, transaction_time, transaction_qty, store_id, store_location, product_id, unit_price, product_category, product_type, product_detail` |
| Dates | 1 Jan – 30 Jun 2023 (181 days); every store trades every day |
| Hours | 06:00 – 20:59, busiest 07:00–10:59 |
| Stores | 3 Astoria (50,599 rows), 5 Lower Manhattan (47,782), 8 Hell's Kitchen (50,735) |
| Products | 80, in 9 categories |
| Weekly pattern | Flat: 811–835 rows per weekday on average (±1.5%) |
| Trend | Rows per month roughly double from January (17,314) to June (35,352) |

Two things the file does differently from what's often described:
- **One line per transaction.** Each `transaction_id` has exactly one product line, so an order is a row and the average ticket is revenue ÷ rows.
- **Prices aren't fixed per product.** 15 of the 80 products have 2–4 different prices, mixed within one store over the same dates. Each sale keeps its own price; the menu price list uses each product's most common price.

## Derivation rules

### Sales

1. **Dates.** Every date moves forward exactly three years: 1 Jan – 30 Jun 2023 becomes 1 Jan – 30 Jun 2026, one to one (neither year is a leap year). Weekdays aren't kept, and they don't need to be: the source has no weekly pattern to keep. That also means there's no weekend effect to remap. The calendar marks Friday and Saturday as the weekend in both countries.
2. **Branches.** B1 Olaya, Riyadh ← store 8 Hell's Kitchen (highest revenue). B2 Tahlia, Jeddah ← store 5 Lower Manhattan. B3 Smouha, Alexandria ← store 3 Astoria. The three stores are within 3% of each other, so most differences between branches come from the rules below, and from B3's currency.
3. **B4 Al Malqa (new branch).** Opened 1 April 2026. Built from B1's lines from that date: each line is kept with probability 0.60, its time moved by up to ±10 minutes (staying within 06:00–20:59), and given a new id from 1,000,001. Result: 19,271 lines.
4. **Menu.** Each of the 80 products maps to one menu item with an Arabic and English name, sizes kept separate (`mappings/menu_items.csv`). Examples: Gourmet brewed coffee → قهوة عربية (Arabic coffee), Traditional Blend Chai → شاي كرك (karak), Oatmeal Scone → معمول بالفستق (pistachio ma'amoul), and Brazilian brewed coffee → قهوة V60. Every sale keeps its `source_product_id`.
5. **Prices.** SAR = source USD × 3.75, rounded to the nearest 0.5. EGP = source USD × 25, rounded to the nearest 5. The EGP factor is a price-level factor that gives Alexandria café prices; it isn't the exchange rate. To compare branches across currencies, the tools use a fixed 13 EGP = 1 SAR.
6. **Ramadan (18 Feb – 19 Mar 2026).** The only deliberate change to customer behaviour; the source has no Ramadan. On each Ramadan day, 80% of the lines move into the evening, from 30 minutes after iftar until 23:59 (iftar: Riyadh 17:50, Jeddah 18:25, Alexandria 17:45). The rest move into 08:00–16:00. Times are mapped linearly, so the order of lines within a day is kept, and daily totals don't change. Result: about 80% of Ramadan sales fall after 18:00.
7. **Weather.** Eight hot drinks also come iced (V60 in three sizes, two lattes, Spanish latte, two karkadeh). Normally 20% of those lines are served iced. On days at or above 40°C (Open-Meteo daily max for the branch city) that becomes 30%, and below 25°C it becomes 10%. An iced drink has the same price and recipe, with a cold cup instead of a hot one. Riyadh has 46 days at or above 40°C in the period, Jeddah 6 and Alexandria none.
8. **Holidays.** Taken from the `holidays` package (English names). No demand change is applied, because the source has none.

### Ingredients, costs and recipes

9. **Recipes.** `mappings/recipes.csv` (233 lines) gives a bill of materials per item. Sizes scale at Small 0.8, Regular 1.0 and Large 1.25. Croissants are baked from bought-in frozen dough (counted in units). Ma'amoul, ka'ak, ghraybeh, cookies, basbousa and manousheh are baked in-house from flour, butter, sugar and so on. Retail goods (beans, tea, merchandise) are bought-in units.
10. **USDA names and units.** 30 of the 34 kitchen ingredients match an SR Legacy entry (`raw/usda_ingredients.csv`). SR Legacy has no roasted-bean entry, so the three coffees use the closest brewed-coffee entries. Cups, hazelnut syrup, sugar-free vanilla syrup and the retail goods have no USDA entry.
11. **Costs.** Each ingredient has a base cost in US dollars per g, ml or unit (`mappings/ingredients.csv`), converted with the same factors as the prices. So a margin in percent is the same in both currencies. A retail good costs 55% of its most common source price.
12. **Ingredient use** = quantity sold × recipe quantity, per branch and day. It follows directly from the sales.

### Inventory and waste

13. **Daily stock.** Opening + delivered − used − wasted = closing, for every branch, ingredient and day. The database enforces this with a CHECK. Opening stock on the first day covers the safety and order-cycle days.
14. **Reordering.** The reorder SOP sets the minimum: reorder point = (supplier lead time + safety days) × average daily use, with 3 safety days for perishables (shelf life up to 7 days) and 4 for everything else. The simulated branches are a little more cautious, because retail goods sell in bursts:
    - they plan on the larger of the 14- and 28-day averages;
    - they add 2 × the daily standard deviation × √(lead time) to the reorder point;
    - goods counted in units keep at least 3 at the reorder point.

    An order brings stock on hand plus stock on order up to the reorder point plus one order cycle, and it arrives after the supplier's lead time. Order cycles are 3 days for fresh milk, 7 for frozen croissant dough and coffee beans, and 14 for everything else.
15. **Emergency deliveries.** If stock would go below zero, a same-day emergency delivery covers the gap plus one day's use. This happened 60 times in the build, mostly for retail goods after a one-off large purchase.
16. **Waste.** Each day, each ingredient may get a waste entry. The size is drawn so that expected waste equals the rate below, with log-normal noise; units round up or down at random so the expectation stays unbiased.

    | Group | Share of use wasted | Chance of an entry per day | Reasons (weight) |
    |---|---|---|---|
    | Fresh milk | 3.0% | 60% | expired 0.65, quality 0.20, prep_error 0.10, damaged 0.05 |
    | Croissant dough, butter, eggs | 2.0% | 30% | prep_error 0.40, expired 0.30, quality 0.30 |
    | Everything else | 0.3% | 10% | damaged 0.50, expired 0.40, quality 0.10 |

    **Waste share** = waste cost ÷ cost of stock used (used + wasted). This is the measure in the waste SOP. Deliveries arrive in lumps, so measuring waste against a week's purchases would swing for no reason.

### Staff and shifts

17. **Staff.** 43 people (B1 13, B2 11, B3 11, B4 8): supervisors, bakers, cashiers and baristas, with generated names. Hourly rates are, in SAR: supervisor 28, baker 20, barista 17, cashier 15. In EGP: 95, 70, 60, 55.
18. **Shifts** follow the scheduling SOP:
    - Each shift is 8 hours. Normal shifts are 06:00–14:00 and 13:00–21:00; Ramadan shifts are 08:00–16:00 and 17:00–01:00.
    - Staff per shift = the larger of 2 and the average hourly orders in that shift ÷ 15, rounded up.
    - Every shift has a supervisor. Morning and day shifts have a baker, and shifts of 4 or more have a cashier.
    - People are assigned by rotation within their role.

    Overtime is paid at 1.5×. Labour cost = hours × hourly rate (× 1.5 for overtime).

### Reviews

19. **Reviews.** 860 reviews are drawn at random from the corpus (B1 260, B2 240, B3 250, B4 110) and given random dates while each branch was open. Their sentiment label comes from the corpus. They are restaurant reviews, not café reviews, so the app labels them "illustrative reviews from a public research corpus".

## Planted facts

These sit on top of the real sales. `checks.sql` confirms each one after seeding, and `build/planted_facts.json` records the values.

| Fact | How it's made | What the data shows |
|---|---|---|
| B2 Tahlia waste spike | 10–23 May 2026: daily `prep_error` waste of 35% of milk use and 50% of croissant-dough use | B2's May waste is 2.90% of stock used. The next-highest branch-month is 0.91%, and the week of 11 May is above the SOP's 5% weekly alert |
| One item with a negative margin | Pistachio cost rises from $0.028/g to $0.068/g on 1 April 2026; pistachio ma'amoul uses 45 g | Margin goes from SAR 6.26 to −0.49 (EGP 40.07 to −4.93); no other item is below cost |
| B4 opened 1 April 2026 | Sales exist only from 1 April | Any full-period comparison has to say B4 is partial; `compare_branches` flags it |
| Ramadan labour above 30% at one branch | B1 Olaya kept its normal schedule in Ramadan and covered evenings with overtime night shifts; the other branches switched to the Ramadan shifts | Ramadan labour ratio: B1 32.8%, B2 19.5%, B3 9.9%. Outside Ramadan, no branch-month goes above 24.4% |
| Low-stock trap | The espresso-bean delivery to B1 on 19 June is the last before a supplier delay, and arrives short; small local top-ups follow | B1's espresso beans drop below 2 days of cover on 26 June and are at 1.20 days on 30 June, the only B1 item below 2 days |

## Attribution

```text
Data sources: Sales transactions adapted from Maven Analytics "Coffee Shop Sales" (Public Domain).
Ingredient names and units from USDA FoodData Central (CC0). Weather data by Open-Meteo.com (CC BY 4.0).
Arabic reviews from ElSahar & El-Beltagy (2015), "Building Large Arabic Multi-domain Resources for Sentiment
Analysis", loaded from Hugging Face. Food-safety extracts from the FDA Food Code 2022. Inventory, waste and
staffing are derived from the sales data using the rules in data/README.md.
```
