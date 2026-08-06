"""Compliance gate for generated video ad copy.

A video script is client-facing housing advertising. Everything that reaches
a provider, the portal, or the Files library goes through here first:

    script / scene voiceover / on-screen overlay
                 │
                 ▼
      1. pricing rules   (video_pipeline_config.validate_script — hard,
                          always runs, strips pricing language)
      2. forbidden phrases (the property's own "Things NOT to Say")
      3. Fair Housing    (fair_housing_gate.check_fair_housing — hard
                          blocklist always runs; LLM nuance pass may degrade)
                 │
                 ▼
          ScriptGateResult

The Fair Housing pass can degrade (no API key, LLM error). When it does,
`degraded` is True and `fair_housing` reads "degraded" — NOT "passed". The
caller must surface that rather than implying a clean bill of health; a
degraded pass only ran the fixed-phrase blocklist. `describe_checks()`
produces the string that gets persisted onto the variant so the record on
HubSpot says what was actually checked.

Blocking is on by default (VIDEO_FAIR_HOUSING_BLOCK=false disables it) —
housing ad copy should not ship on a flag that defaults to permissive.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from video_pipeline_config import sanitize_script, validate_script

logger = logging.getLogger(__name__)


def _blocking_enabled() -> bool:
    """Read at call time so tests and Render config can flip it per-process."""
    return os.getenv("VIDEO_FAIR_HOUSING_BLOCK", "true").strip().lower() not in (
        "false", "0", "no", "off",
    )


@dataclass
class ScriptGateResult:
    """Outcome of screening one piece of ad copy."""

    ok: bool
    """False when the copy must not be sent to a provider or shown."""

    script: str
    """The sanitized copy (pricing language removed). Empty when unusable."""

    violations: list[dict] = field(default_factory=list)
    """Fair Housing violations, in fair_housing_gate's shape."""

    forbidden_used: list[str] = field(default_factory=list)
    """Property-specific "Things NOT to Say" phrases found in the copy."""

    pricing_removed: list[str] = field(default_factory=list)
    """Pricing phrases stripped by the hard pipeline rule."""

    errors: list[str] = field(default_factory=list)
    """Why `ok` is False."""

    degraded: bool = False
    """True when the Fair Housing nuance pass did NOT run."""

    degraded_reason: str = ""

    fair_housing: str = "passed"
    """One of: passed | degraded | violations | blocked."""

    def describe_checks(self) -> str:
        """One-line, honest summary of what actually ran. Persisted on the
        variant so a reviewer can never mistake a degraded pass for a clean
        one after the fact."""
        parts = [f"pricing={'stripped' if self.pricing_removed else 'clean'}"]
        parts.append(f"fair_housing={self.fair_housing}")
        if self.degraded:
            parts.append(f"fair_housing_degraded_reason={self.degraded_reason or 'unknown'}")
        if self.forbidden_used:
            parts.append(f"forbidden_phrases={len(self.forbidden_used)}")
        return " ".join(parts)


def _forbidden_hits(text: str, forbidden_phrases) -> list[str]:
    """Case-insensitive substring match of the property's banned phrases.

    Substring rather than word-boundary on purpose: these are hand-entered by
    the property team ("pet spa", "resort-style") and a near-miss is still
    worth flagging to a human.
    """
    low = (text or "").lower()
    hits = []
    for raw in forbidden_phrases or []:
        phrase = str(raw).strip()
        if len(phrase) < 3:
            continue
        if phrase.lower() in low:
            hits.append(phrase)
    return hits


def gate_script(
    script: str,
    *,
    field_name: str = "video_script",
    forbidden_phrases=(),
    enforce_min_length: bool = True,
) -> ScriptGateResult:
    """Screen one piece of copy. Never raises — inspect the result.

    `enforce_min_length` is the whole-script rule (a 15-second ad needs more
    than a few words). It must be False for fragments: a scene's on-screen
    overlay is meant to be ≤7 words and a per-scene voiceover chunk is one
    sentence, so applying the script minimum to them rejects correct copy.
    """
    validation = validate_script(script or "")
    cleaned = validation["cleaned_script"]
    _, pricing_removed = sanitize_script(script or "")

    if enforce_min_length and not validation["ok"]:
        return ScriptGateResult(
            ok=False,
            script=cleaned,
            errors=list(validation["errors"]),
            pricing_removed=pricing_removed,
            fair_housing="blocked",
            degraded=True,
            degraded_reason="script rejected before the Fair Housing pass ran",
        )

    forbidden_used = _forbidden_hits(cleaned, forbidden_phrases)

    try:
        from fair_housing_gate import check_fair_housing
        fh = check_fair_housing([{"field": field_name, "text": cleaned}])
    except Exception as exc:  # import or unexpected internal failure
        logger.error("video_script_gate: Fair Housing gate unavailable: %s", exc)
        blocked = _blocking_enabled()
        return ScriptGateResult(
            ok=not blocked,
            script=cleaned,
            errors=(["Fair Housing check could not run"] if blocked else []),
            forbidden_used=forbidden_used,
            pricing_removed=pricing_removed,
            degraded=True,
            degraded_reason=f"gate unavailable: {exc}",
            fair_housing="blocked" if blocked else "degraded",
        )

    violations = list(fh.get("violations") or [])
    degraded = bool(fh.get("degraded"))
    degraded_reason = fh.get("degraded_reason", "")

    if violations:
        blocked = _blocking_enabled()
        return ScriptGateResult(
            ok=not blocked,
            script=cleaned,
            violations=violations,
            errors=(
                [f"Fair Housing: {v.get('issue') or v.get('phrase')}" for v in violations]
                if blocked else []
            ),
            forbidden_used=forbidden_used,
            pricing_removed=pricing_removed,
            degraded=degraded,
            degraded_reason=degraded_reason,
            fair_housing="blocked" if blocked else "violations",
        )

    return ScriptGateResult(
        ok=True,
        script=cleaned,
        forbidden_used=forbidden_used,
        pricing_removed=pricing_removed,
        degraded=degraded,
        degraded_reason=degraded_reason,
        # No violations found, but say "degraded" when only the blocklist ran.
        fair_housing="degraded" if degraded else "passed",
    )


def gate_scene_plan(plan: list[dict], *, forbidden_phrases=()) -> ScriptGateResult:
    """Screen every on-screen overlay and scene voiceover in a plan.

    Overlays are burned into the video frame, so they are as client-facing as
    the voiceover and get the identical treatment. Returns a single combined
    result; `script` is unused (the scenes are screened in place, and the
    sanitized text is written back into `plan`).
    """
    texts = []
    for i, scene in enumerate(plan or []):
        if not isinstance(scene, dict):
            continue
        for key in ("voiceover_text", "on_screen_text"):
            value = (scene.get(key) or "").strip()
            if value:
                texts.append((i, key, value))

    if not texts:
        return ScriptGateResult(ok=True, script="")

    combined = ScriptGateResult(ok=True, script="")
    for idx, key, value in texts:
        result = gate_script(
            value,
            field_name=f"scene[{idx}].{key}",
            forbidden_phrases=forbidden_phrases,
            # Fragments, not scripts — overlays are ≤7 words by design.
            enforce_min_length=False,
        )
        plan[idx][key] = result.script
        combined.violations.extend(result.violations)
        combined.forbidden_used.extend(result.forbidden_used)
        combined.pricing_removed.extend(result.pricing_removed)
        combined.errors.extend(result.errors)
        if result.degraded:
            combined.degraded = True
            combined.degraded_reason = combined.degraded_reason or result.degraded_reason
        if not result.ok:
            combined.ok = False

    if not combined.ok:
        combined.fair_housing = "blocked"
    elif combined.violations:
        combined.fair_housing = "violations"
    elif combined.degraded:
        combined.fair_housing = "degraded"
    return combined
