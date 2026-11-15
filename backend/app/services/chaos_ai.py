"""The Chaos model: writes a round's trouble tickets and its debrief.

Chaos never decides *what* breaks — the fault catalog does. The model only
puts the tickets in the reporter's voice and narrates the debrief. Every
call falls back to the catalog's own text when AI is off, the call fails,
or the answer would give the fault away.
"""
from __future__ import annotations

import logging
import os
import re

from app import config
from app.services import llm

logger = logging.getLogger(__name__)

CHAOS_MODEL = os.environ.get("PLABS_CHAOS_MODEL", "").strip() or "claude-sonnet-5-5"
# Words a ticket must never contain, whatever the fault (the cause belongs
# to the learner to find).
_ALWAYS_LEAK = ("misconfig", "configuration change", "chaos", "injected", "fault", "route-policy", "route-map",
                "prefix-list", "password", "authentication", "mtu", "remote-as", "next-hop-self", "passive",
                "metric-style", "multi-topology", "circuit-type", "hello", "timer", "area range", "redistribut")

_TICKET_SYSTEM = """You write trouble tickets for a network troubleshooting exercise in a lab.
Rewrite the ticket you're given in the voice of the person or system that reported it, the way a real
ticket in a service provider's queue reads: what they observe, since when, what they tried.

Rules:
- Keep every fact from the original: addresses, prefixes, AS numbers, names, what works and what doesn't.
  Don't add new technical facts and don't change any.
- Never state, hint at or guess the cause, and never mention configuration, protocols' internals,
  timers, policies, passwords or authentication — the learner has to find the cause.
- {detail}
- Plain text, no headings, no markdown, 2 to 5 sentences. Output only the ticket text — don't
  open with "Ticket from …" or repeat who reported it; that's shown separately."""

_DETAIL = {
    "guided": "Keep all the detail of the original.",
    "standard": "Keep the detail of the original.",
    "expert": "Be terse, like a busy reporter: keep only what they'd actually notice and the facts needed to reproduce it.",
}

_DEBRIEF_SYSTEM = """You are Chaos, the gremlin that broke a lab network in a troubleshooting exercise, now
writing the debrief for the network engineer who worked the tickets. Be direct, a little playful, and
technically precise. You get, for each fault: its ticket, what was changed, why it broke things, and how
to find it.

Write markdown, 120 to 250 words: one short paragraph per fault — what you broke, why the symptom
looked the way it did, and the fastest show commands that would have exposed it — then one or two
sentences of overall advice. If the engineer solved it, acknowledge that; if they gave up, be encouraging.
Don't invent facts beyond what you're given."""


def available() -> bool:
    return bool(config.AI_ENABLED and llm.ANTHROPIC_KEY)


def _ask(system: str, prompt: str, max_tokens: int = 2000) -> str | None:
    if not available():
        return None
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=llm.ANTHROPIC_KEY)
        msg = client.beta.messages.create(
            model=CHAOS_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"},            # short writing: no deep reasoning needed
            # On a safety-classifier decline, re-run on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as e:                     # status, connection and timeout errors
        logger.warning("Chaos model call failed: %s", e)
        return None
    if msg.stop_reason in ("refusal", "max_tokens"):
        return None
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return text or None


def _leaks(text: str, fault: dict, variant: dict) -> bool:
    low = text.lower()
    terms = [*_ALWAYS_LEAK, *(fault.get("leak_terms") or []), *(variant.get("leak_terms") or [])]
    return any(str(t).lower() in low for t in terms if str(t).strip())


def write_ticket(fault: dict, variant: dict, difficulty: str) -> str | None:
    """The variant's ticket in the reporter's voice, or None (use the original)."""
    original = variant["ticket"].strip()
    prompt = f"Reported by: {variant.get('reporter', 'NOC')}\n\nOriginal ticket:\n{original}"
    text = _ask(_TICKET_SYSTEM.format(detail=_DETAIL.get(difficulty, _DETAIL["standard"])), prompt, max_tokens=1500)
    if not text or len(text) > 1200 or _leaks(text, fault, variant):
        return None
    # The facts must survive: every address / prefix / AS number of the original.
    facts = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?\b|\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,}(?:/\d+)?\b|\bAS ?\d+\b",
                           original, re.I))
    if any(f.lower() not in text.lower() for f in facts):
        return None
    return text


def write_debrief(title: str, difficulty: str, items: list[dict], solved: bool, seconds: int, hints: int) -> str | None:
    parts = [f"Scenario: {title}. Difficulty: {difficulty}. Outcome: {'solved' if solved else 'gave up'} "
             f"after {seconds // 60} min {seconds % 60} s with {hints} hint(s)."]
    for i, it in enumerate(items, 1):
        changed = "\n".join(f"{d}:\n{c}" for d, c in it["changed"].items())
        parts.append(f"Fault {i}: {it['title']} ({'fixed' if it['resolved'] else 'not fixed'})\n"
                     f"Ticket: {it['ticket']}\nWhat was changed:\n{changed}\n"
                     f"Why it broke things: {it['explanation']}\nHow to find it: {it['find']}")
    return _ask(_DEBRIEF_SYSTEM, "\n\n".join(parts), max_tokens=3000)
