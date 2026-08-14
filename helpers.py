import json
from functools import lru_cache
import requests
from pathlib import Path
import time
import re
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()


file_path = Path(__file__).parent / "sec_ticker.json"


@lru_cache(maxsize=1)
def _load_ticker_map(filepath: Path) -> dict[str, str]:
    if not filepath.exists():
        agent = os.environ.get("SEC_USER_AGENT")
        if not agent:
            raise RuntimeError(
                "SEC_USER_AGENT='Your Name your@email.com' in .env — SEC requires a contact"
            )
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": agent},
            timeout=30,
        )
        r.raise_for_status()
        with open(filepath, "w") as f:
            json.dump(r.json(), f)

    with open(filepath, "r") as f:
        raw_data = json.load(f)

    return {
        item["ticker"].upper(): f"{item['cik_str']:010d}"
        for item in raw_data.values()
    }


def get_cik(ticker):
    ticker_to_cik = _load_ticker_map(file_path)
    
    try:
        return ticker_to_cik[ticker.upper()]
    except KeyError:
        raise ValueError(f"Unknown ticker: {ticker}")


def get_company_facts(cik, refresh = False):

    if len(cik)!= 10:
        raise ValueError(f"Wrong CIK: {cik}")

    cache_dir = Path(__file__).parent / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    file_path = cache_dir / f"{cik}.json"
    
    if refresh == True or not file_path.exists():

        sec_url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'

        SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT")
        
        if not SEC_USER_AGENT:
            raise RuntimeError(
        "SEC_USER_AGENT='Your Name your@email.com' in .env — SEC requires a contact"
    )

        headers = {"User-Agent": SEC_USER_AGENT}

        response = requests.get(sec_url, headers=headers)
        response.raise_for_status()
        
        new_data = response.json()
        
        with open(file_path, 'w') as data_file:
            json.dump(new_data, data_file,indent=1)
        return new_data
    
    else:
        # We need to fetch the data from the cache directory
    
        with open(file_path, 'r') as data_file:
            return json.load(data_file)
        

# 1. Custom exception expected by assertions
class PeriodError(Exception):
    """Raised when a period string cannot be parsed or is out of range."""
    pass


def normalize_period(period: str) -> int:
    period = period.strip().lower()
    matching_pattern = r'(?:fy|fiscal\s*year|fiscal)?\s*(\d{4}|\d{2})'
    
    normalized_data = re.fullmatch(matching_pattern, period)
    
    # 2. Guard against NoneType before calling .group()
    if normalized_data is None:
        raise PeriodError(f"Unrecognized format: '{period}'")
    
    year_str = normalized_data.group(1)
    year = int(year_str)
    
    # 3. Convert 2-digit years to 4-digit years
    if len(year_str) == 2:
        year += 2000
    
    # 4. Validate range (2000 to 2030)
    if 2000 <= year <= 2030:
        return year
    else:
        raise PeriodError(f"Year out of valid range (2000-2030): {year}")


ALIASES = {
    "rd": ["ResearchAndDevelopmentExpense",
           "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "r&d": ["ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "research and development": ["ResearchAndDevelopmentExpense",
                                 "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "total revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                      "Revenues", "SalesRevenueNet"],
    "net sales": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                  "Revenues", "SalesRevenueNet"],
}

def date_diff(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

# 2. Subtracting gives a timedelta object; access .days for the integer
    days_difference = (end- start).days
    return days_difference

def get_financial_fact(company, metric, period):
    year = normalize_period(period)

    key = metric.strip().lower()
    if key not in ALIASES:
        raise ValueError(
            f"Unsupported metric: '{metric}'. Supported: {sorted(set(ALIASES))}"
        )

    cik = get_cik(company)
    facts = get_company_facts(cik)
    us_gaap = facts["facts"]["us-gaap"]

    filings, matched_tag = [], None
    for tag in ALIASES[key]:
        try:
            records = us_gaap[tag]["units"]["USD"]
        except KeyError:
            continue

        for record in records:
            if "start" not in record:
                continue
            if datetime.strptime(record["end"], "%Y-%m-%d").year != year:
                continue
            if not (350 <= date_diff(record["start"], record["end"]) <= 380):
                continue
            filings.append(record)

        if filings:
            matched_tag = tag
            break

    if not filings:
        raise ValueError(
            f"No annual {metric} found for {company.upper()} FY{year}. "
            f"Tags tried: {ALIASES[key]}"
        )

    best = max(filings, key=lambda r: r["filed"])

    return {
        "company": company.upper(),
        "metric": key,
        "tag": matched_tag,
        "value": best["val"],
        "unit": "USD",
        "fiscal_year": year,
        "start": best["start"],
        "end": best["end"],
        "form": best["form"],
        "accn": best["accn"],
        "filed": best["filed"],
        "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    }
    
if __name__ == "__main__":
    for co in ["AAPL", "MSFT"]:
        for m in ["revenue", "rd"]:
            f = get_financial_fact(co, m, "FY2024")
            print(f["company"], f["metric"], f["value"], f["end"], f["tag"])

