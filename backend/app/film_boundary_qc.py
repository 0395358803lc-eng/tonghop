from __future__ import annotations

import json
from pathlib import Path

JUNCTION_QC_VERSION = "junction-qc-v2"
IDENTITY_MIN = 90.0
PROP_MIN = 85.0
POSITION_MIN = 80.0
AUDIO_MIN = 70.0
WARDROBE_MIN = 85.0
LIGHTING_MIN = 70.0
CAMERA_MIN = 70.0
MOTION_MIN = 75.0
LOCATION_MIN = 75.0

VISION_DIMENSIONS = (
    "identity",
    "wardrobe",
    "character_position",
    "body_orientation",
    "prop_holder",
    "prop_state",
    "prop_owner",
    "location_geometry",
    "lighting",
    "camera_direction",
    "motion_direction",
)


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _dim(score, threshold, hard, issue=None, evidence=None):
    if score is None:
        return {
            "score": None,
            "threshold": threshold,
            "status": "not_evaluated",
            "passed": None,
            "hard": hard,
            "issue": issue,
            "evidence": evidence,
        }
    passed = score >= threshold
    return {
        "score": round(score, 2),
        "threshold": threshold,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "hard": hard,
        "issue": issue,
        "evidence": evidence,
    }


def _not_required_dim(threshold, evidence=None):
    return {
        "score": None,
        "threshold": threshold,
        "status": "not_required",
        "passed": None,
        "hard": False,
        "issue": None,
        "evidence": evidence,
    }


def _ids(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, dict):
        return {str(k) for k, v in value.items() if v}
    return {str(x) for x in value if x}


def _holder_map(ledger: dict | None, scene: dict | None = None) -> dict[str, str]:
    ledger = ledger or {}
    data = {}
    for key in ("prop_holder", "prop_owner"):
        raw = ledger.get(key) or {}
        if isinstance(raw, dict):
            for prop_id, holder in raw.items():
                if holder:
                    data[str(prop_id)] = str(holder)
    structured = (scene or {}).get("end_state_structured") if scene else None
    if isinstance(structured, dict):
        for prop_id, holder in (structured.get("prop_holder") or structured.get("prop_owner") or {}).items():
            data.setdefault(str(prop_id), str(holder))
    return data


def _transfers(scene: dict | None) -> set[str]:
    found = set()
    for item in (scene or {}).get("prop_transfers") or []:
        if isinstance(item, dict):
            pid = item.get("prop_id") or item.get("entity_id")
            if pid:
                found.add(str(pid))
        elif item:
            found.add(str(item))
    return found


def _boundary_prop_ids(scene: dict, *, side: str) -> set[str]:
    """Props that actually exist at the visual boundary of a scene."""
    ids = {str(x) for x in (scene.get("props_present") or []) if x}
    structured_key = "end_state_structured" if side == "end" else "start_state_structured"
    structured = scene.get(structured_key)
    if isinstance(structured, dict):
        raw_props = structured.get("props") or {}
        if isinstance(raw_props, dict):
            ids.update(str(x) for x in raw_props if x)
    return ids


def _critical_props(prev_scene: dict, next_scene: dict) -> set[str]:
    """Return only props whose state/holder must survive this boundary.

    A prop that exists only in the next scene is an introduction/reveal, not a
    direct boundary-continuity obligation. Explicit transfers always remain
    hard because they encode a cross-scene ownership/state change.
    """
    prev_boundary = _boundary_prop_ids(prev_scene, side="end")
    next_boundary = _boundary_prop_ids(next_scene, side="start")
    shared = prev_boundary & next_boundary
    transferred = _transfers(prev_scene) | _transfers(next_scene)
    explicit = set()
    for scene in (prev_scene, next_scene):
        continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
        explicit.update(str(x) for x in (continuity.get("critical_props") or []) if x)
    return shared | transferred | (explicit & (shared | transferred))


def selected_media_usable(media: dict | None, scene_id: str) -> tuple[bool, str | None]:
    if not media:
        return False, "JUNCTION_SELECTED_MEDIA_REQUIRED"
    if not media.get("is_selected"):
        return False, "JUNCTION_SELECTED_MEDIA_REQUIRED"
    if str(media.get("scene_id") or "") != str(scene_id):
        return False, "JUNCTION_SELECTED_MEDIA_REQUIRED"
    if media.get("status") != "completed" or media.get("qc_status") != "passed":
        return False, "JUNCTION_SELECTED_MEDIA_INVALID"
    return True, None


def _data_root() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR).resolve()


def _readable_file(path: Path | None) -> Path | None:
    if not path:
        return None
    try:
        resolved = path if path.is_absolute() else (_data_root() / path)
        resolved = resolved.resolve()
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            return None
        try:
            resolved.read_bytes()[:16]
        except Exception:
            return None
        return resolved
    except Exception:
        return None


def _flow_local_frame(url: str | None, kind: str) -> Path | None:
    if not url:
        return None
    try:
        from .film_qc_service import _flow_frame_path
        return _readable_file(_flow_frame_path(url, kind))
    except Exception:
        return None


def resolve_frame_path(value, *, kind: str, media: dict | None = None) -> Path | None:
    raw = str(value or "").strip()
    if raw:
        if "/last-frame" in raw or kind == "last":
            found = _flow_local_frame(raw, "last")
            if found:
                return found
        if "/first-frame" in raw or kind == "first":
            found = _flow_local_frame(raw, "first")
            if found:
                return found
        candidate = Path(raw)
        if candidate.is_absolute():
            readable = _readable_file(candidate)
            if readable:
                return readable
        else:
            for option in (Path(raw), _data_root() / raw.lstrip("/\\")):
                readable = _readable_file(option)
                if readable:
                    return readable
    meta = (media or {}).get("metadata") or {}
    for key in (("last_frame_url", "last_frame_path") if kind == "last" else ("first_frame_url", "first_frame_path")):
        extra = meta.get(key) or (media or {}).get(key)
        if extra and str(extra) != raw:
            found = resolve_frame_path(extra, kind=kind, media=None)
            if found:
                return found
    job_id = str((media or {}).get("provider_job_id") or "").strip()
    if job_id:
        folder = _data_root() / "flow_downloads" / job_id
        pattern = "last_frame_*.jpg" if kind == "last" else "first_frame_*.jpg"
        try:
            matches = sorted(folder.glob(pattern))
        except Exception:
            matches = []
        if matches:
            readable = _readable_file(matches[0])
            if readable:
                return readable
    return None


def resolve_junction_evidence(
    previous_scene: dict,
    next_scene: dict,
    previous_ledger: dict | None = None,
    next_ledger: dict | None = None,
    previous_media: dict | None = None,
    next_media: dict | None = None,
) -> dict:
    prev_ok, prev_err = selected_media_usable(previous_media, previous_scene.get("id"))
    next_ok, next_err = selected_media_usable(next_media, next_scene.get("id"))
    if not prev_ok or not next_ok:
        code = prev_err or next_err or "JUNCTION_SELECTED_MEDIA_REQUIRED"
        return {"ok": False, "code": code, "previous_last_frame": None, "next_first_frame": None}

    prev_ref = (previous_ledger or {}).get("accepted_last_frame") or (previous_media or {}).get("thumbnail_url")
    next_ref = (next_ledger or {}).get("accepted_first_frame") or (next_media or {}).get("thumbnail_url")
    prev_path = resolve_frame_path(prev_ref, kind="last", media=previous_media)
    next_path = resolve_frame_path(next_ref, kind="first", media=next_media)
    if not prev_path or not next_path:
        return {
            "ok": False,
            "code": "JUNCTION_EVIDENCE_MISSING",
            "previous_last_frame": str(prev_ref or ""),
            "next_first_frame": str(next_ref or ""),
            "previous_last_frame_path": str(prev_path) if prev_path else None,
            "next_first_frame_path": str(next_path) if next_path else None,
        }
    return {
        "ok": True,
        "code": None,
        "previous_last_frame": str(prev_ref or ""),
        "next_first_frame": str(next_ref or ""),
        "previous_last_frame_path": str(prev_path),
        "next_first_frame_path": str(next_path),
        "selected_previous_media_id": (previous_media or {}).get("id"),
        "selected_next_media_id": (next_media or {}).get("id"),
        "previous_scene_id": previous_scene.get("id"),
        "next_scene_id": next_scene.get("id"),
    }


def _obs_dim(observations: dict | None, name: str) -> dict:
    raw = (observations or {}).get(name)
    if isinstance(raw, dict):
        return raw
    if _num(raw) is not None:
        return {"score": _num(raw)}
    return {}



def _ledger_audio_state(ledger: dict | None) -> dict:
    raw = (ledger or {}).get("audio_state")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _audio_transition_observation(previous_ledger: dict | None, next_ledger: dict | None) -> dict:
    prev = _ledger_audio_state(previous_ledger)
    nxt = _ledger_audio_state(next_ledger)
    prev_ok = bool(prev.get("present") and prev.get("non_silent"))
    next_ok = bool(nxt.get("present") and nxt.get("non_silent"))
    if not prev and not nxt:
        return {"score": None, "evidence": "AUDIO_LEDGER_MISSING"}
    if not prev_ok or not next_ok:
        return {
            "score": 35.0,
            "evidence": f"audio_presence prev={prev_ok} next={next_ok}",
        }
    prev_mean = _num(prev.get("mean_volume_db"))
    next_mean = _num(nxt.get("mean_volume_db"))
    if prev_mean is None or next_mean is None:
        return {
            "score": 88.0,
            "evidence": "both scenes contain non-silent accepted audio",
        }
    delta = abs(prev_mean - next_mean)
    score = max(70.0, 96.0 - min(26.0, delta * 3.0))
    return {
        "score": round(score, 2),
        "evidence": f"accepted audio present; mean-volume delta={delta:.2f} dB",
    }


def _dialogue_transition_observation(
    previous_scene: dict,
    next_scene: dict,
    previous_ledger: dict | None,
    next_ledger: dict | None,
) -> dict:
    prev_required = bool(previous_scene.get("dialogue") or previous_scene.get("voiceover"))
    next_required = bool(next_scene.get("dialogue") or next_scene.get("voiceover"))
    required = prev_required and next_required
    if not required:
        return {
            "required": False,
            "score": None,
            "status": "not_required",
            "evidence": "continuous dialogue hard-gate not required",
        }

    prev_dialogue = str((previous_ledger or {}).get("dialogue_state") or "").strip().lower()
    next_dialogue = str((next_ledger or {}).get("dialogue_state") or "").strip().lower()
    prev_audio = _ledger_audio_state(previous_ledger)
    next_audio = _ledger_audio_state(next_ledger)
    prev_ok = prev_dialogue == "delivered" and bool(prev_audio.get("present") and prev_audio.get("non_silent"))
    next_ok = next_dialogue == "delivered" and bool(next_audio.get("present") and next_audio.get("non_silent"))
    if prev_ok and next_ok:
        return {
            "required": True,
            "score": 94.0,
            "status": "passed",
            "evidence": "both accepted scenes delivered dialogue with non-silent audio",
        }
    return {
        "required": True,
        "score": 20.0,
        "status": "failed",
        "evidence": (
            f"dialogue/audio continuity missing: "
            f"prev_dialogue={prev_dialogue or 'missing'} prev_audio={bool(prev_audio.get('present') and prev_audio.get('non_silent'))}; "
            f"next_dialogue={next_dialogue or 'missing'} next_audio={bool(next_audio.get('present') and next_audio.get('non_silent'))}"
        ),
    }


def evaluate_junction_qc(
    previous_scene: dict,
    next_scene: dict,
    previous_ledger: dict | None = None,
    next_ledger: dict | None = None,
    previous_media: dict | None = None,
    next_media: dict | None = None,
    observations: dict | None = None,
    vision_fn=None,
) -> dict:
    evidence = resolve_junction_evidence(
        previous_scene, next_scene, previous_ledger, next_ledger, previous_media, next_media,
    )
    public_evidence = {
        "previous_scene_id": previous_scene.get("id"),
        "next_scene_id": next_scene.get("id"),
        "previous_last_frame": evidence.get("previous_last_frame"),
        "next_first_frame": evidence.get("next_first_frame"),
        "selected_previous_media_id": (previous_media or {}).get("id"),
        "selected_next_media_id": (next_media or {}).get("id"),
        "previous_audio_tail": None,
        "next_audio_head": None,
    }
    if not evidence.get("ok"):
        code = evidence.get("code") or "JUNCTION_EVIDENCE_MISSING"
        return {
            "version": JUNCTION_QC_VERSION,
            "passed": False,
            "blocked": True,
            "code": code,
            "overall_score": None,
            "dimensions": {},
            "hard_gate": {"passed": False, "failed": ["evidence" if code == "JUNCTION_EVIDENCE_MISSING" else "selected_media"], "dimensions": {}},
            "issues": [{"type": "evidence" if "EVIDENCE" in code else "selected_media", "severity": "critical", "evidence": code}],
            "evidence": public_evidence,
            "vision": None,
        }

    prev_chars = _ids(previous_scene.get("characters"))
    next_chars = _ids(next_scene.get("characters"))
    continuing = prev_chars & next_chars
    same_location = bool(
        previous_scene.get("location_id")
        and previous_scene.get("location_id") == next_scene.get("location_id")
    )
    critical = _critical_props(previous_scene, next_scene)
    vision_required = bool(continuing or same_location or critical)

    obs = observations
    vision_meta = None
    if isinstance(obs, dict) and (obs.get("provider") or obs.get("model") or obs.get("observed_summary") or obs.get("vision_raw_summary")):
        vision_meta = {
            "provider": obs.get("provider"),
            "model": obs.get("model"),
            "raw_summary": obs.get("observed_summary") or obs.get("vision_raw_summary"),
        }
    if obs is None and callable(vision_fn) and vision_required:
        payload = vision_fn(evidence)
        if isinstance(payload, dict) and any(key in payload for key in VISION_DIMENSIONS):
            obs = payload
            vision_meta = {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "raw_summary": payload.get("observed_summary") or payload.get("vision_raw_summary"),
            }
        elif isinstance(payload, dict):
            obs = payload.get("dimensions") or payload.get("observations") or payload
            vision_meta = {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "raw_summary": payload.get("observed_summary"),
            }

    identity_hard = bool(continuing)
    next_need_direct = bool(continuing) and same_location
    prop_hard = bool(critical)
    if continuing and same_location:
        boundary_mode = "continuous"
    elif continuing:
        boundary_mode = "location_cut"
    elif same_location:
        boundary_mode = "subject_cut"
    else:
        boundary_mode = "hard_cut"
    prev_speech = bool(previous_scene.get("dialogue") or previous_scene.get("voiceover"))
    next_speech = bool(next_scene.get("dialogue") or next_scene.get("voiceover"))
    dialogue_hard = prev_speech and next_speech

    vis = (previous_ledger or {}).get("character_visibility") or {}
    identity_issue = None
    if identity_hard and isinstance(vis, dict):
        missing = [cid for cid in continuing if vis.get(cid) is False]
        if missing:
            identity_issue = "JUNCTION_IDENTITY_BREAK"

    prev_hold = _holder_map(previous_ledger, previous_scene)
    next_hold = _holder_map(next_ledger, next_scene)
    transferred = _transfers(next_scene) | _transfers(previous_scene)
    prop_issue = None
    if prop_hard:
        for prop_id in critical:
            before = prev_hold.get(prop_id)
            after = next_hold.get(prop_id)
            if before and after and before != after and prop_id not in transferred:
                prop_issue = "JUNCTION_PROP_TRANSFER_BREAK"
                break

    def score_of(name, fallback=None):
        item = _obs_dim(obs, name)
        return _num(item.get("score") if item else fallback)

    def evidence_of(name):
        item = _obs_dim(obs, name)
        return item.get("evidence") if item else None

    identity_score = score_of("identity")
    if identity_issue:
        identity_score = min(identity_score if identity_score is not None else 40.0, 40.0)
    prop_score = score_of("prop_state", score_of("prop_holder", score_of("prop")))
    if prop_issue:
        prop_score = min(prop_score if prop_score is not None else 40.0, 40.0)

    audio_obs = _audio_transition_observation(previous_ledger, next_ledger)
    dialogue_obs = _dialogue_transition_observation(previous_scene, next_scene, previous_ledger, next_ledger)
    dialogue_dim = _dim(
        dialogue_obs.get("score"),
        AUDIO_MIN,
        dialogue_hard,
        evidence=dialogue_obs.get("evidence"),
    )
    if not dialogue_hard:
        dialogue_dim.update({
            "score": None,
            "status": "not_required",
            "passed": None,
            "hard": False,
        })

    dims = {
        "identity": (
            _dim(identity_score, IDENTITY_MIN, identity_hard, identity_issue, evidence_of("identity"))
            if continuing
            else _not_required_dim(IDENTITY_MIN, "No continuing character across boundary.")
        ),
        "wardrobe": (
            _dim(score_of("wardrobe"), WARDROBE_MIN, False, evidence=evidence_of("wardrobe"))
            if continuing
            else _not_required_dim(WARDROBE_MIN, "No continuing character across boundary.")
        ),
        "character_position": (
            _dim(score_of("character_position"), POSITION_MIN, next_need_direct, evidence=evidence_of("character_position"))
            if next_need_direct
            else _not_required_dim(POSITION_MIN, "Direct screen-position continuity not required for this cut.")
        ),
        "body_orientation": (
            _dim(score_of("body_orientation", score_of("character_position")), POSITION_MIN, next_need_direct, evidence=evidence_of("body_orientation"))
            if next_need_direct
            else _not_required_dim(POSITION_MIN, "Direct body-orientation continuity not required for this cut.")
        ),
        "prop_owner": (
            _dim(score_of("prop_owner", prop_score), PROP_MIN, False, prop_issue, evidence_of("prop_owner"))
            if prop_hard
            else _not_required_dim(PROP_MIN, "No prop crosses this boundary.")
        ),
        "prop_holder": (
            _dim(score_of("prop_holder", prop_score), PROP_MIN, True, prop_issue, evidence_of("prop_holder"))
            if prop_hard
            else _not_required_dim(PROP_MIN, "No prop crosses this boundary.")
        ),
        "prop_state": (
            _dim(prop_score, PROP_MIN, True, prop_issue, evidence_of("prop_state"))
            if prop_hard
            else _not_required_dim(PROP_MIN, "No prop crosses this boundary.")
        ),
        "location_geometry": (
            _dim(score_of("location_geometry"), LOCATION_MIN, False, evidence=evidence_of("location_geometry"))
            if same_location
            else _not_required_dim(LOCATION_MIN, "Location changes by script at this cut.")
        ),
        "lighting": (
            _dim(score_of("lighting"), LIGHTING_MIN, False, evidence=evidence_of("lighting"))
            if same_location
            else _not_required_dim(LIGHTING_MIN, "Lighting continuity is not direct across a scripted location cut.")
        ),
        "motion_direction": (
            _dim(score_of("motion_direction"), MOTION_MIN, False, evidence=evidence_of("motion_direction"))
            if next_need_direct
            else _not_required_dim(MOTION_MIN, "Motion-vector continuity not required for this cut.")
        ),
        "camera_direction": (
            _dim(score_of("camera_direction"), CAMERA_MIN, False, evidence=evidence_of("camera_direction"))
            if same_location
            else _not_required_dim(CAMERA_MIN, "Camera-direction continuity not required across a scripted location cut.")
        ),
        "audio_transition": _dim(audio_obs.get("score"), AUDIO_MIN, False, evidence=audio_obs.get("evidence")),
        "dialogue_transition": dialogue_dim,
    }

    hard_failed = [name for name, item in dims.items() if item.get("hard") and item.get("passed") is False]
    incomplete = [name for name, item in dims.items() if item.get("hard") and item.get("status") == "not_evaluated"]
    if incomplete:
        hard_failed = list(dict.fromkeys(hard_failed + incomplete))
    scores = [item["score"] for item in dims.values() if item.get("score") is not None]
    overall = round(sum(scores) / len(scores), 2) if scores else None
    passed = not hard_failed and not incomplete
    issues = []
    if identity_issue:
        issues.append({"type": "identity", "severity": "critical", "evidence": identity_issue})
    if prop_issue:
        issues.append({"type": "prop_state", "severity": "critical", "evidence": prop_issue})
    for name in hard_failed:
        if name in {"identity", "prop_state", "prop_holder"} and (identity_issue or prop_issue):
            continue
        detail = dims[name].get("evidence") or dims[name].get("issue") or name
        issues.append({"type": name, "severity": "critical", "evidence": detail})
    public_evidence["boundary_mode"] = boundary_mode
    public_evidence["vision_required"] = vision_required
    public_evidence["continuing_characters"] = sorted(continuing)
    public_evidence["boundary_critical_props"] = sorted(critical)
    public_evidence["same_location"] = same_location
    public_evidence["audio_transition"] = audio_obs
    public_evidence["dialogue_transition"] = dialogue_obs
    if vision_meta:
        public_evidence["vision_provider"] = vision_meta.get("provider")
        public_evidence["vision_model"] = vision_meta.get("model")
        public_evidence["vision_raw_summary"] = vision_meta.get("raw_summary")
    return {
        "version": JUNCTION_QC_VERSION,
        "passed": passed,
        "blocked": False,
        "overall_score": overall,
        "hard_gate": {
            "passed": not hard_failed,
            "failed": hard_failed,
            "dimensions": {k: v for k, v in dims.items() if v.get("hard")},
        },
        "dimensions": dims,
        "incomplete_dimensions": incomplete,
        "issues": issues,
        "evidence": public_evidence,
        "vision": vision_meta,
        "code": None if passed else ("JUNCTION_VISION_INCOMPLETE" if incomplete else "JUNCTION_FAIL"),
    }


def junction_repair_target(previous_scene: dict, next_scene: dict, previous_ledger: dict | None = None) -> dict:
    prev_id = previous_scene.get("id")
    next_id = next_scene.get("id")
    if not (previous_ledger or {}).get("accepted_last_frame"):
        return {"scene_id": prev_id, "reason": "PREVIOUS_BOUNDARY_CONTRACT_BROKEN", "prefer": "previous"}
    return {"scene_id": next_id, "reason": "JUNCTION_FAIL_REGENERATE_NEXT", "prefer": "next"}


def build_junction_repair_prompt(previous_scene: dict, next_scene: dict, target: dict, report: dict | None = None) -> str:
    failed = ((report or {}).get("hard_gate") or {}).get("failed") or []
    next_id = next_scene.get("id")
    prev_id = previous_scene.get("id")
    preserve = target.get("scene_id") or next_id
    return (
        f"Preserve {preserve} story/action/dialogue.\n"
        f"Repair only boundary continuity against {prev_id if preserve == next_id else next_id}.\n"
        "Must match:\n"
        "- CHAR identity\n"
        "- wardrobe\n"
        "- entry position\n"
        "- body orientation\n"
        "- PROP holder / state\n"
        "- lighting direction\n"
        "- motion direction\n"
        f"Use {prev_id} accepted last frame as primary reference.\n"
        f"Failed hard gates: {', '.join(failed) or 'junction continuity'}."
    )
