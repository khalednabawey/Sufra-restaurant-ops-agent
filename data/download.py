"""Download the data that isn't committed: daily weather and Arabic restaurant reviews.

- Weather: daily max temperature for each branch city from the Open-Meteo historical API
  (data CC BY 4.0, free for non-commercial use).
- Reviews: hadyelsahar/ar_res_reviews from Hugging Face (ElSahar & El-Beltagy, 2015).

Files go to data/external/ (gitignored). Existing files are kept unless --force is given.

    uv run python data/download.py [--force]
"""

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

EXTERNAL = Path(__file__).parent / "external"
WEATHER_PATH = EXTERNAL / "weather_daily.csv"
REVIEWS_PATH = EXTERNAL / "ar_res_reviews.parquet"

START, END = "2026-01-01", "2026-06-30"
CITIES = {  # branch neighbourhoods: Olaya, Tahlia, Smouha
    "Riyadh": (24.69, 46.68, "Asia/Riyadh"),
    "Jeddah": (21.55, 39.16, "Asia/Riyadh"),
    "Alexandria": (31.21, 29.95, "Africa/Cairo"),
}
REVIEWS_URL = (
    "https://huggingface.co/api/datasets/hadyelsahar/ar_res_reviews/parquet/default/train/0.parquet"
)


def download_weather() -> None:
    rows = []
    for city, (lat, lon, tz) in CITIES.items():
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "start_date": START, "end_date": END,
            "daily": "temperature_2m_max", "timezone": tz,
        })
        with urllib.request.urlopen(f"https://archive-api.open-meteo.com/v1/archive?{query}",
                                    timeout=60) as resp:
            daily = json.load(resp)["daily"]
        if None in daily["temperature_2m_max"]:
            raise SystemExit(f"Open-Meteo returned gaps for {city}")
        rows += [(city, day, temp) for day, temp in zip(daily["time"], daily["temperature_2m_max"], strict=True)]

    with WEATHER_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["city", "obs_date", "temp_max_c"])
        writer.writerows(rows)
    print(f"{WEATHER_PATH.name}: {len(rows)} city-days")


def download_reviews() -> None:
    urllib.request.urlretrieve(REVIEWS_URL, REVIEWS_PATH)
    print(f"{REVIEWS_PATH.name}: {REVIEWS_PATH.stat().st_size:,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="download again even if files exist")
    args = parser.parse_args()

    EXTERNAL.mkdir(exist_ok=True)
    for path, download in ((WEATHER_PATH, download_weather), (REVIEWS_PATH, download_reviews)):
        if path.exists() and not args.force:
            print(f"{path.name}: already downloaded")
        else:
            download()


if __name__ == "__main__":
    main()
