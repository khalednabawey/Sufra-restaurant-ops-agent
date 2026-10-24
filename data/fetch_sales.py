"""Fetch Maven Analytics' "Coffee Shop Sales" and save it as data/raw/coffee_shop_sales.csv.

The source ships as an .xlsx inside a zip. The CSV is committed so builds don't depend on
Maven's link staying up; this script records exactly how it was made.

    uv run python data/fetch_sales.py
"""

import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://maven-datasets.s3.amazonaws.com/Coffee+Shop+Sales/Coffee+Shop+Sales.zip"
ZIP_SHA256 = "be13c59c4cfdc0d1b08ceab514aea50a0cd329c916e4c3585eeef92a5b4c3a2c"
RAW = Path(__file__).parent / "raw"
ZIP_PATH = RAW / "coffee-shop-sales.zip"
CSV_PATH = RAW / "coffee_shop_sales.csv"


def main() -> None:
    RAW.mkdir(exist_ok=True)
    if not ZIP_PATH.exists():
        urllib.request.urlretrieve(URL, ZIP_PATH)
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    if digest != ZIP_SHA256:
        raise SystemExit(f"Unexpected file: sha256 {digest}, expected {ZIP_SHA256}")

    with zipfile.ZipFile(ZIP_PATH) as z:
        xlsx = z.read("Coffee Shop Sales.xlsx")
    df = pd.read_excel(io.BytesIO(xlsx))
    df.to_csv(CSV_PATH, index=False, lineterminator="\n")
    print(f"{CSV_PATH.name}: {len(df):,} rows; columns: {', '.join(df.columns)}")


if __name__ == "__main__":
    main()
