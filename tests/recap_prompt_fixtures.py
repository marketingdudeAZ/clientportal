"""The 7 ticket fixtures the recap prompt golden test pins.

Shared by the golden generator and tests/test_ticket_recap_prompt.py so the
captured goldens and the assertions can never drift apart.

Six fixtures cover every TYPE_FRAMING key; the seventh is a budget_update
carrying currency custom fields, which is the path that triggers the
numbers-exception block appended for budget-type generation.
"""

from __future__ import annotations

FIXTURES: list[dict] = [
    {
        "id": "new_account_build",
        "ticket_type": "new_account_build",
        "task": {
            "name": "New account build - The Quincy",
            "text_content": "Stand up paid search and PMax for The Quincy.",
            "list": {"name": "New Account Build"},
            "custom_fields": [
                {"name": "Paid Search Budget", "type": "currency", "value": "4000"},
                {"name": "PMax Budget", "type": "currency", "value": "1500"},
                {"name": "QA Status", "type": "drop_down", "value": "done"},
            ],
        },
        "comments": [
            {"text": "Specialist confirmed tracking is live."},
            {"text": "Manager coached on the naming convention."},
        ],
    },
    {
        "id": "budget_update",
        "ticket_type": "budget_update",
        "task": {
            "name": "Budget update - Riverside Flats",
            "text_content": "Client asked to raise paid search.",
            "list": {"name": "Budget Update"},
            "custom_fields": [
                {"name": "New Paid Search Budget", "type": "currency", "value": "6200"},
            ],
        },
        "comments": [{"text": "Applied the increase and confirmed pacing."}],
    },
    {
        "id": "general",
        "ticket_type": "general",
        "task": {
            "name": "Update phone number",
            "text_content": "Front desk number changed.",
            "list": {"name": "General Requests"},
            "custom_fields": [],
        },
        "comments": [{"text": "Updated everywhere it appears."}],
    },
    {
        "id": "creative_update",
        "ticket_type": "creative_update",
        "task": {
            "name": "Refresh ad copy",
            "text_content": "Swap in the fall specials.",
            "list": {"name": "Creative / Ad Copy"},
            "custom_fields": [
                {"name": "Key Messages", "type": "text", "value": "target young professionals"},
            ],
        },
        "comments": [{"text": "New copy is live."}],
    },
    {
        "id": "performance_review",
        "ticket_type": "performance_review",
        "task": {
            "name": "Q3 performance review",
            "text_content": "Walk through Q3 results.",
            "list": {"name": "Performance Review"},
            "custom_fields": [],
        },
        "comments": [{"text": "Shifted spend toward the better performing channel."}],
    },
    {
        "id": "rebrand",
        "ticket_type": "rebrand",
        "task": {
            "name": "Rebrand rollout",
            "text_content": "Property renamed from Oak Park to The Vale.",
            "list": {"name": "Rebrand"},
            "custom_fields": [],
        },
        "comments": [{"text": "All assets updated to the new name."}],
    },
    {
        # Seventh: budget_update with several currency fields plus internal
        # noise fields, exercising the currency formatting and _FIELD_SKIP.
        "id": "budget_update_currency_detail",
        "ticket_type": "budget_update",
        "task": {
            "name": "Multi-channel budget change",
            "text_content": "Raise search, add display.",
            "list": {"name": "Budget Update"},
            "custom_fields": [
                {"name": "Paid Search Budget", "type": "currency", "value": "4000.4"},
                {"name": "Display Budget", "type": "currency", "value": "900"},
                {"name": "Account Manager", "type": "text", "value": "Jane Doe"},
                {"name": "Target Audience", "type": "text", "value": "young professionals"},
                {"name": "Market", "type": "text", "value": "Phoenix"},
            ],
        },
        "comments": [
            {"text": "Confirmed with the client."},
            {"text": ""},
        ],
    },
]
