"""The Chaos model (Claude Sonnet 5.5 by default): plans each round, writes
its tickets and its debrief.

- plan_round: from every verified candidate fault and the learner's past
  rounds, designs the incident — which faults, where, and the story behind
  them. The server only accepts picks from the candidate list and still
  enforces the combination rules; anything else falls back to a random draw.
- write_tickets: writes each ticket from what Chaos *observed* breaking (the
  failing checks) — the writer never sees the cause, so it can't leak it.
- write_debrief: explains the round and judges how the learner fixed it,
  from a diff of their routers' configs.
Every call falls back to plain behaviour when AI is off or a call fails.
"""
from __future__ import annotations

import json
import logging
import os
import time

from app import config
from app.services import llm

logger = logging.getLogger(__name__)

CHAOS_MODEL = os.environ.get("PLABS_CHAOS_MODEL", "").strip() or "claude-sonnet-5-5"
# Words a ticket must never contain (the cause belongs to the learner to find).
_ALWAYS_LEAK = ("misconfig", "configuration change", "chaos", "injected", "fault", "route-policy", "route-map",
                "prefix-list", "password", "authentication", "mtu", "remote-as", "next-hop-self", "passive",
                "metric-style", "multi-topology", "circuit-type", "hello", "timer", "area range", "redistribut",
                "maximum-prefix", "no-export", "community", "update-source", "distribute-list", "md5")


def available() -> bool:
    return bool(config.AI_ENABLED and llm.ANTHROPIC_KEY)


def _ask(system: str, prompt: str, max_tokens: int = 4000, schema: dict | None = None, effort: str = "low"):
    """Text, or parsed JSON when a schema is given. None on any failure."""
    if not available():
        return None
    import anthropic
    oc: dict = {"effort": effort}
    if schema:
        oc["format"] = {"type": "json_schema", "schema": schema}
    try:
        client = anthropic.Anthropic(api_key=llm.ANTHROPIC_KEY)
        msg = client.beta.messages.create(
            model=CHAOS_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=oc,
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
    if not schema:
        return text or None
    try:
        return json.loads(text)
    except ValueError:
        return None


# --- planning -------------------------------------------------------------------------------

_PLAN_SYSTEM = """You are Chaos, the adversary in a network troubleshooting exercise for engineers studying
for the CCNP Service Provider exam. A healthy lab network is about to be broken; you decide how.

You get the menu of breaks that have been verified on this network (each one a template applied to one
place: a link end, a router, a BGP session…), and the engineer's previous rounds. Pick exactly N breaks and
invent the incident they belong to.

Make every round feel different:
- Vary protocols, layers (neighbor adjacency vs route advertisement), devices, operating systems (IOS XR /
  IOS XE) and address families. Avoid breaks the engineer has already had, unless you're deliberately
  revisiting something they gave up on or solved slowly — then pick it in a *different place*.
- With several breaks, make them plausible together: one change window, one engineer's bad night, a
  migration half done. Breaks may interact (fixing one can reveal another) — that's good at higher
  difficulty.
- Never reuse a previous round's theme or story shape. Incidents can be a change window, a migration, a
  vendor's script, a copy-paste from another router, an audit "hardening", a new peering, an outage
  clean-up, a training exercise gone wrong… or just unrelated bad luck.
- Difficulty: guided → clear, single-cause symptoms; standard → mixed; expert → subtle ones (advertisement
  and policy problems, IPv6-only or one-direction breaks, faults hidden behind others).

Rules: only ids from the menu; no two picks from the same template; no two picks sharing a scope key.
theme: a few words (it's shown to the engineer only after the round). story: 2–4 sentences, written as the
incident's back-story, for the debrief."""

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {"type": "string"}},
        "theme": {"type": "string"},
        "story": {"type": "string"},
    },
    "required": ["picks", "theme", "story"],
    "additionalProperties": False,
}


def plan_round(s, cands: list[dict], n: int, difficulty: str, history: list[dict]) -> dict | None:
    """{picks, theme, story} or None (→ random draw)."""
    if not cands:
        return None
    menu = "\n".join(f"{c['id']} | {c['protocol']}/{c['category']} | {c['title']} | {c['label']} | "
                     f"{c['family']} | scope: {', '.join(c['scope'])}" for c in cands)
    hist = "\n".join(f"- {h['outcome']}{' (by rebuilding the lab)' if h.get('rebuilt') else ''} in {h['minutes']} min, "
                     f"{h['hints']} hints ({h['difficulty']}), theme \"{h.get('theme') or '-'}\": "
                     f"{', '.join(h['faults'])}" for h in history) or "- none: this is the first round"
    prompt = (f"Scenario: {s.title}\n{s.summary}\n\nN = {n}. Difficulty: {difficulty}.\n\n"
              f"Previous rounds (oldest first):\n{hist}\n\nMenu (id | protocol/layer | break | where | OS | scope):\n{menu}")
    plan = _ask(_PLAN_SYSTEM, prompt, max_tokens=4000, schema=_PLAN_SCHEMA, effort="medium")
    if not isinstance(plan, dict) or not isinstance(plan.get("picks"), list):
        return None
    ids = {c["id"] for c in cands}
    plan["picks"] = [p for p in plan["picks"] if p in ids][:n]
    plan["theme"] = str(plan.get("theme") or "")[:120]
    plan["story"] = str(plan.get("story") or "")[:1200]
    return plan if plan["picks"] else None


# --- tickets ---------------------------------------------------------------------------------

_TICKET_SYSTEM = """You write the trouble tickets for a network troubleshooting exercise. Something in the
lab network broke; you only know what monitoring and the network's users observe — never the cause.

For each incident you get what is failing *right now* (re-checked after the network settled). Write one
ticket per incident, from the most plausible reporter: if a customer-visible check fails, the party that
owns what's affected (a customer, a peer's NOC); otherwise the provider's own monitoring. Write it the way
that reporter would: what *they* experience, not what the network's internals show. A customer says "we
can't reach 198.51.100.10 since 14:05", not which session or route is missing.

Level of detail:
{detail}

Rules:
- Only facts from the observations and the parties list: addresses, prefixes, AS numbers, device names,
  the time it started. Never claim anything is down or missing that isn't in the observations.
- Never guess at a cause, and never mention configuration, protocol internals, timers, policies,
  passwords or authentication. Never tell the engineer what to check.
- Never mention "checks", "health checks" or this exercise: the reporter knows nothing about them.
- Plain text, no headings. Don't start with "Ticket from" or repeat the reporter."""

_DETAIL = {
    "guided": ("Guided: 2–4 sentences. A user-reported ticket: the reporter's experience with the specifics they'd "
               "know (from where, to what, IPv4 or IPv6, since when, total or partial), then one line starting "
               "\"NOC notes:\" with at most two monitoring alarms. A monitoring ticket (nothing customer-visible "
               "broken yet): each alarm as a network management system raises it — device, protocol and the "
               "neighbor or prefix concerned (e.g. \"R2: BGP neighbor 2001:db8::3 down\", \"R4: route "
               "198.51.100.0/24 missing\") — and what's at risk (\"customers still reach everything, but the "
               "core has lost a path\")."),
    "standard": ("Standard: 2–3 sentences. A user-reported ticket: only the reporter's experience with the "
                 "specifics they'd know (from where, to what, IPv4 or IPv6, since when) — no alarms, no device "
                 "states. A monitoring ticket (nothing customer-visible broken yet): the alarms the NOC sees, as "
                 "a network management system words them — which device, which protocol, and the neighbor "
                 "or the prefix it concerns (e.g. \"R2 reports an iBGP neighbor down (IPv6)\", \"R4 lost its "
                 "route to 198.51.100.0/24\") — plus that customers aren't affected yet."),
    "expert": ("Expert: one short sentence, like a busy reporter: who can't reach what, nothing more (e.g. "
               "\"Customer A can't get to the internet over IPv6.\"). A monitoring ticket: the device and the "
               "protocol alarming, nothing more (e.g. \"BGP alarm on R2.\")."),
}

_TICKETS_SCHEMA = {
    "type": "object",
    "properties": {"tickets": {"type": "array", "items": {
        "type": "object",
        "properties": {"reporter": {"type": "string"}, "ticket": {"type": "string"}},
        "required": ["reporter", "ticket"], "additionalProperties": False}}},
    "required": ["tickets"],
    "additionalProperties": False,
}


def _leaks(text: str, extra=()) -> bool:
    low = text.lower()
    return any(str(t).lower() in low for t in (*_ALWAYS_LEAK, *extra) if str(t).strip())


def write_tickets(s, difficulty: str, story: str, items: list[dict], broken: list[dict]) -> list[dict | None] | None:
    """One {reporter, ticket} per item (None where the catalog's text should
    stay), written from observations only — what still fails after the
    network settled. `broken`: failing health checks as {describe, scope}.
    `story` is deliberately unused: the ticket writer must not know the cause."""
    del story
    parties = s.parties or []
    allowed = {p["name"] for p in parties} | {"AS 65000 monitoring", "Network monitoring"}
    who = "\n".join(f"- {p['name']}: {p.get('owns', '')}" for p in parties) or "- Network monitoring"
    svc = [b["describe"] for b in broken if b.get("scope") == "service"]
    internal = [b["describe"] for b in broken if b.get("scope") != "service"]
    incidents = "\n\n".join(
        f"Incident {i + 1} — failing checks:\n" + ("\n".join(f"- {o}" for o in it["observed"]) or "- none")
        for i, it in enumerate(items))
    since = time.strftime("%H:%M UTC", time.gmtime())
    prompt = (f"Network: {s.title}\nIt started at about {since}.\n\nParties (and what they own):\n{who}\n"
              f"- AS 65000 monitoring: the provider's own monitoring\n\n"
              f"Customer-visible checks failing (what users notice):\n" + ("\n".join(f"- {b}" for b in svc) or "- none")
              + "\n\nInternal checks failing (what monitoring sees):\n" + ("\n".join(f"- {b}" for b in internal) or "- none")
              + f"\n\n{incidents}")
    out = _ask(_TICKET_SYSTEM.format(detail=_DETAIL.get(difficulty, _DETAIL["standard"])), prompt,
               max_tokens=3000, schema=_TICKETS_SCHEMA)
    if not isinstance(out, dict):
        return None
    res: list[dict | None] = []
    written = out.get("tickets") or []
    for i, it in enumerate(items):
        t = written[i] if i < len(written) else None
        ok = (isinstance(t, dict) and t.get("ticket") and len(t["ticket"]) <= 1200
              and not _leaks(t["ticket"], it["fault"].get("leak_terms") or []))
        if ok:
            rep = t.get("reporter") if t.get("reporter") in allowed else it["fault"].get("reporter", "AS 65000 monitoring")
            res.append({"reporter": rep, "ticket": t["ticket"].strip()})
        else:
            res.append(None)
    return res


# --- debrief ---------------------------------------------------------------------------------

_DEBRIEF_SYSTEM = """You are Chaos, the gremlin that broke a lab network in a troubleshooting exercise, now
writing the debrief for the engineer who worked the tickets. Be direct, a little playful, and technically
precise.

You get the incident's back-story, each fault (its ticket, what was changed, why it broke things, how to
find it), and a diff of what the engineer changed on the routers they manage.

Write markdown, 150 to 300 words:
- For each fault: what you broke and why the symptom looked the way it did.
- How the engineer fixed it, judged from their diff: a precise fix of the root cause, extra changes that
  weren't needed (or that could hurt later), a workaround that hides the cause, or a rebuild from scratch.
  Any fix that restored the network counts — say what a faster, more surgical path would have been.
- The fastest show commands that would have exposed each fault.
- One or two sentences of overall advice. If they gave up, be encouraging.
Don't invent facts beyond what you're given."""


def write_debrief(title: str, difficulty: str, items: list[dict], solved: bool, seconds: int, hints: int,
                  story: str = "", changes: dict | None = None, rebuilt: bool = False, checks: int = 0) -> str | None:
    parts = [f"Scenario: {title}. Difficulty: {difficulty}. Outcome: {'solved' if solved else 'gave up'} after "
             f"{seconds // 60} min {seconds % 60} s, {checks} check(s), {hints} hint(s)."]
    if story:
        parts.append(f"Back-story: {story}")
    for i, it in enumerate(items, 1):
        changed = "\n".join(f"{d}:\n{c}" for d, c in it["changed"].items())
        parts.append(f"Fault {i}: {it['title']} at {it['where']} ({'fixed' if it['resolved'] else 'not fixed'})\n"
                     f"Ticket: {it['ticket']}\nWhat was changed:\n{changed}\n"
                     f"Why it broke things: {it['explanation']}\nHow to find it: {it['find']}")
    if rebuilt:
        parts.append("The engineer rebuilt the whole lab from scratch (wiped every device back to the healthy "
                     "baseline) instead of finding the faults.")
    elif changes:
        parts.append("What the engineer changed (unified diff per router, vs. right after the faults went in):\n"
                     + "\n\n".join(f"{d}:\n{diff}" for d, diff in changes.items()))
    else:
        parts.append("No configuration changes were recorded on the managed routers.")
    return _ask(_DEBRIEF_SYSTEM, "\n\n".join(parts), max_tokens=4000, effort="medium")
