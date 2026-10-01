"""WhatsApp gate channel through Meta's official Cloud API (Phase 7, D2/D10/D11/D48).

Notifications and structured decisions only: nothing a user types is ever relayed to an agent,
except a redirect note, which becomes `HumanDecision.note` for the long-term planner.
"""
