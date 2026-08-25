CASES = [
    {
        "id": "aapl_rd_ratio_fy2024",
        "query": "What was Apple's R&D as a percentage of revenue in FY2024?",
        "expected_numbers": [8.02],
        "required_tools": ["get_financial_fact", "calculator"],
        "forbidden_tools": ["search_filings"],
        "channel": "xbrl",
    },
    {
        "id": "msft_revenue_fy2023",
        "query": "What was Microsoft's total revenue in FY2023?",
        "expected_numbers": [211915000000],
        "required_tools": ["get_financial_fact"],
        "forbidden_tools": ["search_filings"],
        "channel": "xbrl",
    },
    {
        "id": "aapl_rd_strategy_fy2024",
        "query": "Why does Apple invest in research and development? Cite the filing section.",
        "expected_numbers": [],
        "required_tools": ["search_filings"],
        "forbidden_tools": [],
        "channel": "narrative",
    },
    {
        "id": "hero_full",
        "query": (
            "Compare Apple's and Microsoft's R&D as a percentage of revenue for "
            "FY2023 and FY2024. Which company's ratio grew faster? Then summarize "
            "each company's stated strategic reasoning for its R&D investment, "
            "citing the filing sections."
        ),
        "expected_numbers": [7.80, 8.02, 12.83, 12.04],
        "required_tools": ["get_financial_fact", "calculator", "search_filings"],
        "forbidden_tools": [],
        "channel": "both",
    },
    {
        "id": "bad_period_recovery",
        "query": "What was Apple's revenue last fiscal year?",
        "expected_numbers": [],
        "required_tools": ["get_financial_fact"],
        "forbidden_tools": [],
        "channel": "xbrl",
        "expect_tool_error": True,
    },
]