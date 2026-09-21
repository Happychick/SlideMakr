"""SlideMakr — the quality-rules registry: the ONE source of "looks good"
rules, consumed both by the agent prompt (`RuleSet.render_for_prompt`) and by
the eval (`RuleSet.score`).

A `Rule` is data, never code: `check_id` points into `CHECKS`, a registry of
small `(state, slide_index, params) -> 0..1` scorers built on top of the
existing deterministic checks in `app.layout_quality` and `app.edit_assess`.
Persisted user rules (from feedback) are the same shape -- `check_id` +
`params` -- so a saved preference is just a new `Rule`, not new code.

`placeholders_untouched` (verifying a user's template placeholders were not
moved/restyled) needs the original template to diff against, which this
module has no access to -- it stays prompt-only for now (see evals/README.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    id: str                        # "no_overlap" | "user:headings_green"
    text: str                      # imperative prompt line
    scope: str                     # "universal" | "user"
    check_id: Optional[str]        # key into CHECKS, or None = prompt + vision only
    params: Dict[str, Any] = field(default_factory=dict)
    hard: bool = False             # hard = agent must fix before it declares done
    source: str = "default"        # "default" | "feedback:<iso ts>"

    def __post_init__(self) -> None:
        if self.check_id is not None and self.check_id not in CHECKS:
            raise ValueError(
                f"unknown check_id {self.check_id!r} for rule {self.id!r}; "
                f"known: {sorted(CHECKS)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "scope": self.scope,
            "check_id": self.check_id,
            "params": dict(self.params),
            "hard": self.hard,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Rule":
        return cls(
            id=d["id"],
            text=d["text"],
            scope=d.get("scope", "user"),
            check_id=d.get("check_id"),
            params=dict(d.get("params") or {}),
            hard=bool(d.get("hard", False)),
            source=d.get("source", "default"),
        )


# ---------------------------------------------------------------------------
# CHECKS registry -- each: (state, slide_index, params) -> 0..1
# ---------------------------------------------------------------------------

def _slide(state: dict, slide_index: int) -> dict:
    slides = (state or {}).get("slides", [])
    return slides[slide_index] if 0 <= slide_index < len(slides) else {}


def _check_overlap(state: dict, slide_index: int, params: dict) -> float:
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    return lq.overlap_score(els) if els else 1.0


def _check_fit(state: dict, slide_index: int, params: dict) -> float:
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    return lq.fits_page_score(els) if els else 1.0


def _check_one_font(state: dict, slide_index: int, params: dict) -> float:
    from . import layout_quality as lq
    return lq.font_consistency_score(state)


def _check_balanced(state: dict, slide_index: int, params: dict) -> float:
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    return lq.balance_score(els)


def _check_has_title(state: dict, slide_index: int, params: dict) -> float:
    from . import edit_assess as ea
    return 1.0 if ea.check_has_title(state, slide_index) >= 1.0 else 0.7


def _is_heading(el: Dict[str, Any]) -> bool:
    """Delegates to `edit_assess.is_heading_element` so this and
    `check_has_title` cannot diverge (e.g. both skip node_/edge_ labels).
    """
    from . import edit_assess as ea
    return ea.is_heading_element(el)


def _check_image_present(state: dict, slide_index: int, params: dict) -> float:
    """1.0 if the slide has an image element. `params["only_content_slides"]`
    exempts slide index 0 (the title slide) -> 1.0 automatically.
    """
    if params.get("only_content_slides") and slide_index == 0:
        return 1.0
    els = _slide(state, slide_index).get("elements", [])
    return 1.0 if any(e.get("type") == "image" for e in els) else 0.0


def _check_text_color(state: dict, slide_index: int, params: dict) -> float:
    """Fraction of text elements in `params["role"]` ("headings"|"body") whose
    `text_color` matches `params["hex"]`, case-insensitively. No elements in
    that role -> 1.0 (nothing to violate).
    """
    role = params.get("role")
    want = str(params.get("hex", "")).strip().lower()
    els = [e for e in _slide(state, slide_index).get("elements", []) if e.get("text")]
    if role == "headings":
        in_role = [e for e in els if _is_heading(e)]
    else:
        in_role = [e for e in els if not _is_heading(e)]
    if not in_role:
        return 1.0
    matched = sum(1 for e in in_role if str(e.get("text_color", "")).strip().lower() == want)
    return round(matched / len(in_role), 4)


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text or "")


def _check_text_style(state: dict, slide_index: int, params: dict) -> float:
    """Fraction of text elements containing a digit ("numbers") whose style is
    bold, when `params["target"] == "numbers"` and `params["bold"]` is true.

    Slide state does not currently expose a bold flag on elements; if none of
    the number-bearing elements carry a `bold` key at all, we cannot judge
    this deterministically and return 1.0 (prompt + vision judge it instead).
    """
    target = params.get("target", "numbers")
    els = [e for e in _slide(state, slide_index).get("elements", []) if e.get("text")]
    if target == "numbers":
        candidates = [e for e in els if _has_digit(str(e.get("text", "")))]
    else:
        candidates = els
    if not candidates:
        return 1.0
    if not any("bold" in e for e in candidates):
        return 1.0
    want_bold = bool(params.get("bold", True))
    matched = sum(1 for e in candidates if bool(e.get("bold")) == want_bold)
    return round(matched / len(candidates), 4)


def _check_brand_match(state: dict, slide_index: int, params: dict) -> float:
    from . import layout_quality as lq
    colors = params.get("colors") or []
    els = _slide(state, slide_index).get("elements", [])
    deck_colors = [e["fill_color"] for e in els if e.get("fill_color")]
    deck_colors += [e["text_color"] for e in els if e.get("text_color")]
    return lq.brand_match_score(deck_colors, colors)


CHECKS: Dict[str, Callable[[dict, int, dict], float]] = {
    "overlap": _check_overlap,
    "fit": _check_fit,
    "one_font": _check_one_font,
    "balanced": _check_balanced,
    "has_title": _check_has_title,
    "image_present": _check_image_present,
    "text_color": _check_text_color,
    "text_style": _check_text_style,
    "brand_match": _check_brand_match,
}


# ---------------------------------------------------------------------------
# Defaults -- imperative one-liners lifted from
# app/skills/google_slides.md:126-142 ("Anti-patterns" / "Quality bar").
# ---------------------------------------------------------------------------

DEFAULT_RULES: List[Rule] = [
    Rule(
        id="no_overlap",
        text="Never overlap elements -- check positions from get_presentation_state first.",
        scope="universal",
        check_id="overlap",
        hard=True,
    ),
    Rule(
        id="fits_page",
        text="Never place content beyond slide bounds (x > 9,144,000 or y > 5,143,500).",
        scope="universal",
        check_id="fit",
        hard=True,
    ),
    Rule(
        id="one_font",
        text="Fonts match the deck; titles large (28-36pt), body readable (16-18pt).",
        scope="universal",
        check_id="one_font",
        hard=True,
    ),
    Rule(
        id="balanced",
        text="Content is centered/balanced, not bunched on one side with dead space.",
        scope="universal",
        check_id="balanced",
        hard=False,
    ),
    Rule(
        id="has_title",
        text="Every content slide has a clear title.",
        scope="universal",
        check_id="has_title",
        hard=False,
    ),
    Rule(
        id="charts_titled",
        text="Every added component (flowchart/chart) has a title/label.",
        scope="universal",
        check_id=None,
        hard=False,
    ),
]


# ---------------------------------------------------------------------------
# Geometry hard-failure thresholds -- same numbers as today's controller gate
# (app/edit_runner.py's overlap/fit check).
# ---------------------------------------------------------------------------

_OVERLAP_FAIL_AT = 0.3   # score <= this counts as a hard failure
_FIT_FAIL_BELOW = 0.99   # score < this counts as a hard failure


def _is_hard_failure(rule: Rule, score: float) -> bool:
    if rule.check_id == "overlap":
        return score <= _OVERLAP_FAIL_AT
    if rule.check_id == "fit":
        return score < _FIT_FAIL_BELOW
    return score < 1.0


# ---------------------------------------------------------------------------
# RuleSet
# ---------------------------------------------------------------------------

class RuleSet:
    def __init__(self, rules: Optional[List[Rule]] = None):
        self.rules: List[Rule] = list(rules) if rules is not None else list(DEFAULT_RULES)

    # -- serialization --------------------------------------------------

    @classmethod
    def from_dicts(cls, dicts: List[Dict[str, Any]], strict: bool = True) -> "RuleSet":
        """Build a RuleSet from persisted dicts. `strict=True` (default) lets an
        unknown `check_id` raise (Rule.from_dict validates it). `strict=False`
        drops any such rule and logs a warning instead — the mode the db
        loader uses so one bad persisted rule can't break a whole request.
        """
        rules: List[Rule] = []
        for d in dicts:
            if strict:
                rules.append(Rule.from_dict(d))
                continue
            try:
                rules.append(Rule.from_dict(d))
            except ValueError:
                _LOG.warning(
                    "dropping rule %r: unknown check_id %r",
                    d.get("id"), d.get("check_id"),
                )
        return cls(rules)

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules]

    # -- prompt rendering -------------------------------------------------

    def render_for_prompt(self) -> str:
        universal = [r for r in self.rules if r.scope == "universal"]
        user = [r for r in self.rules if r.scope == "user"]
        lines = ["## Quality rules -- you are scored on exactly these"]
        for r in universal:
            lines.append(f"- {r.text}")
        if user:
            lines.append("")
            lines.append("### This user's preferences")
            for r in user:
                lines.append(f"- {r.text}")
        return "\n".join(lines)

    # -- scoring ----------------------------------------------------------

    def score(self, state: dict, slide_index: int) -> Dict[str, Dict[str, float]]:
        result: Dict[str, Dict[str, float]] = {"universal": {}, "user": {}}
        for r in self.rules:
            if r.check_id is None:
                continue  # prompt-only: judged by the vision pass, not scored here
            check = CHECKS.get(r.check_id)
            if check is None:
                continue
            bucket = "universal" if r.scope == "universal" else "user"
            result[bucket][r.id] = check(state, slide_index, r.params)
        return result

    def looks_good(self, state: dict, slide_index: int) -> Tuple[float, float]:
        scores = self.score(state, slide_index)
        baseline = 1.0
        for v in scores["universal"].values():
            baseline *= v
        user = 1.0
        for v in scores["user"].values():
            user *= v
        return round(baseline, 4), round(user, 4)

    def hard_failures(self, state: dict, slide_index: int) -> List[Rule]:
        failures: List[Rule] = []
        for r in self.rules:
            if not r.hard or r.check_id is None:
                continue
            check = CHECKS.get(r.check_id)
            if check is None:
                continue
            score = check(state, slide_index, r.params)
            if _is_hard_failure(r, score):
                failures.append(r)
        return failures


# ---------------------------------------------------------------------------
# Loading a user's effective rule set (defaults + persisted user rules)
# ---------------------------------------------------------------------------

def default_rule_set() -> "RuleSet":
    """The universal-only RuleSet (no persisted user rules) -- used to build the
    module-level agent instructions, which stay on defaults regardless of caller.
    """
    return RuleSet(list(DEFAULT_RULES))


def load_rule_set(user_id: Optional[str]) -> "RuleSet":
    """Build the effective RuleSet for a request: defaults plus this user's
    persisted rules (if any). A corrupt stored rule (e.g. an unknown
    check_id from a stale/edited record) is dropped with a warning rather
    than breaking the whole request -- see `RuleSet.from_dicts(strict=False)`.
    """
    if not user_id:
        return RuleSet(list(DEFAULT_RULES))

    from . import db  # lazy import: avoid a db <-> quality_rules import cycle

    user_dicts = db.get_user_rules(user_id)
    user_rules = RuleSet.from_dicts(user_dicts, strict=False).rules
    return RuleSet(list(DEFAULT_RULES) + user_rules)
