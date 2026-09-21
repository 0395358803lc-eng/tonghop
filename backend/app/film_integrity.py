import hashlib
import json
import re
from copy import deepcopy


STRUCTURED_FORMAT = "CINEMATIC CONSISTENCY SCRIPT V1"


def _sha256(value) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_scalar(value: str):
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    if text.lower() in {"null", "none", "n/a"}:
        return None
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            pass
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except Exception:
            pass
    return text


def _parse_list(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip().strip('"').strip("'") for part in text.split(",") if part.strip()]


def _section(text: str, label: str, next_labels: list[str]) -> str:
    marker = f"\n{label}\n"
    pos = text.find(marker)
    if pos < 0 and text.startswith(label + "\n"):
        pos = -1
        start = len(label) + 1
    elif pos >= 0:
        start = pos + len(marker)
    else:
        return ""
    sep = re.search(r"={8,}\s*\n", text[start:])
    if sep:
        start += sep.end()
    ends = []
    for other in next_labels:
        m = text.find(f"\n{other}\n", start)
        if m >= 0:
            ends.append(m)
    end = min(ends) if ends else len(text)
    body = text[start:end]
    body = re.sub(r"\n={8,}\s*$", "", body.strip())
    return body.strip()


def _parse_flat_fields(body: str) -> dict:
    out = {}
    for line in body.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line.strip())
        if m:
            out[m.group(1).lower()] = _clean_scalar(m.group(2))
    return out


def _parse_entities(body: str, prefix: str) -> list[dict]:
    pattern = re.compile(rf"(?m)^({re.escape(prefix)}_\d+):\s*$")
    matches = list(pattern.finditer(body))
    entities = []
    for idx, match in enumerate(matches):
        block = body[match.end(): matches[idx + 1].start() if idx + 1 < len(matches) else len(body)]
        item = {"id": match.group(1)}
        for line in block.splitlines():
            m = re.match(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
            if not m:
                continue
            key = m.group(1)
            value = _clean_scalar(m.group(2))
            item[key] = value
        entities.append(item)
    return entities


def _parse_nested_items(lines: list[str], start: int) -> tuple[list[dict], int]:
    items = []
    current = None
    i = start
    while i < len(lines):
        line = lines[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", line):
            break
        m_new = re.match(r"^\s{2}-\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m_new:
            current = {m_new.group(1): _clean_scalar(m_new.group(2))}
            items.append(current)
            i += 1
            continue
        m_field = re.match(r"^\s{4,}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m_field and current is not None:
            current[m_field.group(1)] = _clean_scalar(m_field.group(2))
        i += 1
    return items, i


def _parse_scene_block(scene_id: str, block: str, raw_block: str) -> dict:
    lines = block.splitlines()
    fields = {}
    dialogue = []
    transfers = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, raw = m.group(1), m.group(2)
        low = key.lower()
        if low in {"dialogue", "prop_transfers"} and not raw.strip():
            parsed, i = _parse_nested_items(lines, i + 1)
            if low == "dialogue":
                dialogue = parsed
            else:
                transfers = parsed
            continue
        fields[low] = _clean_scalar(raw)
        i += 1

    characters = _parse_list(str(fields.get("characters_onscreen") or ""))
    props_present = _parse_list(str(fields.get("props_present") or ""))
    duration = fields.get("duration_sec")
    try:
        duration = float(duration or 8)
    except Exception:
        duration = 8.0

    previous = fields.get("previous_scene")
    if isinstance(previous, str) and previous.lower() in {"null", "none"}:
        previous = None

    scene = {
        "id": scene_id,
        "title": scene_id,
        "source_text": str(fields.get("action") or ""),
        "summary": str(fields.get("action") or ""),
        "duration": duration,
        "characters": characters,
        "location_id": fields.get("location_id"),
        "props_present": props_present,
        "prop_transfers": transfers,
        "action": str(fields.get("action") or ""),
        "camera": str(fields.get("camera") or ""),
        "lighting": str(fields.get("lighting") or ""),
        "atmosphere": str(fields.get("atmosphere") or ""),
        "voiceover": str(fields.get("voiceover") or ""),
        "dialogue": dialogue,
        "start_state": str(fields.get("start_state") or ""),
        "end_state": str(fields.get("end_state") or ""),
        "continuity": {
            "previous_scene": previous,
            "next_scene": fields.get("next_scene"),
            "notes": "Source-locked continuity from structured script.",
        },
        "visual_prompt": "",
        "warnings": [],
    }
    snapshot = {
        "id": scene_id,
        "duration": duration,
        "characters": characters,
        "location_id": scene.get("location_id"),
        "props_present": props_present,
        "prop_transfers": transfers,
        "action": scene["action"],
        "camera": scene["camera"],
        "lighting": scene["lighting"],
        "atmosphere": scene["atmosphere"],
        "dialogue": dialogue,
        "voiceover": scene["voiceover"],
        "start_state": scene["start_state"],
        "end_state": scene["end_state"],
        "previous_scene": previous,
        "next_scene": fields.get("next_scene"),
    }
    scene["source_snapshot"] = snapshot
    scene["source_hash"] = _sha256(snapshot)
    scene["_raw_source_block"] = raw_block
    return scene


def prop_state_dimensions(text: str) -> dict:
    low = (text or "").lower()
    dims = {}

    # Visibility / reveal is independent from mechanical state.
    if "hidden" in low or "concealed" in low or "ẩn" in low:
        dims["visibility_state"] = "hidden"
    partial_visibility = any(token in low for token in (
        "not fully revealed", "partially revealed", "partially visible", "edge visible",
        "still not fully revealed", "chưa lộ hoàn toàn", "chỉ lộ cạnh", "lộ một phần",
    ))
    if partial_visibility:
        dims["visibility_state"] = "partial"
    elif "revealed" in low or "visible" in low or "lộ ra" in low or "hiện ra" in low:
        dims["visibility_state"] = "revealed"

    # Playback / operational state must never be confused with lid/container open state.
    if any(token in low for token in ("playing", "music plays", "music playing", "record plays", "đang phát", "phát nhạc", "nhạc bắt đầu", "nhạc đang")):
        dims["playback_state"] = "playing"
    elif any(token in low for token in ("stopped", "stop playing", "ngừng phát", "dừng phát")):
        dims["playback_state"] = "stopped"
    elif re.search(r"\bidle\b", low) or "chưa phát" in low:
        dims["playback_state"] = "idle"

    # Lid state is a separate dimension.
    if re.search(r"\b(?:lid|cover)\b[^.;,]{0,24}\b(?:open|opened)\b", low) or "nắp mở" in low or "mở nắp" in low:
        dims["lid_state"] = "open"
    elif re.search(r"\b(?:lid|cover)\b[^.;,]{0,24}\bclosed\b", low) or "nắp đóng" in low or "đóng nắp" in low:
        dims["lid_state"] = "closed"

    # Disc/media loading state.
    if any(token in low for token in ("no disc", "without disc", "empty platter", "không có đĩa", "chưa có đĩa")):
        dims["disc_state"] = "empty"
    elif any(token in low for token in ("disc on", "record on", "disc loaded", "record loaded", "đặt đĩa", "đĩa trên mâm", "đĩa đã đặt")):
        dims["disc_state"] = "loaded"

    # Rotation state is independent from audible playback.
    if any(token in low for token in ("disc spinning", "record spinning", "platter spinning", "beginning to spin", "bắt đầu quay", "đang quay")):
        dims["rotation_state"] = "spinning"
    elif any(token in low for token in ("disc stopped", "platter stopped", "ngừng quay", "dừng quay")):
        dims["rotation_state"] = "stopped"

    # Needle state for turntable-like devices.
    if any(token in low for token in ("needle down", "needle lowered", "lower the needle", "lowers the needle", "hạ kim", "kim đã hạ")):
        dims["needle_state"] = "down"
    elif any(token in low for token in ("needle up", "needle raised", "raise the needle", "raises the needle", "nâng kim", "kim đang nâng")):
        dims["needle_state"] = "up"

    # Power state.
    if re.search(r"\bpower(?:ed)?\s+on\b|\bturned\s+on\b", low) or "bật nguồn" in low or "đang bật" in low:
        dims["power_state"] = "on"
    elif re.search(r"\bpower(?:ed)?\s+off\b|\bturned\s+off\b", low) or "tắt nguồn" in low or "đang tắt" in low:
        dims["power_state"] = "off"

    # Lock state.
    if "unlocked" in low or "đã mở khóa" in low:
        dims["lock_state"] = "unlocked"
    elif re.search(r"\blocked\b", low) or "đang khóa" in low or "bị khóa" in low:
        dims["lock_state"] = "locked"

    # Container / envelope / box state. Only use open/closed when context indicates a container.
    container_context = any(token in low for token in ("envelope", "box", "drawer", "door", "container", "phong bì", "hộp", "ngăn kéo", "cửa"))
    if "sealed" in low or "niêm kín" in low:
        dims["container_state"] = "sealed"
    elif container_context and (re.search(r"\bopened\b|\bopen\b", low) or "đã mở" in low):
        dims["container_state"] = "open"
    elif container_context and ("closed" in low or "đóng" in low):
        dims["container_state"] = "closed"

    # Fallback mechanical state for simple props when no specific dimension is available.
    if not dims:
        if re.search(r"\bopened\b|\bopen\b", low):
            dims["mechanical_state"] = "open"
        elif "closed" in low:
            dims["mechanical_state"] = "closed"
        elif "sealed" in low:
            dims["mechanical_state"] = "sealed"
        elif re.search(r"\blocked\b", low):
            dims["mechanical_state"] = "locked"
        elif "unlocked" in low:
            dims["mechanical_state"] = "unlocked"

    return dims


def _state_keyword(text: str) -> str | None:
    dims = prop_state_dimensions(text)
    # Backward-compatible scalar state. Operational state wins over lid/container state.
    for key in (
        "playback_state",
        "visibility_state",
        "lock_state",
        "container_state",
        "mechanical_state",
        "disc_state",
        "needle_state",
        "power_state",
        "lid_state",
    ):
        if dims.get(key):
            return dims[key]
    return None


def _owner_or_container(fragment: str, prop_id: str) -> tuple[str | None, str | None]:
    low = fragment.lower()
    inside = re.search(r"inside\s+(PROP_\d+)", fragment, re.I)
    if inside:
        return None, inside.group(1).upper()
    chars = re.findall(r"CHAR_\d+", fragment, re.I)
    if chars:
        if any(token in low for token in ("held", "hand", "palm", "with ", "in char_", "by char_", "owner")):
            return chars[0].upper(), None
    return None, None


def structure_state(text: str, declared_characters: list[str] | None = None, location_id: str | None = None, props_present: list[str] | None = None) -> dict:
    raw = str(text or "").strip()
    entities = sorted(set(re.findall(r"(?:CHAR|PROP|LOC)_\d+", raw, re.I)))
    entities = [x.upper() for x in entities]
    fragments = [x.strip() for x in re.split(r"[;\n]+", raw) if x.strip()]
    result = {
        "raw": raw,
        "entities": entities,
        "characters": {},
        "props": {},
        "locations": {},
        "weather": None,
    }
    for entity in entities:
        related = [frag for frag in fragments if entity.lower() in frag.lower()]
        joined = "; ".join(related)
        if entity.startswith("CHAR_"):
            result["characters"][entity] = {"raw": joined}
        elif entity.startswith("LOC_"):
            result["locations"][entity] = {"raw": joined}
        elif entity.startswith("PROP_"):
            owner, container = _owner_or_container(joined, entity)
            dimensions = prop_state_dimensions(joined)
            result["props"][entity] = {
                "raw": joined,
                "owner": owner,
                "container": container,
                "state": _state_keyword(joined),
                "state_dimensions": dimensions,
            }
    # Complete structured state from declared scene context without inventing facts.
    declared_characters = declared_characters or []
    props_present = props_present or []
    if len(declared_characters) == 2 and ("both" in raw.lower() or "cả hai" in raw.lower()):
        for cid in declared_characters:
            result["characters"].setdefault(cid, {"raw": raw, "resolved_from": "both"})
            if cid not in result["entities"]:
                result["entities"].append(cid)
    if location_id:
        result["locations"].setdefault(location_id, {"raw": raw, "declared": True})
        if location_id not in result["entities"]:
            result["entities"].append(location_id)
    for pid in props_present:
        result["props"].setdefault(pid, {"raw": "", "owner": None, "container": None, "state": None, "state_dimensions": {}, "declared": True})
        if pid not in result["entities"]:
            result["entities"].append(pid)
    result["entities"] = sorted(set(result["entities"]))

    low = raw.lower()
    if "drizzle" in low or "lất phất" in low:
        result["weather"] = "drizzle"
    elif "rain" in low or "mưa" in low:
        if any(x in low for x in ("lighter", "dịu", "thưa", "nhẹ")):
            result["weather"] = "light_rain"
        else:
            result["weather"] = "rain"
    return result




def _canon_completeness_errors(characters: list[dict], locations: list[dict], props: list[dict]) -> list[dict]:
    issues = []
    char_required = ("id", "name", "age", "appearance", "clothing")
    loc_required = ("id", "name", "layout", "lighting")
    prop_required = ("id", "name", "canonical_description", "initial_owner", "initial_state")

    for item in characters:
        missing = [key for key in char_required if item.get(key) in (None, "", [])]
        if missing:
            issues.append({
                "scene": "PROJECT",
                "code": "CHARACTER_CANON_INCOMPLETE",
                "detail": f"{item.get('id')}: missing {', '.join(missing)}",
            })
    for item in locations:
        missing = [key for key in loc_required if item.get(key) in (None, "", [])]
        if missing:
            issues.append({
                "scene": "PROJECT",
                "code": "LOCATION_CANON_INCOMPLETE",
                "detail": f"{item.get('id')}: missing {', '.join(missing)}",
            })
    for item in props:
        missing = [key for key in prop_required if item.get(key) in (None, "", [])]
        if missing:
            issues.append({
                "scene": "PROJECT",
                "code": "PROP_CANON_INCOMPLETE",
                "detail": f"{item.get('id')}: missing {', '.join(missing)}",
            })
    return issues


def _canon_characters(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        row = deepcopy(item)
        row["canonical_version"] = "v1"
        row["canonical_locked"] = True
        row["identity_lock"] = row.get("identity_lock") or "FIXED"
        out.append(row)
    return out


def _canon_locations(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        row = deepcopy(item)
        row["canonical_version"] = "v1"
        row["canonical_locked"] = True
        row["layout_lock"] = row.get("layout_lock") or "FIXED"
        out.append(row)
    return out


def _canon_props(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        row = deepcopy(item)
        row["canonical_version"] = "v1"
        row["canonical_locked"] = True
        row["canonical_description"] = row.get("description") or ""
        row["initial_owner"] = row.get("owner_initial") or row.get("owner") or None
        row["initial_state"] = row.get("state_initial") or row.get("state") or ""
        # Keep current runtime state out of Canon.
        row.pop("owner", None)
        row.pop("state", None)
        out.append(row)
    return out


def _timeline_section(body: str) -> list[dict]:
    timeline = []
    for line in body.splitlines():
        m = re.match(r"^\s*(\d+)\.\s*(.+)$", line)
        if not m:
            continue
        timeline.append({"order": int(m.group(1)), "event": m.group(2).strip()})
    return timeline


def _normalize_initial_state(value: str) -> str | None:
    return _state_keyword(value or "")


def _legacy_state_from_dimensions(dimensions: dict, fallback: str | None = None) -> str | None:
    for key in (
        "playback_state", "rotation_state", "visibility_state", "lock_state", "container_state",
        "mechanical_state", "disc_state", "needle_state", "power_state", "lid_state",
    ):
        if dimensions.get(key):
            return dimensions[key]
    return fallback


def _dimension_transition_allowed(dimension: str, old: str, new: str, action_text: str) -> bool:
    if old == new:
        return True
    allowed = {
        "visibility_state": {("hidden", "partial"), ("partial", "revealed"), ("hidden", "revealed"), ("revealed", "hidden")},
        "lock_state": {("locked", "unlocked"), ("unlocked", "locked")},
        "container_state": {("sealed", "open"), ("closed", "open"), ("open", "closed")},
        "mechanical_state": {
            ("sealed", "open"), ("locked", "unlocked"), ("locked", "open"),
            ("unlocked", "open"), ("closed", "open"), ("open", "closed"),
        },
        "playback_state": {
            ("idle", "playing"), ("playing", "stopped"), ("stopped", "playing"),
            ("playing", "idle"), ("stopped", "idle"),
        },
        "rotation_state": {("stopped", "spinning"), ("spinning", "stopped")},
        "disc_state": {("empty", "loaded"), ("loaded", "empty")},
        "needle_state": {("up", "down"), ("down", "up")},
        "power_state": {("off", "on"), ("on", "off")},
        "lid_state": {("closed", "open"), ("open", "closed")},
    }
    if (old, new) in allowed.get(dimension, set()):
        return True

    action = (action_text or "").lower()
    evidence = {
        "visibility_state": ("reveal", "hide", "lộ", "ẩn"),
        "lock_state": ("unlock", "lock", "mở khóa", "khóa"),
        "container_state": ("open", "close", "seal", "mở", "đóng", "niêm"),
        "mechanical_state": ("open", "close", "lock", "unlock", "mở", "đóng", "khóa"),
        "playback_state": ("play", "stop", "music", "record", "phát", "nhạc"),
        "rotation_state": ("spin", "platter", "record", "disc", "quay", "mâm", "đĩa"),
        "disc_state": ("disc", "record", "đĩa", "mâm"),
        "needle_state": ("needle", "kim"),
        "power_state": ("power", "switch", "bật", "tắt"),
        "lid_state": ("lid", "cover", "nắp"),
    }
    return any(token in action for token in evidence.get(dimension, ()))


def _derive_action_state_dimensions(scene: dict, current_dimensions: dict) -> dict:
    text = " ".join([
        str(scene.get("action") or ""),
        str(scene.get("start_state") or ""),
        str(scene.get("end_state") or ""),
    ]).lower()
    out = {}
    if "disc_state" in current_dimensions:
        if any(token in text for token in (
            "on platter", "onto platter", "disc on platter", "record on platter",
            "đặt prop_002 lên mâm", "đặt đĩa lên mâm", "đĩa trên mâm",
            "disc beginning to spin", "disc spinning", "đĩa bắt đầu quay", "đĩa đang quay",
        )):
            out["disc_state"] = "loaded"
    device_like = any(key in current_dimensions for key in (
        "needle_state", "disc_state", "playback_state", "rotation_state", "lid_state"
    ))
    if device_like:
        negated_raise = any(token in text for token in (
            "không nhấc kim", "không nâng kim", "does not raise the needle", "doesn't raise the needle",
            "without raising the needle",
        ))
        if negated_raise:
            if current_dimensions.get("needle_state"):
                out["needle_state"] = current_dimensions["needle_state"]
        elif any(token in text for token in ("needle down", "lower the needle", "lowers the needle", "hạ kim", "kim đã hạ")):
            out["needle_state"] = "down"
        elif any(token in text for token in ("needle up", "raise the needle", "raises the needle", "nhấc kim", "nâng kim")):
            out["needle_state"] = "up"
    if "rotation_state" in current_dimensions or "disc_state" in current_dimensions:
        if any(token in text for token in (
            "disc beginning to spin", "disc spinning", "record spinning", "platter spinning",
            "mâm bắt đầu quay", "đĩa bắt đầu quay", "đĩa đang quay",
        )):
            out["rotation_state"] = "spinning"
    if "playback_state" in current_dimensions:
        if any(token in text for token in (
            "music playing", "music continuing", "music still playing", "music playing softer",
            "giai điệu nữ trầm vang", "nhạc vang", "nhạc đang phát", "nhạc tiếp tục", "music continuing softer",
        )):
            out["playback_state"] = "playing"
        elif any(token in text for token in ("music stops", "stop playing", "nhạc dừng", "ngừng phát")):
            out["playback_state"] = "stopped"
    return out


def _prop_ledger(props: list[dict], scenes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    current = {}
    ledger = []
    errors = []
    warnings = []
    seen_transfers = set()

    for prop in props:
        pid = str(prop.get("id") or "")
        if not pid:
            continue
        initial_text = str(prop.get("initial_state") or "")
        dimensions = prop_state_dimensions(initial_text)
        current[pid] = {
            "owner": prop.get("initial_owner"),
            "container": prop.get("initial_owner") if str(prop.get("initial_owner") or "").startswith("INSIDE_") else None,
            "relations": {},
            "state_dimensions": dimensions,
            "state": _legacy_state_from_dimensions(dimensions, _normalize_initial_state(initial_text)),
        }
        ledger.append({"scene_id": "INITIAL", "prop_id": pid, **deepcopy(current[pid]), "event": "initial_state"})

    for scene in scenes:
        sid = scene["id"]
        start_struct = scene.get("start_state_structured") or {}
        end_struct = scene.get("end_state_structured") or {}

        for transfer in scene.get("prop_transfers") or []:
            if not isinstance(transfer, dict):
                continue
            pid = str(transfer.get("prop_id") or "")
            source = transfer.get("source_owner")
            target = transfer.get("target_owner")
            if not pid:
                continue
            if pid not in current:
                errors.append({"scene": sid, "code": "UNKNOWN_PROP_TRANSFER", "detail": pid})
                continue
            expected = current[pid].get("owner")
            if source and expected and source != expected:
                errors.append({
                    "scene": sid,
                    "code": "PROP_OWNER_MISMATCH",
                    "detail": f"{pid}: expected source {expected}, got {source}",
                })
            key = (pid, source, target)
            if key in seen_transfers:
                errors.append({"scene": sid, "code": "DUPLICATE_PROP_TRANSFER", "detail": str(key)})
            seen_transfers.add(key)
            if target:
                current[pid]["owner"] = target
                current[pid]["container"] = target if str(target).startswith("INSIDE_") else None
            if str(source or "").startswith("INSIDE_") and target:
                dims = dict(current[pid].get("state_dimensions") or {})
                dims["visibility_state"] = "revealed"
                current[pid]["state_dimensions"] = dims
                current[pid]["state"] = _legacy_state_from_dimensions(dims, current[pid].get("state"))
            ledger.append({
                "scene_id": sid,
                "prop_id": pid,
                **deepcopy(current[pid]),
                "event": "transfer",
                "source_owner": source,
                "target_owner": target,
                "moment": transfer.get("moment"),
            })

        action_low = " ".join([
            str(scene.get("action") or ""),
            str(scene.get("start_state") or ""),
            str(scene.get("end_state") or ""),
        ]).lower()
        mounted = re.search(r"(PROP_\d+).*?(?:on\s+(?:the\s+)?platter|onto\s+(?:the\s+)?platter|lên\s+mâm|trên\s+mâm)", action_low, re.I)
        if mounted:
            mounted_prop = mounted.group(1).upper()
            device_candidates = [
                candidate for candidate in (scene.get("props_present") or [])
                if candidate != mounted_prop
                and candidate in current
                and "disc_state" in (current[candidate].get("state_dimensions") or {})
            ]
            if mounted_prop in current and device_candidates:
                device_pid = device_candidates[0]
                current[mounted_prop].setdefault("relations", {})["mounted_on"] = device_pid
                current[device_pid].setdefault("state_dimensions", {})["disc_state"] = "loaded"

        for pid in scene.get("props_present") or []:
            if pid not in current:
                errors.append({"scene": sid, "code": "UNKNOWN_PROP_PRESENT", "detail": pid})
                continue
            start_info = (start_struct.get("props") or {}).get(pid) or {}
            end_info = (end_struct.get("props") or {}).get(pid) or {}
            inferred_owner = end_info.get("owner")
            if inferred_owner:
                current[pid]["owner"] = inferred_owner
                current[pid]["container"] = None

            old_dims = dict(current[pid].get("state_dimensions") or {})
            new_dims = dict(end_info.get("state_dimensions") or {})
            action_dims = _derive_action_state_dimensions(scene, old_dims)
            new_dims.update(action_dims)
            action_text = " ".join([
                str(scene.get("action") or ""),
                str(scene.get("start_state") or ""),
                str(scene.get("end_state") or ""),
            ])
            for dimension, new_value in new_dims.items():
                old_value = old_dims.get(dimension)
                if old_value and new_value and old_value != new_value:
                    if not _dimension_transition_allowed(dimension, old_value, new_value, action_text):
                        errors.append({
                            "scene": sid,
                            "code": "INVALID_PROP_STATE_TRANSITION",
                            "detail": f"{pid}.{dimension}: {old_value} -> {new_value}",
                            "prop_id": pid,
                            "dimension": dimension,
                            "from_state": old_value,
                            "to_state": new_value,
                        })
                if new_value:
                    old_dims[dimension] = new_value

            # For simple legacy states that have no dimensional representation.
            new_state = end_info.get("state")
            if new_state and not new_dims and not old_dims:
                old_state = current[pid].get("state")
                if old_state and old_state != new_state and not _dimension_transition_allowed("mechanical_state", old_state, new_state, action_text):
                    errors.append({
                        "scene": sid,
                        "code": "INVALID_PROP_STATE_TRANSITION",
                        "detail": f"{pid}: {old_state} -> {new_state}",
                        "prop_id": pid,
                        "dimension": "mechanical_state",
                        "from_state": old_state,
                        "to_state": new_state,
                    })
                current[pid]["state"] = new_state
            else:
                current[pid]["state_dimensions"] = old_dims
                current[pid]["state"] = _legacy_state_from_dimensions(old_dims, current[pid].get("state"))

            ledger.append({"scene_id": sid, "prop_id": pid, **deepcopy(current[pid]), "event": "end_state"})

    return ledger, errors, warnings


def _event_ledger(scenes: list[dict], prop_ledger: list[dict]) -> tuple[list[dict], list[dict]]:
    events = []
    errors = []
    seen_unique = set()

    previous_prop_state: dict[str, dict] = {}
    for row in prop_ledger:
        pid = str(row.get("prop_id") or "")
        if not pid:
            continue
        if row.get("scene_id") == "INITIAL":
            previous_prop_state[pid] = {
                "owner": row.get("owner"),
                "state": row.get("state"),
                "container": row.get("container"),
            }
            continue

        scene_id = row.get("scene_id")
        current = {
            "owner": row.get("owner"),
            "state": row.get("state"),
            "container": row.get("container"),
        }
        prev = previous_prop_state.get(pid, {})

        if row.get("event") == "transfer":
            source = row.get("source_owner")
            target = row.get("target_owner")
            event_type = "PROP_REVEAL" if str(source or "").startswith("INSIDE_") else "PROP_TRANSFER"
            unique_key = f"{event_type}:{pid}:{source}->{target}"
            if unique_key in seen_unique:
                errors.append({
                    "scene": scene_id,
                    "code": "DUPLICATE_EVENT",
                    "detail": unique_key,
                })
            seen_unique.add(unique_key)
            events.append({
                "scene_id": scene_id,
                "event_type": event_type,
                "entity_id": pid,
                "source_owner": source,
                "target_owner": target,
                "moment": row.get("moment"),
                "unique_key": unique_key,
            })

        old_state, new_state = prev.get("state"), current.get("state")
        if old_state and new_state and old_state != new_state:
            unique_key = f"PROP_STATE_CHANGE:{pid}:{old_state}->{new_state}"
            # Repeating the exact same irreversible state change later is invalid.
            if unique_key in seen_unique:
                errors.append({
                    "scene": scene_id,
                    "code": "DUPLICATE_EVENT",
                    "detail": unique_key,
                })
            seen_unique.add(unique_key)
            events.append({
                "scene_id": scene_id,
                "event_type": "PROP_STATE_CHANGE",
                "entity_id": pid,
                "from_state": old_state,
                "to_state": new_state,
                "unique_key": unique_key,
            })
        previous_prop_state[pid] = current

    # Scene beats are recorded for traceability. Exact repeated source beats are suspicious.
    seen_beats: dict[str, str] = {}
    for scene in scenes:
        action = _norm_text(scene.get("action") or "").lower()
        if not action:
            continue
        beat_hash = _sha256(action)
        if beat_hash in seen_beats:
            errors.append({
                "scene": scene.get("id"),
                "code": "DUPLICATE_SOURCE_BEAT",
                "detail": f"Same action as {seen_beats[beat_hash]}",
            })
        else:
            seen_beats[beat_hash] = scene.get("id")
        events.append({
            "scene_id": scene.get("id"),
            "event_type": "SCENE_BEAT",
            "entity_id": None,
            "source_hash": beat_hash,
            "action": scene.get("action") or "",
        })

    return events, errors


def _transition_gate(scenes: list[dict]) -> list[dict]:
    issues = []
    for i in range(1, len(scenes)):
        prev = scenes[i - 1]
        cur = scenes[i]
        if (cur.get("continuity") or {}).get("previous_scene") != prev.get("id"):
            issues.append({
                "scene": cur.get("id"),
                "code": "PREVIOUS_SCENE_LINK",
                "detail": f"Expected {prev.get('id')}",
            })
        prev_end = prev.get("end_state_structured") or {}
        cur_start = cur.get("start_state_structured") or {}
        prev_props = prev_end.get("props") or {}
        cur_props = cur_start.get("props") or {}

        prev_loc = prev.get("location_id")
        cur_loc = cur.get("location_id")
        shared_characters = set(prev.get("characters") or []) & set(cur.get("characters") or [])
        if prev_loc and cur_loc and prev_loc != cur_loc and shared_characters:
            transition_text = (
                str(prev.get("end_state") or "") + " " +
                str(cur.get("start_state") or "") + " " +
                str(cur.get("action") or "")
            ).lower()
            explicit_movement = bool(re.search(
                r"\b(?:enter(?:s|ed|ing)?|exit(?:s|ed|ing)?|leave(?:s|d|ing)?|"
                r"walk(?:s|ed|ing)?\s+(?:in|into|out|outside)|"
                r"bước\s+(?:vào|ra)|đi\s+(?:vào|ra)|rời\s+(?:khỏi|nhà|phòng|ngõ)|"
                r"qua\s+(?:cửa|ngưỡng)|ngưỡng\s+cửa)\b",
                transition_text,
                re.I,
            ))
            boundary_ready = any(token in str(prev.get("end_state") or "").lower() for token in ("at threshold", "on threshold", "ở ngưỡng", "tại ngưỡng"))
            time_jump = any(token in transition_text for token in (
                "later", "hours later", "next morning", "next day", "cut to",
                "một lúc sau", "vài giờ sau", "sáng hôm sau", "ngày hôm sau", "chuyển cảnh"
            ))
            if not explicit_movement and not boundary_ready and not time_jump:
                issues.append({
                    "scene": cur.get("id"),
                    "code": "LOCATION_TELEPORT",
                    "detail": f"{prev_loc} -> {cur_loc} for {sorted(shared_characters)} without explicit movement event",
                })

        weather_rank = {"clear": 0, "drizzle": 1, "light_rain": 2, "rain": 3}
        prev_weather = prev_end.get("weather")
        cur_weather = cur_start.get("weather")
        if prev_weather in weather_rank and cur_weather in weather_rank:
            if weather_rank[cur_weather] > weather_rank[prev_weather]:
                weather_text = (str(cur.get("start_state") or "") + " " + str(cur.get("action") or "")).lower()
                intensify = ("heavier", "stronger", "intens", "mưa lớn", "mưa nặng", "to hơn", "nặng hơn")
                if not any(token in weather_text for token in intensify):
                    issues.append({
                        "scene": cur.get("id"),
                        "code": "WEATHER_REGRESSION",
                        "detail": f"{prev_weather} -> {cur_weather} without source event",
                    })

        for pid in set(prev_props) & set(cur_props):
            a, b = prev_props[pid], cur_props[pid]
            if a.get("owner") and b.get("owner") and a["owner"] != b["owner"]:
                issues.append({
                    "scene": cur.get("id"),
                    "code": "START_END_PROP_OWNER_MISMATCH",
                    "detail": f"{pid}: {a['owner']} -> {b['owner']}",
                })
            if a.get("state") and b.get("state") and a["state"] != b["state"]:
                issues.append({
                    "scene": cur.get("id"),
                    "code": "START_END_PROP_STATE_MISMATCH",
                    "detail": f"{pid}: {a['state']} -> {b['state']}",
                })
    return issues


def _dialogue_gate(scenes: list[dict], characters: list[dict]) -> list[dict]:
    char_ids = {str(x.get("id")) for x in characters}
    issues = []
    for scene in scenes:
        onscreen = set(scene.get("characters") or [])
        for line in scene.get("dialogue") or []:
            if not isinstance(line, dict):
                issues.append({"scene": scene["id"], "code": "DIALOGUE_SCHEMA", "detail": "Dialogue item is not object"})
                continue
            speaker = line.get("character_id")
            text = str(line.get("text") or "").strip()
            if not speaker or speaker not in char_ids:
                issues.append({"scene": scene["id"], "code": "DIALOGUE_UNKNOWN_SPEAKER", "detail": str(speaker)})
            elif speaker not in onscreen:
                issues.append({"scene": scene["id"], "code": "DIALOGUE_SPEAKER_OFFSCREEN", "detail": str(speaker)})
            if not text:
                issues.append({"scene": scene["id"], "code": "DIALOGUE_EMPTY", "detail": "Empty dialogue"})
    return issues


def _voiceover_gate(scenes: list[dict]) -> list[dict]:
    issues = []
    for scene in scenes:
        voice = str(scene.get("voiceover") or "").strip()
        if not voice:
            continue
        words = len(voice.split())
        duration = float(scene.get("duration") or 8)
        if words > duration * 3.2:
            issues.append({
                "scene": scene["id"],
                "code": "VOICEOVER_TOO_LONG",
                "detail": f"{words} words for {duration:g}s",
            })
    return issues


def parse_structured_source(text: str, settings: dict | None = None) -> dict | None:
    raw = str(text or "")
    if STRUCTURED_FORMAT not in raw or not re.search(r"(?m)^SCENE_\d+\s*$", raw):
        return None

    labels = ["META", "CHARACTER_BIBLE", "LOCATION_BIBLE", "PROP_BIBLE", "TIMELINE", "SCENES (total duration = 120s)", "CONTINUITY_AUDIT"]
    meta_body = _section(raw, "META", labels[1:])
    char_body = _section(raw, "CHARACTER_BIBLE", labels[2:])
    loc_body = _section(raw, "LOCATION_BIBLE", labels[3:])
    prop_body = _section(raw, "PROP_BIBLE", labels[4:])
    timeline_body = _section(raw, "TIMELINE", labels[5:])
    meta = _parse_flat_fields(meta_body)

    characters = _canon_characters(_parse_entities(char_body, "CHAR"))
    locations = _canon_locations(_parse_entities(loc_body, "LOC"))
    props = _canon_props(_parse_entities(prop_body, "PROP"))

    scene_matches = list(re.finditer(r"(?m)^(SCENE_\d+)\s*$", raw))
    scenes = []
    for idx, match in enumerate(scene_matches):
        end = scene_matches[idx + 1].start() if idx + 1 < len(scene_matches) else len(raw)
        audit_pos = raw.find("\nCONTINUITY_AUDIT\n", match.end(), end)
        if audit_pos >= 0:
            end = audit_pos
        raw_block = raw[match.start():end].strip()
        block = raw[match.end():end]
        scene = _parse_scene_block(match.group(1), block, raw_block)
        scene["start_state_structured"] = structure_state(
            scene.get("start_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )
        scene["end_state_structured"] = structure_state(
            scene.get("end_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )
        scenes.append(scene)

    if not scenes or not characters or not locations:
        return None

    char_ids = [x["id"] for x in characters]
    loc_ids = [x["id"] for x in locations]
    prop_ids = [x["id"] for x in props]

    errors = []
    warnings = []
    errors.extend(_canon_completeness_errors(characters, locations, props))
    if len(char_ids) != len(set(char_ids)):
        errors.append({"scene": "PROJECT", "code": "DUPLICATE_CHARACTER_ID", "detail": "Character IDs are not unique"})
    if len(loc_ids) != len(set(loc_ids)):
        errors.append({"scene": "PROJECT", "code": "DUPLICATE_LOCATION_ID", "detail": "Location IDs are not unique"})
    if len(prop_ids) != len(set(prop_ids)):
        errors.append({"scene": "PROJECT", "code": "DUPLICATE_PROP_ID", "detail": "Prop IDs are not unique"})

    char_set, loc_set, prop_set = set(char_ids), set(loc_ids), set(prop_ids)
    for idx, scene in enumerate(scenes, 1):
        expected = f"SCENE_{idx:03d}" if idx <= 999 else f"SCENE_{idx:04d}"
        if scene["id"] != expected:
            errors.append({"scene": scene["id"], "code": "SCENE_ORDER", "detail": f"Expected {expected}"})
        for cid in scene.get("characters") or []:
            if cid not in char_set:
                errors.append({"scene": scene["id"], "code": "UNKNOWN_CHARACTER", "detail": cid})
        if scene.get("location_id") and scene["location_id"] not in loc_set:
            errors.append({"scene": scene["id"], "code": "UNKNOWN_LOCATION", "detail": str(scene["location_id"])})
        for pid in scene.get("props_present") or []:
            if pid not in prop_set:
                errors.append({"scene": scene["id"], "code": "UNKNOWN_PROP", "detail": pid})
        scene["gate"] = {
            "source_fidelity": {"passed": True, "hash": scene["source_hash"]},
            "referential_integrity": {"passed": True},
            "dialogue": {"passed": True},
            "voiceover": {"passed": True},
            "start_end": {"passed": True},
            "prop_state": {"passed": True},
        }

    dialogue_errors = _dialogue_gate(scenes, characters)
    voice_errors = _voiceover_gate(scenes)
    transition_errors = _transition_gate(scenes)
    ledger, prop_errors, prop_warnings = _prop_ledger(props, scenes)
    event_ledger, event_errors = _event_ledger(scenes, ledger)

    errors.extend(dialogue_errors)
    errors.extend(voice_errors)
    errors.extend(transition_errors)
    errors.extend(prop_errors)
    errors.extend(event_errors)
    warnings.extend(prop_warnings)

    by_scene = {}
    for issue in errors + warnings:
        by_scene.setdefault(issue.get("scene"), []).append(issue)
    for scene in scenes:
        scene_issues = by_scene.get(scene["id"], [])
        scene["warnings"] = [
            f"{x['code']}: {x.get('detail','')}" for x in scene_issues if x in warnings
        ]
        scene["gate"]["dialogue"]["passed"] = not any(x["code"].startswith("DIALOGUE") for x in scene_issues)
        scene["gate"]["voiceover"]["passed"] = not any(x["code"].startswith("VOICEOVER") for x in scene_issues)
        scene["gate"]["start_end"]["passed"] = not any(
            x["code"].startswith("START_END")
            or x["code"] in {"PREVIOUS_SCENE_LINK", "LOCATION_TELEPORT", "WEATHER_REGRESSION"}
            for x in scene_issues
        )
        scene["gate"]["prop_state"]["passed"] = not any("PROP_" in x["code"] for x in scene_issues)
        scene["gate"]["referential_integrity"]["passed"] = not any(x["code"].startswith("UNKNOWN_") for x in scene_issues)

    story_bible = {
        "theme": meta.get("theme") or "",
        "genre": meta.get("genre") or "",
        "purpose": meta.get("purpose") or "",
        "audience": meta.get("audience") or "",
        "synopsis": meta.get("synopsis") or "",
        "language": meta.get("language") or "",
        "source_locked": True,
        "compiler": "deterministic_source_v1",
    }
    title = str(meta.get("title") or "Dự án phim")
    visual_style = str(meta.get("visual_style") or (settings or {}).get("style") or "Cinematic")
    timeline = _timeline_section(timeline_body)
    duration_sum = sum(float(x.get("duration") or 0) for x in scenes)

    source_manifest = {
        "format": STRUCTURED_FORMAT,
        "source_hash": _sha256(raw),
        "scene_count": len(scenes),
        "duration_sum": duration_sum,
        "scene_hashes": {scene["id"]: scene["source_hash"] for scene in scenes},
        "source_locked": True,
        "dialogue_locked": True,
        "voiceover_locked": True,
        "canon_locked": True,
    }

    gates = {
        "SOURCE_FIDELITY": len([x for x in errors if x["code"].startswith("SOURCE_")]) == 0,
        "CANON_LOCK": bool(characters and locations and props) and not any(x["code"].endswith("_CANON_INCOMPLETE") for x in errors),
        "CHARACTER_LOCK": all(x.get("canonical_locked") for x in characters),
        "LOCATION_LOCK": all(x.get("canonical_locked") for x in locations),
        "PROP_STATE": not any("PROP_" in x["code"] for x in errors),
        "OWNERSHIP": not any(x["code"] == "PROP_OWNER_MISMATCH" for x in errors),
        "EVENT_ORDER": not any(x["code"] in {"DUPLICATE_PROP_TRANSFER", "DUPLICATE_EVENT", "DUPLICATE_SOURCE_BEAT"} for x in errors),
        "START_END": not any(
            x["code"].startswith("START_END")
            or x["code"] in {"PREVIOUS_SCENE_LINK", "LOCATION_TELEPORT", "WEATHER_REGRESSION"}
            for x in errors
        ),
        "DIALOGUE": not any(x["code"].startswith("DIALOGUE") for x in errors),
        "VOICEOVER": not any(x["code"].startswith("VOICEOVER") for x in errors),
        "REFERENTIAL_INTEGRITY": not any(x["code"].startswith("UNKNOWN_") for x in errors),
    }
    final_gate = all(gates.values()) and not errors
    integrity = {
        "version": "batch-a-v1",
        "source_mode": "deterministic_structured",
        "gates": gates,
        "final_gate": final_gate,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "prop_ledger": ledger,
        "event_ledger": event_ledger,
    }

    master_prompt = (
        f"Project {title}. Canon is immutable. Preserve exact character identity, wardrobe, location layout, "
        "prop ownership/state timeline, source action, exact dialogue and voiceover. Never invent or repeat story events."
    )

    return {
        "project_title": title,
        "story_bible": story_bible,
        "characters": characters,
        "locations": locations,
        "props": props,
        "timeline": timeline,
        "master_prompt": master_prompt,
        "visual_style": visual_style,
        "source_manifest": source_manifest,
        "integrity": integrity,
        "scenes": scenes,
    }


def enforce_source_snapshot(scene: dict) -> None:
    snapshot = scene.get("source_snapshot") or {}
    if not snapshot:
        return
    mapping = {
        "duration": "duration",
        "characters": "characters",
        "location_id": "location_id",
        "props_present": "props_present",
        "prop_transfers": "prop_transfers",
        "action": "action",
        "camera": "camera",
        "lighting": "lighting",
        "atmosphere": "atmosphere",
        "dialogue": "dialogue",
        "voiceover": "voiceover",
        "start_state": "start_state",
        "end_state": "end_state",
    }
    for source_key, target_key in mapping.items():
        if source_key in snapshot:
            scene[target_key] = deepcopy(snapshot[source_key])
    scene["source_text"] = str(snapshot.get("action") or scene.get("source_text") or "")
    scene["source_hash"] = _sha256(snapshot)
    scene["start_state_structured"] = structure_state(
        scene.get("start_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
    )
    scene["end_state_structured"] = structure_state(
        scene.get("end_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
    )


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def finalize_ai_integrity(original_text: str, result: dict) -> dict:
    """Strict fallback gate for free-form scripts analyzed by AI.

    The model may choose scene boundaries, but persisted source_text must be a
    verbatim excerpt of the original input. Once accepted, source-derived
    fields are snapshotted and locked exactly like deterministic V1 scenes.
    """
    out = result
    errors = []
    warnings = []
    original_norm = _norm_text(original_text)

    out["characters"] = _canon_characters(out.get("characters") or [])
    out["locations"] = _canon_locations(out.get("locations") or [])

    normalized_props = []
    for prop in out.get("props") or []:
        row = deepcopy(prop) if isinstance(prop, dict) else {}
        if not row:
            continue
        if not row.get("owner_initial") and row.get("owner"):
            row["owner_initial"] = row.get("owner")
        if not row.get("state_initial") and row.get("state"):
            row["state_initial"] = row.get("state")
        normalized_props.append(row)
    out["props"] = _canon_props(normalized_props)

    char_ids = {str(x.get("id")) for x in out["characters"] if x.get("id")}
    loc_ids = {str(x.get("id")) for x in out["locations"] if x.get("id")}
    prop_ids = {str(x.get("id")) for x in out["props"] if x.get("id")}
    errors.extend(_canon_completeness_errors(out["characters"], out["locations"], out["props"]))

    for scene in out.get("scenes") or []:
        sid = str(scene.get("id") or "UNKNOWN")
        excerpt = _norm_text(scene.get("source_text") or "")
        source_ok = bool(excerpt and excerpt in original_norm)
        if not source_ok:
            errors.append({
                "scene": sid,
                "code": "SOURCE_EXCERPT_NOT_VERBATIM",
                "detail": "source_text không phải trích đoạn nguyên văn từ input.",
            })

        for cid in scene.get("characters") or []:
            if cid not in char_ids:
                errors.append({"scene": sid, "code": "UNKNOWN_CHARACTER", "detail": str(cid)})
        lid = scene.get("location_id")
        if lid and lid not in loc_ids:
            errors.append({"scene": sid, "code": "UNKNOWN_LOCATION", "detail": str(lid)})

        scene_blob = " ".join(
            str(scene.get(key) or "")
            for key in ("source_text", "action", "start_state", "end_state")
        )
        props_present = sorted(set(re.findall(r"PROP_\d+", scene_blob)))
        scene["props_present"] = [pid for pid in props_present if pid in prop_ids]

        dialogue = scene.get("dialogue") or []
        for line in dialogue:
            if not isinstance(line, dict):
                errors.append({"scene": sid, "code": "DIALOGUE_SCHEMA", "detail": "Dialogue item không phải object"})
                continue
            speaker = line.get("character_id")
            text = _norm_text(line.get("text") or "")
            if speaker and speaker not in (scene.get("characters") or []):
                errors.append({"scene": sid, "code": "DIALOGUE_SPEAKER_OFFSCREEN", "detail": str(speaker)})
            if text and excerpt and text not in excerpt:
                errors.append({
                    "scene": sid,
                    "code": "DIALOGUE_NOT_IN_SOURCE_EXCERPT",
                    "detail": text[:180],
                })

        voice = _norm_text(scene.get("voiceover") or "")
        if voice and excerpt and voice not in excerpt:
            errors.append({
                "scene": sid,
                "code": "VOICEOVER_NOT_IN_SOURCE_EXCERPT",
                "detail": voice[:180],
            })

        snapshot = {
            "id": sid,
            "duration": float(scene.get("duration") or 8),
            "characters": list(scene.get("characters") or []),
            "location_id": lid,
            "props_present": list(scene.get("props_present") or []),
            "prop_transfers": list(scene.get("prop_transfers") or []),
            "action": str(scene.get("action") or ""),
            "camera": str(scene.get("camera") or ""),
            "lighting": str(scene.get("lighting") or ""),
            "atmosphere": str(scene.get("atmosphere") or ""),
            "dialogue": deepcopy(dialogue),
            "voiceover": str(scene.get("voiceover") or ""),
            "start_state": str(scene.get("start_state") or ""),
            "end_state": str(scene.get("end_state") or ""),
            "source_excerpt": str(scene.get("source_text") or ""),
        }
        scene["source_snapshot"] = snapshot
        scene["source_hash"] = _sha256(snapshot)
        scene["start_state_structured"] = structure_state(
            scene.get("start_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )
        scene["end_state_structured"] = structure_state(
            scene.get("end_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )
        scene["gate"] = {
            "source_fidelity": {"passed": source_ok, "hash": scene["source_hash"]},
            "referential_integrity": {
                "passed": not any(x["scene"] == sid and x["code"].startswith("UNKNOWN_") for x in errors)
            },
            "dialogue": {
                "passed": not any(x["scene"] == sid and x["code"].startswith("DIALOGUE") for x in errors)
            },
            "voiceover": {
                "passed": not any(x["scene"] == sid and x["code"].startswith("VOICEOVER") for x in errors)
            },
            "start_end": {"passed": True},
            "prop_state": {"passed": True},
        }

    transition_errors = _transition_gate(out.get("scenes") or [])
    dialogue_errors = _dialogue_gate(out.get("scenes") or [], out.get("characters") or [])
    voice_errors = _voiceover_gate(out.get("scenes") or [])
    ledger, prop_errors, prop_warnings = _prop_ledger(out.get("props") or [], out.get("scenes") or [])
    event_ledger, event_errors = _event_ledger(out.get("scenes") or [], ledger)
    errors.extend(transition_errors)
    errors.extend(dialogue_errors)
    errors.extend(voice_errors)
    errors.extend(prop_errors)
    errors.extend(event_errors)
    warnings.extend(prop_warnings)

    gates = {
        "SOURCE_FIDELITY": not any(x["code"].startswith("SOURCE_") or x["code"].endswith("_NOT_IN_SOURCE_EXCERPT") for x in errors),
        "CANON_LOCK": bool(out.get("characters") and out.get("locations")) and not any(x["code"].endswith("_CANON_INCOMPLETE") for x in errors),
        "CHARACTER_LOCK": all(x.get("canonical_locked") for x in out.get("characters") or []),
        "LOCATION_LOCK": all(x.get("canonical_locked") for x in out.get("locations") or []),
        "PROP_STATE": not any("PROP_" in x["code"] for x in errors),
        "OWNERSHIP": not any(x["code"] == "PROP_OWNER_MISMATCH" for x in errors),
        "EVENT_ORDER": not any(x["code"] in {"DUPLICATE_PROP_TRANSFER", "DUPLICATE_EVENT", "DUPLICATE_SOURCE_BEAT"} for x in errors),
        "START_END": not any(
            x["code"].startswith("START_END")
            or x["code"] in {"PREVIOUS_SCENE_LINK", "LOCATION_TELEPORT", "WEATHER_REGRESSION"}
            for x in errors
        ),
        "DIALOGUE": not any(x["code"].startswith("DIALOGUE") for x in errors),
        "VOICEOVER": not any(x["code"].startswith("VOICEOVER") for x in errors),
        "REFERENTIAL_INTEGRITY": not any(x["code"].startswith("UNKNOWN_") for x in errors),
    }
    final_gate = all(gates.values()) and not errors
    out["source_manifest"] = {
        "format": "FREEFORM_SOURCE_LOCKED",
        "source_hash": _sha256(original_text),
        "scene_count": len(out.get("scenes") or []),
        "scene_hashes": {x.get("id"): x.get("source_hash") for x in out.get("scenes") or []},
        "source_locked": True,
        "dialogue_locked": True,
        "voiceover_locked": True,
        "canon_locked": True,
    }
    out["integrity"] = {
        "version": "batch-a-v1",
        "source_mode": "ai_boundaries_verbatim_source",
        "gates": gates,
        "final_gate": final_gate,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "prop_ledger": ledger,
        "event_ledger": event_ledger,
    }
    return out


def verify_source_lock(scene: dict) -> tuple[bool, str | None]:
    snapshot = scene.get("source_snapshot") or {}
    source_hash = scene.get("source_hash")
    if not snapshot or not source_hash:
        return False, "SOURCE_SNAPSHOT_MISSING"
    if _sha256(snapshot) != source_hash:
        return False, "SOURCE_HASH_INVALID"

    continuity = scene.get("continuity") or {}
    current_lookup = {
        "id": scene.get("id"),
        "duration": float(scene.get("duration") or 0),
        "characters": list(scene.get("characters") or []),
        "location_id": scene.get("location_id"),
        "props_present": list(scene.get("props_present") or []),
        "prop_transfers": list(scene.get("prop_transfers") or []),
        "action": str(scene.get("action") or ""),
        "camera": str(scene.get("camera") or ""),
        "lighting": str(scene.get("lighting") or ""),
        "atmosphere": str(scene.get("atmosphere") or ""),
        "dialogue": deepcopy(scene.get("dialogue") or []),
        "voiceover": str(scene.get("voiceover") or ""),
        "start_state": str(scene.get("start_state") or ""),
        "end_state": str(scene.get("end_state") or ""),
        "source_excerpt": str(scene.get("source_text") or ""),
        "previous_scene": continuity.get("previous_scene"),
        "next_scene": continuity.get("next_scene"),
    }
    immutable_keys = {
        "id", "duration", "characters", "location_id", "props_present", "prop_transfers",
        "action", "dialogue", "voiceover", "source_excerpt",
    }
    drift = []
    for key, expected in snapshot.items():
        if key not in immutable_keys or key not in current_lookup:
            continue
        if current_lookup[key] != expected:
            drift.append(key)
    if drift:
        return False, "SOURCE_FIELD_MUTATED:" + ",".join(sorted(drift))
    return True, None
