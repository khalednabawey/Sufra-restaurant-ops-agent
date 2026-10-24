"""Look up each ingredient in USDA FoodData Central (SR Legacy) and save a small extract.

Matches the exact `usda_description` in mappings/ingredients.csv against the SR Legacy food
table and writes raw/usda_ingredients.csv (committed). Ingredients without a description
(cups, two syrups, retail goods) have no USDA entry.

    uv run python data/fetch_usda.py
"""

import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"
ZIP_SHA256 = "b80817294b8850530aaedf2e515c02593b1824f763a0ff356e5c2081643e6fd0"
DATA = Path(__file__).parent
ZIP_PATH = DATA / "external" / "usda" / "sr_legacy.zip"
INNER = "FoodData_Central_sr_legacy_food_csv_2018-04"
OUT = DATA / "raw" / "usda_ingredients.csv"


def main() -> None:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ZIP_PATH.exists():
        urllib.request.urlretrieve(URL, ZIP_PATH)
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    if digest != ZIP_SHA256:
        raise SystemExit(f"Unexpected file: sha256 {digest}, expected {ZIP_SHA256}")

    with zipfile.ZipFile(ZIP_PATH) as z:
        food = pd.read_csv(io.BytesIO(z.read(f"{INNER}/food.csv")))
        categories = pd.read_csv(io.BytesIO(z.read(f"{INNER}/food_category.csv")))
    food = food.merge(categories, left_on="food_category_id", right_on="id", suffixes=("", "_category"))

    ingredients = pd.read_csv(DATA / "mappings" / "ingredients.csv").dropna(subset=["usda_description"])
    extract = ingredients[["ingredient_id", "usda_description"]].merge(
        food, left_on="usda_description", right_on="description", how="left"
    )
    missing = extract[extract.fdc_id.isna()].usda_description.tolist()
    if missing:
        raise SystemExit(f"Not found in SR Legacy: {missing}")

    extract = extract.rename(columns={"description_category": "food_category"})
    extract["fdc_id"] = extract.fdc_id.astype(int)
    extract[["ingredient_id", "fdc_id", "description", "food_category", "data_type"]].to_csv(
        OUT, index=False, lineterminator="\n"
    )
    print(f"{OUT.name}: {len(extract)} ingredients matched")


if __name__ == "__main__":
    main()
