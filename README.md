# Agentic Financial Analyst

An agent that answers multi-step financial questions over public SEC EDGAR filings, with every number traceable to a source filing.

Built without an agent framework. The planning pass, the ReAct loop, tool dispatch, retry, and error classification are all hand-written against the raw Anthropic SDK — the goal was to understand each mechanism before reaching for an abstraction.

---

## Example

**Query:** *Compare Apple's and Microsoft's R&D as a percentage of revenue for FY2023 and FY2024. Which company's ratio grew faster?*

The agent plans, issues 8 `get_financial_fact` calls against EDGAR, runs 6 `calculator` steps, and returns:

| | FY2023 | FY2024 | Change |
|---|---|---|---|
| Apple | 7.80% | 8.02% | +0.22 pp |
| Microsoft | 12.83% | 12.04% | −0.79 pp |

Every figure resolves to a specific accession number, form type, and reporting period.

---

## Architecture

```
question
   ↓
make_plan            text plan, no tools bound
   ↓
ReAct loop           model selects, harness acts
   ↓
   ├── get_financial_fact   XBRL structured facts (built)
   ├── search_filings       RAG over narrative sections (stub)
   └── calculator           deterministic arithmetic (built)
   ↓
answer with lineage
```

Tool results are returned as JSON carrying full provenance — value, unit, period start and end, form type, accession number, filing date, and source URL — not just a number.

---

## The problem this project is actually about

SEC's `companyfacts` API returns every XBRL fact a company has ever tagged. Retrieving a specific number correctly is harder than it looks.

Apple's `ResearchAndDevelopmentExpense` under USD contains **234 records across only 74 distinct period-end dates** — roughly 3.2 records per period. Two causes:

1. **Quarterly, year-to-date, and annual figures share end dates.** A quarter ending 2024-06-29 and a nine-month period ending 2024-06-29 differ only in their `start`.
2. **Every 10-K restates prior years as comparatives.** FY2024 revenue appears in the FY2024 10-K, again in the FY2025 10-K, and again in FY2026 — same period, same value, different accession numbers.

The trap is the `fy` field. It looks like the fiscal year of the fact, but it comes from `DocumentFiscalYearFocus` and describes **the filing that reported it**. Apple's FY2024 revenue appears with `fy: 2024` and with `fy: 2025`. Filtering on `fy` silently returns the wrong year — or drops facts that only ever appeared as comparatives.

**The fix:** filter on period semantics, not filing metadata.

- Keep records whose `end − start` spans 350–380 days (this is what makes a record annual, not `fp`)
- Match on the calendar year of `end`, not on `fy`
- Where duplicates survive, take `max(filed)` — the most recent filing carries any restated figure
- Where nothing survives, return an error; never relax the filter

This handles differing fiscal calendars without special-casing. Apple's FY2024 ends 2024-09-28; Microsoft's ends 2024-06-30.

---

## Design decisions

**Metric names resolve to an ordered list of GAAP tags, never one.** Revenue is `RevenueFromContractWithCustomerExcludingAssessedTax` post-ASC 606, `Revenues` or `SalesRevenueNet` before it. All three appear in Apple's tag list. A candidate tag is accepted only if records survive the period filter — tag *presence* is not evidence of usable data.

Tags are matched exactly, never by substring. `SalesRevenueServicesGross` and `RevenueRemainingPerformanceObligation` both contain "revenue" and neither is total revenue.

**Period parsing is an allowlist, not a blocklist.** `re.fullmatch` against a single accepted shape. `FY2024`, `fiscal 2024`, `2024`, `FY24` parse; `Q3 2024`, `September 2024`, and `FY2024 vs FY2023` are rejected because they don't match the whole pattern — including forms nobody anticipated.

Relative periods (`last fiscal year`, `latest`) are **refused rather than resolved**. "Last" relative to today, to the most recent filing, or to the most recent fact are three different answers, and picking one silently produces a wrong number with correct-looking provenance. The refusal returns to the model as an observation, which retries with an explicit year — one extra turn instead of a wrong figure.

**Errors are classified, not caught uniformly.** Deterministic failures (malformed arguments, unknown metric, no matching record) return to the model immediately — retrying identical input produces an identical error. Transient failures (network, rate limit) retry with exponential backoff. Programming errors (`NameError`, `TypeError`, `KeyError`) propagate and crash.

That last rule came from a real incident: a missing import was caught by a broad `except Exception` and returned to the model as a tool observation. The agent read "the tool failed," concluded the data was unavailable, and burned a full run before giving up. A code defect had been laundered into a data-availability message. Broad exception handling in an agent harness doesn't just hide bugs — it actively misinforms the model.

**Caching is scoped to staleness.** The ticker map is fetched once and tolerates being stale. Company facts are cached per company, since fiscal calendars differ and a combined file couldn't be expired selectively. Cold fetch is ~2.6s; cached reads are ~17ms.

---

## Grounding

The system prompt requires every numeric figure in the final answer to originate from a tool result in that conversation.

This was tested unintentionally. During the incident above, both tool channels failed. The model attempted the alternate channel, found it also unusable, and then stated it could not proceed — listing precisely which figures it needed. It did not fill in Apple's revenue from training memory, despite plainly knowing it.

A prompt-level rule is not an enforcement mechanism. A deterministic verification gate — extracting every number from the final answer and confirming each traces to a tool result — is the next thing to be built.

---

## Repository layout

```
helpers.py    EDGAR ingestion: get_cik, get_company_facts,
              normalize_period, get_financial_fact
agent.py      planning pass, ReAct loop, tool dispatch,
              retry and error classification
data/cache/   local EDGAR cache (gitignored, fetched on first run)
helpers.py    EDGAR ingestion: get_cik, get_company_facts,
              normalize_period, get_financial_fact
agent.py      planning pass, ReAct loop, tool dispatch,
              retry and error classification
data/cache/   local EDGAR cache (gitignored, fetched on first run)
```

---

## Running it

```bash
git clone <repo>
cd agentic-financial-analyst
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in both values
python agent.py
```

`.env` requires:

```
ANTHROPIC_API_KEY=...
SEC_USER_AGENT=Your Name your@email.com
```

SEC's fair-access policy requires a real contact in the User-Agent header; requests without one are rejected. Nothing is vendored — the ticker map and company facts are fetched and cached on first run.

Run `python helpers.py` to exercise the ingestion layer on its own.

---

## Current state

**Built:** CIK resolution, companyfacts ingestion with disk caching, period normalization, tag resolution with fallback, the annual-period filter, `get_financial_fact` with full lineage, calculator, planning pass, ReAct loop, tool dispatch, classified retry.

**Next:** deterministic verification gate; `search_filings` over narrative sections (currently stubbed); eval harness measuring answer correctness, groundedness, and tool-call trajectory across repeated runs; deployment behind FastAPI with Bedrock inference, Terraform-provisioned infrastructure, and CloudWatch instrumentation.

---

## A note on non-determinism

Two runs of the identical query produced different answers to "which grew faster." One computed percentage-point change (+0.22 / −0.79); the other computed relative growth (+2.79% / −6.19%). Both are defensible readings and both trace correctly to source data.

This is why single-run evaluation is insufficient. Reliability is a rate, measured across repeated runs — not a score from one pass.
