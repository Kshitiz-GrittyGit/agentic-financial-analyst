import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SUBMISSIONS_CACHE = Path(__file__).parent / "data" / "cache_submission"


def _sec_headers():
    agent = os.environ.get("SEC_USER_AGENT")
    if not agent:
        raise RuntimeError(
            "Set SEC_USER_AGENT='Your Name your@email.com' in .env — SEC requires a contact"
        )
    return {"User-Agent": agent}


def get_filing_metadata(cik, form="10-K", year=None, refresh=False):
    """Return metadata for one filing: form + fiscal year (by period end)."""

    if not (isinstance(cik, str) and cik.isdigit() and len(cik) == 10):
        raise ValueError(f"CIK must be a 10-digit zero-padded string, got: {cik!r}")
    if year is None:
        raise ValueError("year is required, e.g. year=2024")

    SUBMISSIONS_CACHE.mkdir(parents=True, exist_ok=True)
    file_path = SUBMISSIONS_CACHE / f"{cik}.json"

    if refresh or not file_path.exists():
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        response = requests.get(url, headers=_sec_headers(), timeout=30)
        response.raise_for_status()
        with open(file_path, "w") as f:
            json.dump(response.json(), f)

    with open(file_path, "r") as f:
        data = json.load(f)

    recent = data["filings"]["recent"]

    matches = []
    for i in range(len(recent["form"])):
        if recent["form"][i] != form:
            continue
        report_date = recent["reportDate"][i]
        if not report_date or report_date[:4] != str(year):
            continue
        matches.append({
            "accession_number": recent["accessionNumber"][i],
            "form": recent["form"][i],
            "filing_date": recent["filingDate"][i],
            "report_date": report_date,
            "primary_document": recent["primaryDocument"][i],
        })

    if not matches:
        available = sorted({
            recent["reportDate"][i][:4]
            for i in range(len(recent["form"]))
            if recent["form"][i] == form and recent["reportDate"][i]
        }, reverse=True)
        raise ValueError(
            f"No {form} with period ending in {year} for CIK {cik}. "
            f"Available years: {available}"
        )

    best = max(matches, key=lambda m: m["filing_date"])

    accn_plain = best["accession_number"].replace("-", "")
    cik_plain = str(int(cik))          # Archives path uses CIK without leading zeros

    return {
        "cik": cik,
        "company_name": data.get("name", ""),
        "ticker": (data.get("tickers") or [""])[0],
        "accession_number": best["accession_number"],
        "form": best["form"],
        "filing_date": best["filing_date"],
        "report_date": best["report_date"],
        "fiscal_year": year,
        "primary_document": best["primary_document"],
        "document_url": (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_plain}/{accn_plain}/{best['primary_document']}"
        ),
    }
FILINGS_DIR = Path(__file__).parent / "documents"
REGISTRY_PATH = Path(__file__).parent / "sla_registry.json"


def _filing_filename(meta):
    """Single source of truth for the PDF filename."""
    return f"{meta['ticker']}_{meta['form']}_{meta['report_date']}_{meta['cik']}.pdf"


def download_filing_pdf(meta, refresh=False):
    """Fetch the filing HTML from EDGAR and render it to PDF."""
    from weasyprint import HTML

    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FILINGS_DIR / _filing_filename(meta)

    if pdf_path.exists() and not refresh:
        return pdf_path

    response = requests.get(meta["document_url"], headers=_sec_headers(), timeout=60)
    response.raise_for_status()

    # base_url lets weasyprint resolve the filing's relative image/CSS references
    HTML(string=response.text, base_url=meta["document_url"]).write_pdf(str(pdf_path))
    return pdf_path


def update_sla_registry(meta):
    """Add or update this filing's entry. Key and filename come from the same source."""
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)
    else:
        registry = {"_schema_version": "1.0", "documents": {}}

    key = f"{meta['cik']}::{meta['form']}::{meta['report_date']}"
    registry["documents"][key] = {
        "filename":         _filing_filename(meta),
        "company_name":     meta["company_name"],
        "ticker":           meta["ticker"],
        "cik":              meta["cik"],
        "document_type":    meta["form"],
        "fiscal_year":      meta["fiscal_year"],
        "fiscal_quarter":   None,
        "filing_date":      meta["filing_date"],
        "period_of_report": meta["report_date"],
        "exchange":         (
            (json.load(open(SUBMISSIONS_CACHE / f"{meta['cik']}.json")).get("exchanges") or [""])[0]
        ),
        "sic_code":         "",
    }

    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)
    return key


def ingest_filing(cik, form, year, refresh=False):
    meta = get_filing_metadata(cik, form, year, refresh=refresh)
    pdf_path = download_filing_pdf(meta, refresh=refresh)
    update_sla_registry(meta)
    return meta, pdf_path


if __name__ == "__main__":
    for cik in ["0000320193", "0000789019"]:
        for yr in [2023, 2024]:
            meta, path = ingest_filing(cik, "10-K", yr)
            print(f"{meta['ticker']} FY{yr} -> {path.name} ({path.stat().st_size:,} bytes)")

