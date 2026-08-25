import json
import re

SCALES = {
    "%": 1.0, "pp": 1.0, "percentage point": 1.0, "percentage points": 1.0,
    "bps": 0.01, "basis point": 0.01, "basis points": 0.01,
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12,
}

NUM_RE = re.compile(
    r"(?P<sign>[-+\u2212]?)\$?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<suffix>%|bps|basis points?|percentage points?|pp|"
    r"billion|bn|million|mn|thousand|trillion|[BMK])?",
    re.IGNORECASE,
)


def extract_numbers(text, financial_only=True):
    """Return [{raw, stated, scale, decimals}] for financial-looking numbers."""
    out = []
    for m in NUM_RE.finditer(text or ""):
        raw = m.group(0).strip()
        suffix = (m.group("suffix") or "").lower().strip()
        num_str = m.group("num")

        has_marker = bool(suffix) or "$" in raw or "," in num_str
        if financial_only and not has_marker:
            continue

        stated = float(num_str.replace(",", ""))
        if m.group("sign") in ("-", "\u2212"):
            stated = -stated

        decimals = len(num_str.split(".")[1]) if "." in num_str else 0
        out.append({
            "raw": raw,
            "stated": stated,
            "scale": SCALES.get(suffix, 1.0),
            "decimals": decimals,
        })
    return out


def tool_result_numbers(tool_calls):
    """Every numeric value any tool returned."""
    values = []
    for call in tool_calls:
        result = call.get("result", "")
        try:
            parsed = json.loads(result)
            values.extend(_walk_numbers(parsed))
        except (json.JSONDecodeError, TypeError):
            pass
        for m in re.finditer(r"-?\d+(?:\.\d+)?", str(result)):
            values.append(float(m.group()))
    return values


def _walk_numbers(obj):
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _walk_numbers(x)]
    if isinstance(obj, list):
        return [v for x in obj for v in _walk_numbers(x)]
    return []


def score_number_groundedness(record):
    """Every financial number in the answer traces to a tool result."""
    answer_nums = extract_numbers(record["final_answer"])
    tool_nums = tool_result_numbers(record["tool_calls"])

    ungrounded = []
    for a in answer_nums:
        target = a["stated"]
        if not any(
            round(t / a["scale"], a["decimals"]) == round(target, a["decimals"])
            for t in tool_nums
        ):
            ungrounded.append(a["raw"])

    return (not ungrounded), {"ungrounded": ungrounded, "checked": len(answer_nums)}


def score_correctness(record, case):
    """Every expected number appears in the answer at its stated precision."""
    answer_nums = extract_numbers(record["final_answer"])
    missing = []
    for expected in case["expected_numbers"]:
        if not any(
            round(a["stated"] * a["scale"], 6) == round(expected, 6)
            or round(a["stated"], 2) == round(expected, 2)
            for a in answer_nums
        ):
            missing.append(expected)
    return (not missing), {"missing": missing}


def score_tool_routing(record, case):
    used = {c["name"] for c in record["tool_calls"]}
    missing = [t for t in case.get("required_tools", []) if t not in used]
    forbidden = [t for t in case.get("forbidden_tools", []) if t in used]
    return (not missing and not forbidden),            {"missing": missing, "forbidden_used": forbidden}