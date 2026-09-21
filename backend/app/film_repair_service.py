from __future__ import annotations

REPAIR_TEMPLATES = {
    "identity": (
        "identity drift: Keep {entities} face shape, hair, eyes and age. "
        "Do not change facial structure, body type or canonical identity."
    ),
    "wardrobe": (
        "wardrobe drift: Keep the exact canonical wardrobe and accessories for {entities}. "
        "Do not change clothing color, layering or silhouette."
    ),
    "location": (
        "location drift: Stay inside canonical location {entities}. "
        "Preserve architecture, layout, lighting direction and time-of-day."
    ),
    "prop": (
        "prop missing/wrong: {entities} must remain visible with the correct owner/hand/state "
        "during the first 3 seconds and throughout the action."
    ),
    "boundary": (
        "boundary mismatch: Start the scene with the character at the same screen position, "
        "body orientation and scale as the previous accepted last frame."
    ),
    "camera": (
        "camera mismatch: Keep camera direction, shot size and screen direction consistent with the scene plan."
    ),
    "lighting": (
        "lighting mismatch: Preserve established lighting, color temperature and time-of-day."
    ),
    "audio": (
        "audio missing: The scene requires audible dialogue/voiceover. Do not generate a silent clip."
    ),
    "speech": (
        "speech mismatch: Deliver the exact scene dialogue/voiceover with the correct speaker visible."
    ),
}


def _entity_label(scene: dict, kind: str) -> str:
    if kind == "identity" or kind == "wardrobe":
        ids = scene.get("characters") or []
        return ", ".join(str(x) for x in ids) or "the locked character"
    if kind == "location":
        return str(scene.get("location_id") or "the locked location")
    if kind == "prop":
        ids = scene.get("props_present") or []
        return ", ".join(str(x) for x in ids) or "required props"
    return kind


def failed_dimensions(qc_v2: dict) -> list[str]:
    report = qc_v2.get("qc") if isinstance(qc_v2.get("qc"), dict) else qc_v2
    dims = report.get("dimensions") if isinstance(report.get("dimensions"), dict) else {}
    failed = [name for name, item in dims.items() if isinstance(item, dict) and item.get("passed") is False]
    hard = report.get("hard_gate") if isinstance(report.get("hard_gate"), dict) else {}
    for name in hard.get("failed") or []:
        if name not in failed:
            failed.append(name)
    return failed


def build_repair_prompt(scene: dict, qc_v2: dict, base_prompt: str) -> dict:
    failed = failed_dimensions(qc_v2)
    lines = []
    for name in failed:
        template = REPAIR_TEMPLATES.get(name)
        if not template:
            continue
        lines.append(template.format(entities=_entity_label(scene, name)))
    issues = ((qc_v2.get("qc") or {}).get("issues") if isinstance(qc_v2.get("qc"), dict) else None) or []
    for issue in issues[:6]:
        if not isinstance(issue, dict):
            continue
        evidence = str(issue.get("evidence") or "").strip()
        expected = str(issue.get("expected") or "").strip()
        if evidence or expected:
            lines.append(f"{issue.get('type') or 'issue'}: {expected or evidence}")
    repair_block = "\n".join(f"- {line}" for line in lines) or "- Keep canonical identity, wardrobe, location and previous last-frame continuity."

    # Repair constraints must lead the provider prompt.  Current scene state is
    # authoritative and overrides generic character habits/signature traits
    # whenever they conflict (for example, a character who often carries a box
    # must still have empty hands in a scene whose START_STATE says the box was
    # left behind).
    state_lines = [
        "- CURRENT SCENE START_STATE / END_STATE override generic character habits, signature traits and movement habits when they conflict."
    ]
    start_state = str(scene.get("start_state") or "").strip()
    end_state = str(scene.get("end_state") or "").strip()
    if start_state:
        state_lines.append(f"- CURRENT START_STATE: {start_state}")
    if end_state:
        state_lines.append(f"- CURRENT END_STATE: {end_state}")
    if not (scene.get("props_present") or []):
        state_lines.append(
            "- PROP OVERRIDE: Do not invent, carry or hold any handheld prop/object in this scene. "
            "Keep hands empty/natural except fixed wearable accessories explicitly defined by character canon."
        )

    prompt = (
        "REPAIR CONSTRAINTS — HIGHEST PRIORITY (must follow before the original prompt):\n"
        + "\n".join(state_lines)
        + "\n"
        + repair_block
        + "\n\nORIGINAL LOCKED SCENE PROMPT (preserve story content unless overridden above):\n"
        + (base_prompt or "").rstrip()
    )
    return {
        "failed_dimensions": failed,
        "repair_lines": lines,
        "prompt": prompt,
    }


def rank_tuple(hard_gates_passed: bool, dimensions_passed: int, overall_score: float | None) -> tuple:
    return (
        1 if hard_gates_passed else 0,
        int(dimensions_passed or 0),
        float(overall_score or 0),
    )


def is_better_candidate(new: dict, current: dict | None) -> bool:
    if not current:
        return True
    return rank_tuple(
        bool(new.get("hard_gates_passed")),
        int(new.get("dimensions_passed") or 0),
        new.get("overall_score"),
    ) > rank_tuple(
        bool(current.get("hard_gates_passed")),
        int(current.get("dimensions_passed") or 0),
        current.get("overall_score"),
    )
