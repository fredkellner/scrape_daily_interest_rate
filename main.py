import os
import re
import time
import logging
import requests

from bs4 import BeautifulSoup
from flask import Flask
import pandas as pd

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bucket_name = os.getenv("BUCKET_NAME")

MONTH_MAP = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}

URL = "https://www.bankrate.com/mortgages/mortgage-rates/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_page(url: str, retries: int = 3, backoff: float = 5.0) -> str:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("Attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"All {retries} attempts to fetch {url} failed.")


def parse_date(soup: BeautifulSoup, page_text: str) -> str:
    """Return YYYY-MM-DD. Tries several strategies in order."""

    # Strategy 1: look for "Rates as of <Weekday>, <Month> <D>, <YYYY>" pattern
    match = re.search(
        r"Rates as of\s+\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})",
        page_text,
        re.IGNORECASE,
    )
    if match:
        month_name, day, year = match.group(1), match.group(2), match.group(3)
        if month_name in MONTH_MAP:
            log.info("Date found via 'Rates as of' pattern.")
            return f"{year}-{MONTH_MAP[month_name]}-{day.zfill(2)}"

    # Strategy 2: legacy data-sheets-root span (kept in case Bankrate reverts)
    span = soup.find("span", attrs={"data-sheets-root": "1"})
    if span:
        date_text = span.text.strip()
        date_str = date_text[date_text.find(",") + 2:]
        month, day_year = date_str.split(" ", 1)
        day = day_year.split(",")[0].zfill(2)
        year = day_year.split(",")[1].strip()
        if month in MONTH_MAP:
            log.info("Date found via legacy data-sheets-root span.")
            return f"{year}-{MONTH_MAP[month]}-{day}"

    # Strategy 3: any element whose text matches a date-like pattern
    for tag in soup.find_all(string=re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b")):
        m = re.search(r"(\w+)\s+(\d{1,2}),\s+(\d{4})", tag)
        if m:
            month_name, day, year = m.group(1), m.group(2), m.group(3)
            if month_name in MONTH_MAP:
                log.info("Date found via generic month-name search.")
                return f"{year}-{MONTH_MAP[month_name]}-{day.zfill(2)}"

    raise ValueError("Could not extract date from page.")


def parse_rate(soup: BeautifulSoup, page_text: str) -> str:
    """Return the 30-year fixed APR string. Tries several strategies in order."""

    # Strategy 1: find a table row whose first cell contains "30-Year Fixed"
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if cells and re.search(r"30.?year fixed", cells[0].get_text(), re.IGNORECASE):
            # APR is typically the last numeric column; interest rate is second-to-last
            rate_cells = [
                c.get_text(strip=True) for c in cells[1:]
                if re.search(r"\d+\.\d+%?", c.get_text())
            ]
            if rate_cells:
                # Prefer the APR (last value) if multiple numeric cells exist
                rate = rate_cells[-1].rstrip("%")
                log.info("Rate found via table row strategy.")
                return rate

    # Strategy 2: legacy text sibling of data-sheets-root span
    span = soup.find("span", attrs={"data-sheets-root": "1"})
    if span and span.next_sibling:
        after_text = span.next_sibling
        if isinstance(after_text, str):
            marker = "the national average 30-year fixed mortgage APR is"
            start = after_text.find(marker)
            if start != -1:
                end = after_text.find("%", start + len(marker))
                rate = after_text[start + len(marker): end].strip()
                log.info("Rate found via legacy span sibling strategy.")
                return rate

    # Strategy 3: regex over full page text
    match = re.search(
        r"30.?year fixed[^\d]{0,60}(\d+\.\d+)%",
        page_text,
        re.IGNORECASE,
    )
    if match:
        log.info("Rate found via regex over page text.")
        return match.group(1)

    # Strategy 4: look for the "national average" sentence anywhere on page
    match = re.search(
        r"national average 30.year fixed mortgage APR is\s+(\d+\.\d+)%",
        page_text,
        re.IGNORECASE,
    )
    if match:
        log.info("Rate found via 'national average' sentence.")
        return match.group(1)

    raise ValueError("Could not extract 30-year fixed rate from page.")


@app.route("/")
def handle_request():
    try:
        log.info("Fetching %s", URL)
        html = fetch_page(URL)
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        formatted_date = parse_date(soup, page_text)
        mortgage_rate = parse_rate(soup, page_text)

        log.info("Date: %s  Rate: %s%%", formatted_date, mortgage_rate)

        mortgage_rate_df = pd.DataFrame({"date": [formatted_date], "rate": [mortgage_rate]})
        existing_rates_df = pd.read_csv(f"gs://{bucket_name}/mortgage_rates.csv")
        final_df = pd.concat([existing_rates_df, mortgage_rate_df])
        final_df.drop_duplicates(["date"], inplace=True)
        final_df.to_csv(
            f"gs://{bucket_name}/mortgage_rates.csv",
            index=False,
            storage_options={"token": None},
        )
        log.info("CSV updated successfully.")
        return {"status": "ok", "date": formatted_date, "rate": mortgage_rate}, 200

    except Exception as exc:
        log.exception("Scrape failed: %s", exc)
        return {"status": "error", "message": str(exc)}, 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
