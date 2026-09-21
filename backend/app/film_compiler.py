import hashlib
import json
import math
import re
from copy import deepcopy

from .film_voice_profile_store import default_narrator_profile, default_voice_profile


def _hash(value) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sentences(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [x.strip() for x in parts if x.strip()]


def _clauses(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?…;])\s+|\s+—\s+|\n+", text)
    return [x.strip() for x in parts if x.strip()]


def _distribute(items: list, count: int) -> list[list]:
    buckets = [[] for _ in range(max(1, count))]
    if not items:
        return buckets
    for index, item in enumerate(items):
        bucket = min(count - 1, int(index * count / len(items)))
        buckets[bucket].append(item)
    return buckets


def _word_seconds(text: str, words_per_second: float = 2.6) -> float:
    words = len(re.findall(r"\S+", str(text or "")))
    return words / words_per_second if words else 0.0


def build_duration_budget(scene: dict, settings: dict | None = None) -> dict:
    settings = settings or {}
    declared = max(1.0, float(scene.get("duration") or settings.get("scene_duration") or 8))
    provider_max = float(
        settings.get("provider_shot_max_seconds")
        or settings.get("shot_max_seconds")
        or min(max(float(settings.get("scene_duration") or 8), 4.0), 12.0)
    )
    provider_max = max(2.0, min(provider_max, 20.0))

    dialogue_seconds = sum(_word_seconds(x.get("text") or "") for x in (scene.get("dialogue") or []) if isinstance(x, dict))
    voiceover_seconds = _word_seconds(scene.get("voiceover") or "")
    speech_seconds = dialogue_seconds + voiceover_seconds
    action_clauses = len(_clauses(scene.get("action") or scene.get("source_text") or ""))
    action_floor = min(declared, max(2.0, action_clauses * 1.1)) if action_clauses else 1.0

    required = max(action_floor, speech_seconds)
    passed = required <= declared + 0.35
    shot_count = max(1, math.ceil(declared / provider_max))
    return {
        "declared_seconds": round(declared, 3),
        "required_seconds": round(required, 3),
        "dialogue_seconds": round(dialogue_seconds, 3),
        "voiceover_seconds": round(voiceover_seconds, 3),
        "provider_shot_max_seconds": round(provider_max, 3),
        "shot_count": shot_count,
        "passed": passed,
        "error_code": None if passed else "DURATION_BUDGET_EXCEEDED",
    }


def build_shot_plan(scene: dict, settings: dict | None = None) -> tuple[list[dict], dict]:
    budget = build_duration_budget(scene, settings)
    count = int(budget["shot_count"])
    total = float(budget["declared_seconds"])
    base = total / count

    action_parts = _distribute(_clauses(scene.get("action") or scene.get("source_text") or ""), count)
    dialogue_parts = _distribute([deepcopy(x) for x in (scene.get("dialogue") or []) if isinstance(x, dict)], count)
    voice_parts = _distribute(_sentences(scene.get("voiceover") or ""), count)

    shots = []
    allocated = 0.0
    for index in range(count):
        shot_id = f"{scene.get('id')}_SHOT_{index + 1:03d}"
        duration = round(base, 3) if index < count - 1 else round(total - allocated, 3)
        allocated += duration
        action = " ".join(action_parts[index]).strip() or str(scene.get("action") or "")
        voice = " ".join(voice_parts[index]).strip()
        shots.append({
            "id": shot_id,
            "scene_id": scene.get("id"),
            "shot_index": index + 1,
            "duration": duration,
            "action": action,
            "camera": scene.get("camera") or "",
            "dialogue": dialogue_parts[index],
            "voiceover": voice,
            "start_state": scene.get("start_state") if index == 0 else f"Continue exact state from {scene.get('id')}_SHOT_{index:03d}",
            "end_state": scene.get("end_state") if index == count - 1 else f"Carry exact state into {scene.get('id')}_SHOT_{index + 2:03d}",
            "characters": list(scene.get("characters") or []),
            "location_id": scene.get("location_id"),
            "props_present": list(scene.get("props_present") or []),
        })
    return shots, budget


def compute_source_spans(original_text: str, scenes: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    spans = {}
    errors = []
    cursor = 0
    raw = str(original_text or "")
    for scene in scenes:
        sid = str(scene.get("id") or "")
        excerpt = str(scene.get("source_text") or scene.get("action") or "").strip()
        if not excerpt:
            errors.append({"scene": sid, "code": "SOURCE_SPAN_MISSING", "detail": "Scene has no source excerpt/action."})
            continue
        pos = raw.find(excerpt, cursor)
        if pos < 0:
            # Source can include formatting around the action. Search globally to distinguish reordering from missing source.
            global_pos = raw.find(excerpt)
            if global_pos >= 0 and global_pos < cursor:
                errors.append({"scene": sid, "code": "TIMELINE_SOURCE_REORDERED", "detail": f"Source appears before cursor {cursor}."})
            else:
                errors.append({"scene": sid, "code": "SOURCE_EXCERPT_NOT_FOUND", "detail": excerpt[:220]})
            continue
        end = pos + len(excerpt)
        spans[sid] = {"start": pos, "end": end, "hash": _hash(excerpt)}
        cursor = end
    return spans, errors


def build_boundary_context(bible: dict, accepted_scenes: list[dict]) -> dict:
    previous = accepted_scenes[-1] if accepted_scenes else None
    recent = accepted_scenes[-2:] if accepted_scenes else []
    recent_for_hash = accepted_scenes[-12:] if accepted_scenes else []

    prop_current_owner = {}
    prop_history = []
    for prop in bible.get("props") or []:
        if not isinstance(prop, dict) or not prop.get("id"):
            continue
        owner = prop.get("initial_owner") or prop.get("owner_initial") or prop.get("owner")
        prop_current_owner[str(prop["id"])] = owner

    for scene in accepted_scenes:
        for transfer in scene.get("prop_transfers") or []:
            if not isinstance(transfer, dict) or not transfer.get("prop_id"):
                continue
            pid = str(transfer.get("prop_id"))
            source = transfer.get("source_owner")
            target = transfer.get("target_owner")
            prop_history.append({
                "scene_id": scene.get("id"),
                "prop_id": pid,
                "source_owner": source,
                "target_owner": target,
                "moment": transfer.get("moment"),
            })
            if target:
                prop_current_owner[pid] = target

    recent_beat_hashes = [
        {
            "scene_id": scene.get("id"),
            "beat_hash": _hash(_norm(scene.get("action") or scene.get("source_text") or "")),
        }
        for scene in recent_for_hash
        if _norm(scene.get("action") or scene.get("source_text") or "")
    ]
    recent_source_hashes = [
        {
            "scene_id": scene.get("id"),
            "source_hash": _hash(str(scene.get("source_text") or "").strip()),
        }
        for scene in recent_for_hash
        if str(scene.get("source_text") or "").strip()
    ]
    history_fingerprint = _hash([
        {
            "scene_id": scene.get("id"),
            "action": _norm(scene.get("action") or ""),
            "end_state": _norm(scene.get("end_state") or ""),
            "transfers": scene.get("prop_transfers") or [],
        }
        for scene in accepted_scenes
    ])

    return {
        "previous_scene_id": previous.get("id") if previous else None,
        "previous_end_state": previous.get("end_state") if previous else None,
        "previous_location_id": previous.get("location_id") if previous else None,
        "previous_characters": previous.get("characters") if previous else [],
        "previous_props_present": previous.get("props_present") if previous else [],
        "recent_events": [
            {
                "scene_id": item.get("id"),
                "action": item.get("action"),
                "end_state": item.get("end_state"),
                "prop_transfers": item.get("prop_transfers") or [],
            }
            for item in recent
        ],
        "recent_beat_hashes": recent_beat_hashes,
        "recent_source_hashes": recent_source_hashes,
        "prop_transfer_history": prop_history,
        "current_prop_owners": prop_current_owner,
        "history_fingerprint": history_fingerprint,
        "accepted_scene_count": len(accepted_scenes),
        "canon_ids": {
            "characters": [x.get("id") for x in (bible.get("characters") or []) if isinstance(x, dict)],
            "locations": [x.get("id") for x in (bible.get("locations") or []) if isinstance(x, dict)],
            "props": [x.get("id") for x in (bible.get("props") or []) if isinstance(x, dict)],
        },
    }


def merge_ai_scene_batch(
    raw_batch: list[dict],
    section_text: str,
    start_index: int,
    bible: dict,
    boundary_context: dict | None = None,
) -> list[dict]:
    if not isinstance(raw_batch, list) or not raw_batch:
        raise ValueError("STORYBOARD_BATCH_EMPTY")

    boundary_context = boundary_context or {}
    char_ids = {str(x.get("id")) for x in (bible.get("characters") or []) if isinstance(x, dict) and x.get("id")}
    loc_ids = {str(x.get("id")) for x in (bible.get("locations") or []) if isinstance(x, dict) and x.get("id")}
    prop_ids = {str(x.get("id")) for x in (bible.get("props") or []) if isinstance(x, dict) and x.get("id")}

    cursor = 0
    result = []
    previous_id = boundary_context.get("previous_scene_id")
    previous_end = boundary_context.get("previous_end_state") or ""
    recent_source_hashes = {
        str(item.get("source_hash"))
        for item in (boundary_context.get("recent_source_hashes") or [])
        if isinstance(item, dict) and item.get("source_hash")
    }
    current_prop_owners = dict(boundary_context.get("current_prop_owners") or {})

    for offset, incoming in enumerate(raw_batch):
        if not isinstance(incoming, dict):
            raise ValueError(f"STORYBOARD_SCENE_SCHEMA:{offset}")
        sid = f"SCENE_{start_index + offset:04d}" if start_index + offset > 999 else f"SCENE_{start_index + offset:03d}"
        excerpt = str(incoming.get("source_text") or "").strip()
        if not excerpt:
            raise ValueError(f"SOURCE_EXCERPT_MISSING:{sid}")
        pos = section_text.find(excerpt, cursor)
        if pos < 0:
            raise ValueError(f"SOURCE_EXCERPT_NOT_VERBATIM:{sid}")
        excerpt_hash = _hash(excerpt)
        if excerpt_hash in recent_source_hashes:
            raise ValueError(f"DUPLICATE_CHUNK_SOURCE:{sid}")
        cursor = pos + len(excerpt)

        characters = [str(x) for x in (incoming.get("characters") or [])]
        unknown_chars = [x for x in characters if x not in char_ids]
        if unknown_chars:
            raise ValueError(f"UNKNOWN_CHARACTER:{sid}:{','.join(unknown_chars)}")

        location_id = incoming.get("location_id")
        if location_id and str(location_id) not in loc_ids:
            raise ValueError(f"UNKNOWN_LOCATION:{sid}:{location_id}")

        dialogue = deepcopy(incoming.get("dialogue") or [])
        for line in dialogue:
            if not isinstance(line, dict):
                raise ValueError(f"DIALOGUE_SCHEMA:{sid}")
            speaker = line.get("character_id")
            text = str(line.get("text") or "").strip()
            if speaker and speaker not in characters:
                raise ValueError(f"DIALOGUE_SPEAKER_OFFSCREEN:{sid}:{speaker}")
            if text and text not in excerpt:
                raise ValueError(f"DIALOGUE_NOT_IN_SOURCE_EXCERPT:{sid}")

        voiceover = str(incoming.get("voiceover") or "").strip()
        if voiceover and voiceover not in excerpt:
            raise ValueError(f"VOICEOVER_NOT_IN_SOURCE_EXCERPT:{sid}")

        explicit_props = [str(x) for x in (incoming.get("props_present") or [])]
        unknown_explicit_props = [x for x in explicit_props if x not in prop_ids]
        if unknown_explicit_props:
            raise ValueError(f"UNKNOWN_PROP:{sid}:{','.join(unknown_explicit_props)}")

        prop_candidates = sorted(set(explicit_props + re.findall(r"PROP_\d+", " ".join([
            excerpt,
            str(incoming.get("action") or ""),
            str(incoming.get("start_state") or ""),
            str(incoming.get("end_state") or ""),
        ]))))
        props_present = [x for x in prop_candidates if x in prop_ids]

        transfers = deepcopy(incoming.get("prop_transfers") or [])
        allowed_owner_ids = char_ids | loc_ids | {f"INSIDE_{pid}" for pid in prop_ids}
        for transfer in transfers:
            if not isinstance(transfer, dict):
                raise ValueError(f"PROP_TRANSFER_SCHEMA:{sid}")
            pid = str(transfer.get("prop_id") or "")
            if not pid or pid not in prop_ids:
                raise ValueError(f"UNKNOWN_PROP_TRANSFER:{sid}:{pid or 'MISSING'}")
            for field in ("source_owner", "target_owner"):
                owner = transfer.get(field)
                if owner in (None, ""):
                    continue
                if str(owner) not in allowed_owner_ids:
                    raise ValueError(f"UNKNOWN_PROP_OWNER:{sid}:{field}:{owner}")
            expected_owner = current_prop_owners.get(pid)
            source_owner = transfer.get("source_owner")
            target_owner = transfer.get("target_owner")
            if expected_owner and source_owner and str(source_owner) != str(expected_owner):
                raise ValueError(
                    f"PROP_OWNER_MISMATCH_BATCH:{sid}:{pid}:expected={expected_owner}:got={source_owner}"
                )
            if target_owner:
                current_prop_owners[pid] = target_owner

        action = str(incoming.get("action") or "").strip()
        # AI may not rewrite source facts. If action is not verbatim within excerpt, source excerpt becomes the action.
        if not action or action not in excerpt:
            action = excerpt

        start_state = str(incoming.get("start_state") or "").strip()
        if not start_state and previous_end:
            start_state = previous_end

        continuity = incoming.get("continuity") if isinstance(incoming.get("continuity"), dict) else {}
        continuity = {
            **continuity,
            "previous_scene": previous_id,
            "notes": continuity.get("notes") or (
                "Opening scene." if previous_id is None else f"Direct continuation of {previous_id}; source facts are immutable."
            ),
        }

        scene = {
            "id": sid,
            "title": str(incoming.get("title") or sid),
            "source_text": excerpt,
            "summary": str(incoming.get("summary") or excerpt[:280]),
            "duration": max(1.0, min(float(incoming.get("duration") or 8), 60.0)),
            "characters": characters,
            "location_id": location_id,
            "props_present": props_present,
            "prop_transfers": transfers,
            "action": action,
            "camera": str(incoming.get("camera") or ""),
            "lighting": str(incoming.get("lighting") or ""),
            "atmosphere": str(incoming.get("atmosphere") or ""),
            "voiceover": voiceover,
            "dialogue": dialogue,
            "start_state": start_state,
            "end_state": str(incoming.get("end_state") or ""),
            "continuity": continuity,
            "visual_prompt": str(incoming.get("visual_prompt") or ""),
            "warnings": [],
            "merge_audit": {
                "assigned_id": sid,
                "source_offset_in_batch": pos,
                "source_hash": _hash(excerpt),
                "model_id_ignored": incoming.get("id"),
                "allowed_source_facts": True,
            },
        }
        result.append(scene)
        previous_id = sid
        previous_end = scene["end_state"] or previous_end

    return result


def compile_flow_prompt(
    scene: dict,
    visual_style: str,
    characters: list[dict],
    locations: list[dict],
    props: list[dict],
    shot: dict | None = None,
) -> tuple[str, dict]:
    char_map = {str(x.get("id")): x for x in characters if isinstance(x, dict) and x.get("id")}
    loc_map = {str(x.get("id")): x for x in locations if isinstance(x, dict) and x.get("id")}
    prop_map = {str(x.get("id")): x for x in props if isinstance(x, dict) and x.get("id")}

    unit = shot or scene
    char_refs = [char_map[x] for x in (unit.get("characters") or scene.get("characters") or []) if x in char_map]
    loc_id = unit.get("location_id") or scene.get("location_id")
    loc = loc_map.get(str(loc_id)) if loc_id else None
    prop_refs = [prop_map[x] for x in (unit.get("props_present") or scene.get("props_present") or []) if x in prop_map]

    action = str(unit.get("action") or scene.get("action") or scene.get("source_text") or "")
    dialogue = unit.get("dialogue") if shot is not None else scene.get("dialogue")
    dialogue = dialogue or []
    dialogue_speaker_ids = []
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        cid = str(
            item.get("speaker_character_id")
            or item.get("character_id")
            or item.get("speaker_id")
            or ""
        ).strip()
        if cid and cid in char_map and cid not in dialogue_speaker_ids:
            dialogue_speaker_ids.append(cid)
    voice_refs = []
    for cid in dialogue_speaker_ids:
        profile = default_voice_profile(char_map[cid])
        profile["voice_profile_id"] = f"VOICE_{cid}"
        voice_refs.append(profile)
    voiceover_value = unit.get("voiceover") if shot is not None else scene.get("voiceover")
    voiceover = str(voiceover_value or "")
    if voiceover:
        narrator_profile = default_narrator_profile()
        if not any(ref.get("voice_profile_id") == "VOICE_NARRATOR" for ref in voice_refs):
            voice_refs.append(narrator_profile)
    start_state = str(unit.get("start_state") or scene.get("start_state") or "")
    end_state = str(unit.get("end_state") or scene.get("end_state") or "")
    camera = str(unit.get("camera") or scene.get("camera") or "")
    lighting = str(unit.get("lighting") or scene.get("lighting") or "")
    atmosphere = str(unit.get("atmosphere") or scene.get("atmosphere") or "")

    sections = [
        ("UNIT", str(unit.get("id") or scene.get("id") or "")),
        ("DURATION_SECONDS", str(unit.get("duration") or scene.get("duration") or "")),
        ("VISUAL_STYLE", str(visual_style or "Cinematic")),
        ("CHARACTER_CANON", json.dumps(char_refs, ensure_ascii=False, sort_keys=True)),
        ("LOCATION_CANON", json.dumps(loc or {}, ensure_ascii=False, sort_keys=True)),
        ("PROP_CANON", json.dumps(prop_refs, ensure_ascii=False, sort_keys=True)),
        ("VOICE_CANON", json.dumps(voice_refs, ensure_ascii=False, sort_keys=True)),
        ("SOURCE_ACTION_IMMUTABLE", action),
        ("EXACT_DIALOGUE", json.dumps(dialogue, ensure_ascii=False, sort_keys=True)),
        ("EXACT_VOICEOVER", voiceover),
        ("START_STATE", start_state),
        ("END_STATE", end_state),
        ("CAMERA", camera),
        ("LIGHTING", lighting),
        ("ATMOSPHERE", atmosphere),
        (
            "AUDIO_POLICY",
            "If dialogue exists: speaker visible, exact spoken text, synchronized lips. "
            "For every character, preserve the exact same voice identity defined in VOICE_CANON across all scenes: "
            "same timbre, vocal age, gender presentation, pitch range, cadence, accent and speaking style. "
            "Do not invent a new voice between scenes. Preserve ambience and continuity.",
        ),
        ("CONTINUITY_POLICY", "Do not invent, delete, repeat, reorder or reverse story events. Preserve identity, wardrobe, location layout, prop ownership/state and previous visual state."),
        ("NEGATIVE", "No visual identity drift, no voice identity drift, no wardrobe drift, no location drift, duplicated props, teleportation, impossible ownership, text artifacts, unintended subtitles."),
    ]
    prompt = "\n\n".join(f"[{name}]\n{value}" for name, value in sections)
    meta = {
        "compiler": "deterministic_flow_prompt_v4_narrator_lock",
        "prompt_hash": _hash(prompt),
        "scene_id": scene.get("id"),
        "shot_id": shot.get("id") if shot else None,
        "character_ids": [x.get("id") for x in char_refs],
        "location_id": loc_id,
        "prop_ids": [x.get("id") for x in prop_refs],
    }
    return prompt, meta


def validate_flow_prompt(scene: dict, prompt: str, meta: dict, shot: dict | None = None) -> list[dict]:
    issues = []
    unit = shot or scene
    sid = str(scene.get("id") or "")
    if meta.get("prompt_hash") != _hash(prompt):
        issues.append({"scene": sid, "code": "FLOW_PROMPT_HASH_MISMATCH", "detail": str(unit.get("id") or sid)})

    voice_value = unit.get("voiceover") if shot is not None else scene.get("voiceover")
    required = [
        str(unit.get("action") or scene.get("action") or scene.get("source_text") or ""),
        str(voice_value or ""),
    ]
    for line in (unit.get("dialogue") if shot is not None else scene.get("dialogue") or []):
        if isinstance(line, dict):
            required.append(str(line.get("text") or ""))
            if line.get("character_id"):
                required.append(str(line["character_id"]))
    required.extend(str(x) for x in (unit.get("characters") or scene.get("characters") or []))
    if unit.get("location_id") or scene.get("location_id"):
        required.append(str(unit.get("location_id") or scene.get("location_id")))
    required.extend(str(x) for x in (unit.get("props_present") or scene.get("props_present") or []))

    for value in required:
        if value and value not in prompt:
            issues.append({"scene": sid, "code": "FLOW_PROMPT_FIDELITY", "detail": value[:180]})
    return issues


def attach_batch_b_compilation(
    original_text: str,
    result: dict,
    settings: dict | None = None,
) -> dict:
    settings = settings or {}
    scenes = result.get("scenes") or []
    spans, span_errors = compute_source_spans(original_text, scenes)
    errors = list((result.get("integrity") or {}).get("errors") or [])
    warnings = list((result.get("integrity") or {}).get("warnings") or [])
    errors.extend(span_errors)

    prompt_errors = []
    duration_errors = []
    for scene in scenes:
        sid = scene.get("id")
        scene["source_span"] = spans.get(str(sid), {})
        shots, budget = build_shot_plan(scene, settings)
        scene["duration_budget"] = budget
        scene["shots"] = shots
        if not budget.get("passed"):
            duration_errors.append({
                "scene": sid,
                "code": "DURATION_BUDGET_EXCEEDED",
                "detail": f"required={budget['required_seconds']}s declared={budget['declared_seconds']}s",
            })

        prompt, meta = compile_flow_prompt(
            scene,
            result.get("visual_style") or "Cinematic",
            result.get("characters") or [],
            result.get("locations") or [],
            result.get("props") or [],
        )
        scene["flow_prompt"] = prompt
        scene["flow_prompt_meta"] = meta
        prompt_errors.extend(validate_flow_prompt(scene, prompt, meta))

        for shot in shots:
            shot_prompt, shot_meta = compile_flow_prompt(
                scene,
                result.get("visual_style") or "Cinematic",
                result.get("characters") or [],
                result.get("locations") or [],
                result.get("props") or [],
                shot=shot,
            )
            shot["flow_prompt"] = shot_prompt
            shot["flow_prompt_meta"] = shot_meta
            prompt_errors.extend(validate_flow_prompt(scene, shot_prompt, shot_meta, shot=shot))

    errors.extend(duration_errors)
    errors.extend(prompt_errors)

    integrity = deepcopy(result.get("integrity") or {})
    gates = deepcopy(integrity.get("gates") or {})
    gates["DURATION_BUDGET"] = not duration_errors
    gates["TIMELINE_ORDER"] = not span_errors
    gates["FLOW_PROMPT"] = not prompt_errors
    gates["SHOT_PLAN"] = all(bool(scene.get("shots")) for scene in scenes)
    gates["DETERMINISTIC_MERGE"] = True
    gates["BOUNDARY_CONTEXT"] = True
    integrity["version"] = "batch-b-v1"
    integrity["batch_b"] = {
        "duration_budget": "deterministic",
        "shot_planner": "deterministic_v1",
        "scene_merge": "deterministic_whitelist_v1",
        "boundary_context": "previous_scene+end_state+location+characters+props+recent_events+prop_history+current_owners+history_fingerprint",
        "flow_prompt_compiler": "deterministic_flow_prompt_v4_narrator_lock",
    }
    integrity["gates"] = gates
    integrity["errors"] = errors
    integrity["warnings"] = warnings
    integrity["error_count"] = len(errors)
    integrity["warning_count"] = len(warnings)
    integrity["source_spans"] = spans
    integrity["final_gate"] = all(gates.values()) and not errors

    for scene in scenes:
        sid = str(scene.get("id") or "")
        scene_errors = [x for x in errors if str(x.get("scene") or "") == sid]
        gate = deepcopy(scene.get("gate") or {})
        gate["duration_budget"] = {
            "passed": bool((scene.get("duration_budget") or {}).get("passed")),
            "shot_count": int((scene.get("duration_budget") or {}).get("shot_count") or 0),
        }
        gate["timeline_order"] = {
            "passed": bool(scene.get("source_span")) and not any(x.get("code") in {"TIMELINE_SOURCE_REORDERED", "SOURCE_EXCERPT_NOT_FOUND", "SOURCE_SPAN_MISSING"} for x in scene_errors)
        }
        gate["flow_prompt"] = {
            "passed": not any(str(x.get("code") or "").startswith("FLOW_PROMPT") for x in scene_errors),
            "hash": (scene.get("flow_prompt_meta") or {}).get("prompt_hash"),
        }
        gate["shot_plan"] = {"passed": bool(scene.get("shots")), "count": len(scene.get("shots") or [])}
        scene["gate"] = gate

    result["integrity"] = integrity
    return result
