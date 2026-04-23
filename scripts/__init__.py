"""State-specific scrapers for the K12 adoption intelligence pipeline.

Each module in this package exports a `parse(html)` function that takes
the raw HTML of a state DOE instructional-materials page and returns a
normalized dict describing current adoption cycles for that state.

A separate coordinator (scripts/run_adapters.py) calls each adapter,
compares the output to the prior day's data, and merges high-confidence
changes into adoption_data.json. On any adapter failure, yesterday's
data for that state is retained and flagged as stale rather than dropped.
"""
