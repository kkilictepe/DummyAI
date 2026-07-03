"""The copilot answering prompt's behavioural contract.

Each rule in the copilot "System prompt contract" (docs/flows/copilot.md) is pinned by a
distinctive substring, so a prompt edit that silently drops one fails loudly. This is the "test
guard" the doc promises for each rule — keep the two in sync.
"""

from __future__ import annotations

from src.prompts.copilot import COPILOT_SYSTEM_PROMPT


def test_prompt_encodes_behavioural_rules() -> None:
    prompt = COPILOT_SYSTEM_PROMPT
    # Markdown output format.
    assert "Markdown" in prompt
    # 1. No manual T-code deflections.
    assert "NEVER tell the operator to check something manually" in prompt
    # 2. Ask for missing / ambiguous parameters instead of guessing.
    assert "ask the operator a specific clarifying question" in prompt
    # 3. Default time window = last 5 minutes.
    assert "last 5 minutes" in prompt
    # 4. Metric provenance footer.
    assert "This report is based on the following metrics" in prompt
    # 5. Severity-graded anomalies (never just presence).
    assert "severity (critical / warning / info)" in prompt
    # 6. No ticketing / alert-management store to offer.
    assert "no access to a ticketing or alert-management store" in prompt
