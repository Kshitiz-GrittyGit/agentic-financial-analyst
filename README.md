# Agentic Financial Analyst

An agent that answers multi-step financial questions over public SEC EDGAR filings, with every number traceable to a source filing and every narrative claim traceable to a filing section — and an eval harness that measures how often that actually holds.

Built without an agent framework. The planning pass, the ReAct loop, tool dispatch, retry, and error classification are all hand-written against the raw Anthropic SDK — the goal was to understand each mechanism before reaching for an abstraction.

---

## Example

**Query:** *Compare Apple's and Microsoft's R&D as a percentage of revenue for FY2023 and FY2024. Which company's ratio grew faster? Then summarize each company's stated strategic reasoning for its R&D investment, citing the filing sections.*

The agent plans, then issues 8 `get_financial_fact` calls, 6 `calculator` steps, and 2 `search_filings` queries — 5 turns, 18 tool calls, ~33 seconds.

| | FY2023 | FY2024 | Change |
|---|---|---|---|
| Apple | 7.80% | 8.02% | +0.22 pp |
| Microsoft | 12.83% | 12.04% | −0.79 pp |

Every figure resolves to a specific accession number, form type, and reporting period. Every narrative claim resolves to a section path — Apple's rationale from `Item 1. Business > Markets and Distribution` and `Item 7. MD&A > Operating Expenses`, Microsoft's from `PART I > Investing in the Future`.

---

## Architecture

```
question
   ↓
make_plan            text plan, no tools bound
   ↓
ReAct loop           model selects, harness acts
   ↓
   ├── get_financial_fact   exact figures from XBRL
   ├── search_filings       narrative passages from 10-K text
   └── calculator           deterministic arithmetic
   ↓
answer with lineage
```

The division of labour is strict and stated in the tool descriptions: **XBRL owns every number, retrieval owns every argument.** Financial figures also appear inside 10-K prose, so the retriever will happily return them — the model is instructed never to read a figure out of a passage. In practice this holds: in a full run, a retrieved passage contained Apple's R&D figures in text and the model still sourced them from XBRL.

---

## Part 1 — Getting the number right

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

## Part 2 — Getting the narrative right

There is no XBRL tag for *why* a company is spending more on R&D. That sentence exists only in the narrative sections of the 10-K, so the second channel retrieves it.

**EDGAR serves filings as HTML, not PDF.** `filing.py` resolves a fiscal year to a specific 10-K through the `submissions` API — matching on `reportDate`, not `filingDate`, for the same reason the XBRL filter avoids `fy` — then downloads the document and renders it to PDF. Section headings survive the render, which is what makes the next step work.

**Chunks carry their section path.** A bare 1,500-character passage is close to useless as evidence: it doesn't say which company, year, or section it came from. `ingestion.py` builds a heading hierarchy first, then attaches a breadcrumb to every chunk — `Item 7. MD&A > Operating Expenses`.

Heading detection by font size alone failed here. EDGAR's rendered filings use bold at body size for table headers, cover-page labels, and checkbox rows, so font metrics promoted `☒` and `California94-2404110` to headings while real sections were missed. A 10-K has canonical structure, so the classifier matches it directly — `PART [IVX]+` and `Item \d+[A-Z]?\.` become level-1 headings, with font size and case demoted to a secondary signal for sub-headings.

**Metadata filtering does the correctness work; vectors only do relevance.** With four filings in one collection, pure semantic search will return Microsoft's FY2023 R&D discussion when asked about Apple FY2024 — corporate prose about R&D investment is near-identical across companies and years, and an embedding model cannot separate them. `search_filings` filters the collection to `ticker` and `fiscal_year` *first*, then searches within that subset. `embeddings.py` creates Qdrant payload indexes on both fields for exactly this.

That makes the filename-to-metadata link load-bearing: if a PDF's name doesn't match its registry entry, no metadata attaches, the filter has nothing to filter on, and the failure is a confident cross-company answer with no error anywhere. Both the filename and the registry entry are generated by the same function so they cannot drift.

---

## Part 3 — Measuring it

Running the agent once and reading the trace tells you what happened in one run. It does not tell you what happens usually — and this system is not deterministic. Three runs of the identical query produced three different treatments of "which grew faster": percentage-point change, relative growth, and both.

`eval/` runs cases repeatedly and scores the recorded runs.

```
eval/cases.py      test cases with expected numbers and tool expectations
eval/runner.py     executes each case N times, appends one JSON line per run
eval/scorers.py    deterministic scorers over a recorded run
eval/report.py     pass rates and failure detail from a results file
```

The runner only executes and records; scoring happens afterwards over the saved file. That separation means a new scorer can be run against existing results without paying for the agent runs again — and scorers change far more often than the agent does.

**Deterministic where the question has an exact answer; LLM-judged where it needs semantic understanding.**

| Dimension | Method |
|---|---|
| Correctness of numbers | deterministic — exact match at stated precision |
| Number groundedness | deterministic — every figure traces to a tool result |
| Tool routing | deterministic — required present, forbidden absent |
| Narrative faithfulness | Ragas over the retrieved passages |
| Retrieval precision | Ragas over the retrieved passages |

Groundedness is deliberately *not* LLM-judged. Asking a model to scan thirty numbers and confirm exact matches is asking it to do the thing models are worst at, and it introduces a circularity — using an LLM to check whether an LLM invented a number. The same extraction code also becomes the runtime verification gate, which a judge could never be: too slow, too expensive, too variable.

**Defining "grounded" precisely was forced by writing the scorer.** A unit conversion (`0.2174` percentage points reported as `21.7 bps`) carries no new information and counts as grounded. New arithmetic performed by the model does not. The scorer compares magnitudes at the stated scale and precision, and does not verify sign — natural language often carries the sign in words ("declined by 6.19%"), so sign is left to the correctness scorer.

### First results

Baseline, one run per case:

```
case                         done  correct  grounded  routing  turns  calls   secs
aapl_rd_ratio_fy2024          1/1      1/1       1/1      1/1    3.0    3.0    9.1
msft_revenue_fy2023           1/1      1/1       1/1      1/1    2.0    1.0    6.0
aapl_rd_strategy_fy2024       1/1      1/1       1/1      1/1    3.0    2.0   22.9
hero_full                     1/1      1/1       0/1      1/1    5.0   18.0   33.0
bad_period_recovery           1/1      1/1       1/1      1/1    2.0    1.0   14.3
```

---

## Grounding, and where it leaks

The system prompt requires every numeric figure in the final answer to originate from a tool result in that conversation.

This was tested unintentionally. During a run in which both tool channels failed, the model attempted the alternate channel, found it also unusable, and then stated it could not proceed — listing precisely which figures it needed. It did not fill in Apple's revenue from training memory, despite plainly knowing it.

But a prompt-level rule is not an enforcement mechanism, and successful runs show the gap. One run reported Apple's R&D growth as **"$1.455 billion"** — arithmetically correct, computed by the model, traceable to nothing. The same query on a later run produced no such figure. The leak is intermittent, which is the whole argument for measuring a rate rather than inspecting a run.

The next step is a deterministic verification gate reusing the groundedness scorer's extraction, run inside the request and able to block an answer. It will be measured for false-positive rate before it is allowed to block anything.

---

## Known limitations

**XBRL retrieval is point-query only.** One company, one metric, one fiscal year per call. Any question spanning a range — a trend, a top-N ranking, "the last five years" — must be decomposed by the model into N separate calls, and the model must supply the list of years from its own knowledge rather than from the data.

This produced a measured failure. Asked for Apple's revenue "last fiscal year," the agent resolved the relative reference itself and queried FY2024; as of August 2026 the correct answer is FY2025. The returned figure traced perfectly to a tool result, so groundedness passed — the error was in a year chosen *before any tool was called*, which nothing in the system observes. The tool's period allowlist refuses relative strings like "last fiscal year," but that defence only applies to strings that reach the tool; it cannot prevent the model from resolving the ambiguity upstream.

The fix is to accept a range in the period argument (`FY2015-FY2024`, or `all`) and return every matching annual record. Available years then become a property of the data the model can observe rather than something it must recall, and multi-year questions become one call instead of N.

Also outstanding:

- Fiscal-year label is assumed equal to the calendar year of period end. True for AAPL and MSFT; breaks for January/February year-end filers.
- XBRL retrieval covers duration concepts only. Instantaneous balance-sheet facts (`Assets`, `StockholdersEquity`) are skipped.
- Quarterly periods are not supported.
- Table extraction is deliberately out of scope, so table rows appear in narrative chunks as unstructured text. Harmless, since XBRL owns the numbers.
- Sub-headings within Item 7 are not detected, so MD&A citations resolve to the Item rather than the subsection.
- Retrieval scores cluster tightly (0.60–0.68 across the top 5) on corporate prose, so a score threshold is not a usable relevance filter. `top_k` is the only control.
- Chunk size (1500 characters) and overlap (200) are chosen to sit under BGE-large's 512-token limit, not empirically tuned. Sweeping them against retrieval precision is what the eval harness is for.
- The calculator still uses `eval()`. Replacing it with an AST-walking evaluator is required before any network-exposed deployment.

---

## Design decisions

**Metric names resolve to an ordered list of GAAP tags, never one.** Revenue is `RevenueFromContractWithCustomerExcludingAssessedTax` post-ASC 606, `Revenues` or `SalesRevenueNet` before it. All three appear in Apple's tag list. A candidate tag is accepted only if records survive the period filter — tag *presence* is not evidence of usable data.

Tags are matched exactly, never by substring. `SalesRevenueServicesGross` and `RevenueRemainingPerformanceObligation` both contain "revenue" and neither is total revenue.

**Period parsing is an allowlist, not a blocklist.** `re.fullmatch` against a single accepted shape. `FY2024`, `fiscal 2024`, `2024`, `FY24` parse; `Q3 2024`, `September 2024`, and `FY2024 vs FY2023` are rejected because they don't match the whole pattern — including forms nobody anticipated. Both tools share this parser, so they accept and reject identically.

Relative periods (`last fiscal year`, `latest`) are **refused rather than resolved**. "Last" relative to today, to the most recent filing, or to the most recent fact are three different answers, and picking one silently produces a wrong number with correct-looking provenance.

**Errors are classified, not caught uniformly.** Deterministic failures (malformed arguments, unknown metric, no matching record) return to the model immediately — retrying identical input produces an identical error. Transient failures (network, rate limit) retry with exponential backoff. Programming errors (`NameError`, `TypeError`, `KeyError`) propagate and crash.

That last rule came from a real incident: a missing import was caught by a broad `except Exception` and returned to the model as a tool observation. The agent read "the tool failed," concluded the data was unavailable, and burned a full run before giving up. A code defect had been laundered into a data-availability message. Broad exception handling in an agent harness doesn't just hide bugs — it actively misinforms the model.

**Caching is scoped to staleness.** The ticker map is fetched once and tolerates being stale. Company facts are cached per company, since fiscal calendars differ and a combined file couldn't be expired selectively. Cold fetch is ~2.6s; cached reads are ~17ms.

---

## Repository layout

```
helpers.py       XBRL: get_cik, get_company_facts,
                 normalize_period, get_financial_fact
filing.py        EDGAR filing retrieval: submissions lookup,
                 HTML to PDF render, registry generation
ingestion.py     PDF to heading hierarchy to breadcrumbed chunks
embeddings.py    chunks + metadata into Qdrant
search.py        search_filings: filtered vector search
agent.py         planning pass, ReAct loop, tool dispatch,
                 retry and error classification
eval/            cases, runner, scorers, report
data/cache/      EDGAR JSON cache      (gitignored)
documents/       rendered filings      (gitignored)
output/          chunked filings       (gitignored)
```

Nothing is vendored. Every artifact above is regenerated from public sources by the commands below.

---

## Running it

```bash
git clone <repo>
cd agentic-financial-analyst
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in both values
```

`.env` requires:

```
ANTHROPIC_API_KEY=...
SEC_USER_AGENT=Your Name your@email.com
```

SEC's fair-access policy requires a real contact in the User-Agent header; requests without one are rejected.

Start the vector store:

```bash
docker run -d --name qdrant -p 6333:6333 \
  -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant
```

Build the corpus, then run the agent:

```bash
python filing.py       # fetch 10-Ks from EDGAR, render to PDF, write registry
python ingestion.py    # parse into breadcrumbed chunks
python embeddings.py   # embed into Qdrant (downloads BGE-large on first run)
python agent.py        # run the hero query
```

Run the evals:

```bash
cd eval
python runner.py       # execute every case, write results/<timestamp>.jsonl
python report.py       # pass rates and failure detail from the latest results
```

Embedding defaults to a local BGE-large model. Set `EMBED_PROVIDER=bedrock` for Amazon Titan Embed v2 — the two produce different vector spaces, so switching requires deleting the collection and re-embedding.

Each module runs standalone for testing: `python helpers.py` exercises XBRL retrieval, `python search.py` exercises retrieval against the index.

---

## Current state

**Built:** CIK resolution, companyfacts ingestion with disk caching, period normalization, tag resolution with fallback, the annual-period filter, `get_financial_fact` with full lineage; EDGAR filing retrieval and HTML-to-PDF rendering, hierarchical chunking with breadcrumbs, embedding into Qdrant with filterable metadata, `search_filings` with pre-filtered vector search; calculator, planning pass, ReAct loop, tool dispatch, classified retry; eval harness with deterministic scorers for correctness, groundedness, and tool routing. The full two-channel query runs end to end.

**Next:** Ragas scoring for narrative faithfulness and retrieval precision; replacing `eval()` in the calculator with an AST-walking evaluator; the deterministic verification gate; exposing the tools over MCP; deployment behind FastAPI with Bedrock inference, Terraform-provisioned infrastructure, and CloudWatch instrumentation.

---

## A note on non-determinism

Three runs of the identical query produced three different answers to "which grew faster." One computed percentage-point change (+0.22 / −0.79); one computed relative growth (+2.79% / −6.19%); one computed both. All are defensible readings and all trace correctly to source data.

Separately, an ungrounded figure appeared in one run of a query and not in the next.

This is why single-run evaluation is insufficient. Reliability is a rate, measured across repeated runs — not a score from one pass.
