import asyncio
import base64
import io
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, Page, Playwright, async_playwright

from runtime_dependencies import ffmpeg_path

from .config import DATA_DIR, MEDIA_DIR, load_config, project_media_root
from .sessions import ACTIVE_PROFILE, port_open, set_flow_chrome_visibility, start_flow_chrome


class FlowBrowserError(RuntimeError):
    pass


def classify_flow_generation_error_text(text: str | None) -> tuple[str, str] | None:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return None
    lowered = raw.lower()
    policy_markers = (
        "vi phạm chính sách",
        "người nổi tiếng",
        "public figure",
        "policy violation",
        "violates our policy",
        "may violate",
    )
    if any(marker in lowered for marker in policy_markers):
        return "FLOW_POLICY_BLOCKED", raw
    failure_markers = (
        "không thành công",
        "không thể tạo",
        "couldn't generate",
        "generation failed",
        "failed to generate",
    )
    if any(marker in lowered for marker in failure_markers):
        return "FLOW_GENERATION_FAILED", raw
    return None


def classify_flow_session(
    *,
    url: str = "",
    title: str = "",
    workspace_ready: bool = False,
    login_prompt: bool = False,
    connected: bool = True,
) -> dict:
    url = url or ""
    redirected_to_login = "accounts.google.com" in url
    on_public_about = "/about" in url
    project_404 = "flow.google.com/404" in url or ("/404" in url and "reason=project" in url)
    if not connected:
        state = "BRIDGE_DISCONNECTED"
        account_authenticated = False
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "Không kết nối được Chrome Flow."
    elif redirected_to_login or login_prompt:
        state = "REAUTH_REQUIRED"
        account_authenticated = False
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "Flow cần đăng nhập lại bằng Chrome profile riêng."
    elif on_public_about:
        state = "REAUTH_REQUIRED"
        account_authenticated = False
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "Flow đang ở trang public, chưa vào workspace."
    elif project_404:
        state = "PROJECT_NOT_FOUND"
        account_authenticated = True
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "FLOW_PROJECT_NOT_FOUND: route project không dùng được."
    elif "flow.google.com" in url and workspace_ready:
        state = "AUTHENTICATED"
        account_authenticated = True
        page_usable = True
        project_usable = True
        authenticated = True
        reason = None
    elif "flow.google.com" in url:
        state = "SESSION_EXPIRED"
        account_authenticated = not redirected_to_login
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "Flow session không usable."
    else:
        state = "SESSION_EXPIRED"
        account_authenticated = False
        page_usable = False
        project_usable = False
        authenticated = False
        reason = "Không ở trang Flow."
    return {
        "connected": connected,
        "authenticated": authenticated,
        "account_authenticated": account_authenticated,
        "page_usable": page_usable,
        "project_usable": project_usable,
        "state": state,
        "url": url,
        "title": title,
        "reason": reason,
    }


def canonical_video_model_variants(label: str | None) -> list[str]:
    """Map config <-> capability <-> UI label for Flow video models.

    Ground truth 2026-09-21 (workspace bd06cd41): dropdown shows
    Omni 1.1 Flash, Veo 3.1 - Lite, Veo 3.1 - Fast, Veo 3.1 - Quality,
    Veo 3.1 - Lite [Lower Priority]. Config uses the Lower Priority
    variant. Never assume exact raw string: try exact, then stripped
    canonical, then base family.
    """
    raw = str(label or "").strip()
    if not raw:
        return []
    variants: list[str] = []
    for cand in (raw, re.sub(r"\s*\[Lower Priority\]\s*", "", raw).strip()):
        if cand and cand not in variants:
            variants.append(cand)
    # Base family fallback, e.g. "Veo 3.1 - Lite [Lower Priority]" -> "Veo 3.1 - Lite"
    # already covered; also try first two dash segments for Quality/Fast cousins?
    return variants


def model_selection_variants(model: str | None) -> list[str]:
    """Variants để click trong model dropdown.

    Credit guard: model [Lower Priority] chỉ dùng exact string, không bao giờ
    fallback sang bản base trả phí.
    """
    raw = str(model or "").strip()
    if not raw:
        return []
    if "[lower priority]" in raw.lower():
        return [raw]
    return canonical_video_model_variants(raw)


# Ordered fallback strategy for the Flow model selector (report §20):
# 1. aria-label canonical (Vietnamese UI: "Chon nhom mo hinh" contains "mo hinh")
# 2. role=button + visible text (Veo/Omni + dropdown arrow)
# 3. stable text fallback (exact model family)
MODEL_BUTTON_SELECTORS = (
    'button[aria-label*="mô hình"]:visible',
    'button[aria-label*="Mô hình"]:visible',
    'button[aria-label*="nhóm mô hình"]:visible',
    'button[aria-label*="Nhóm mô hình"]:visible',
    'button[aria-label*="model" i]:visible',
)
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SIGNED_CDN_HOSTS = ("flow-content.google", "googleusercontent.com", "lh3.googleusercontent.com")


def classify_flow_image_url(url: str) -> str:
    raw = (url or "").strip()
    lower = raw.lower()
    if lower.startswith("blob:") or lower.startswith("data:image/"):
        return "browser"
    host = (urlparse(raw).hostname or "").lower()
    if any(token in host for token in SIGNED_CDN_HOSTS):
        return "signed_cdn"
    if "/asb/" in lower or host in {"flow.google.com", "labs.google"}:
        return "protected"
    if lower.startswith("http://") or lower.startswith("https://"):
        return "unknown_http"
    return "invalid"


def validate_downloaded_image(content: bytes, content_type: str = "", final_url: str = "") -> dict:
    if not content:
        raise FlowBrowserError("FLOW_IMAGE_INVALID_RESPONSE: response body rỗng.")
    ctype = (content_type or "").split(";")[0].strip().lower()
    head = content[:2048]
    sniff = head.lstrip().lower()
    if (
        ctype.startswith("text/html")
        or sniff.startswith(b"<!doctype html")
        or sniff.startswith(b"<html")
        or b"accounts.google.com" in sniff
        or b"sign-in" in sniff
        or b">sign in<" in sniff
    ):
        raise FlowBrowserError(
            "FLOW_IMAGE_DOWNLOAD_AUTH_REQUIRED: Flow trả HTML đăng nhập thay vì ảnh."
        )
    if ctype and not ctype.startswith("image/") and ctype not in {"application/octet-stream", "binary/octet-stream"}:
        raise FlowBrowserError(f"FLOW_IMAGE_INVALID_RESPONSE: Content-Type {ctype} không phải ảnh.")
    if content.startswith(JPEG_MAGIC):
        mime, suffix = "image/jpeg", ".jpg"
    elif content.startswith(PNG_MAGIC):
        mime, suffix = "image/png", ".png"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        mime, suffix = "image/webp", ".webp"
    else:
        raise FlowBrowserError("FLOW_IMAGE_INVALID_RESPONSE: không nhận ra JPEG/PNG/WebP.")
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(content))
        image.verify()
        image = Image.open(io.BytesIO(content))
        image.load()
        width, height = image.size
    except FlowBrowserError:
        raise
    except Exception as exc:
        raise FlowBrowserError(f"FLOW_IMAGE_INVALID_RESPONSE: PIL không decode được ảnh: {exc}") from exc
    if not width or not height:
        raise FlowBrowserError("FLOW_IMAGE_INVALID_RESPONSE: ảnh không có kích thước.")
    return {"mime": mime, "suffix": suffix, "width": width, "height": height, "final_url": final_url}


class FlowBrowser:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._ui_lock = asyncio.Lock()

    async def connect(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        cfg = load_config()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                if not port_open(9223) and attempt == 0:
                    await asyncio.to_thread(start_flow_chrome)
                if not self._playwright:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(cfg["cdp_url"])
                return self._browser
            except Exception as exc:
                last_error = exc
                await self.close()
                if attempt == 0:
                    await asyncio.to_thread(start_flow_chrome)
                    await asyncio.sleep(1.2)
        raise FlowBrowserError(f"Không kết nối được Chrome CDP tại {cfg['cdp_url']}: {last_error}") from last_error

    async def flow_page(self) -> Page:
        browser = await self.connect()
        cfg = load_config()
        if not browser.contexts:
            raise FlowBrowserError("Chrome CDP chưa có browser context.")

        # Reuse the dedicated Flow tab first. During Google login the same tab is
        # temporarily on accounts.google.com, so keep using it instead of opening
        # another Chrome tab/window.
        for context in browser.contexts:
            for page in context.pages:
                if "flow.google.com" in page.url:
                    return page
        for context in browser.contexts:
            for page in context.pages:
                if "accounts.google.com" in page.url:
                    return page

        # The profile is dedicated to Flow. Reuse an existing restored/new-tab page
        # before creating anything new so app restarts keep one canonical tab.
        context = browser.contexts[0]
        page = next((item for item in context.pages if not item.is_closed()), None)
        if page is None:
            page = await context.new_page()
        await page.goto(cfg["flow_url"], wait_until="domcontentloaded", timeout=30000)
        return page

    async def list_projects(self) -> list[dict]:
        page = await self.flow_page()
        if "/project/" in page.url:
            await page.goto(load_config()["flow_url"], wait_until="domcontentloaded", timeout=30000)
        try:
            await page.locator('a[href^="/project/"]').first.wait_for(state="attached", timeout=10000)
        except Exception:
            await page.wait_for_timeout(4000)
        links = page.locator('a[href^="/project/"]')
        projects = []
        seen = set()
        for i in range(await links.count()):
            link = links.nth(i)
            href = await link.get_attribute("href") or ""
            project_id = href.rstrip("/").split("/")[-1]
            if not project_id or project_id in seen:
                continue
            seen.add(project_id)
            modified = None
            try:
                parent_text = (await link.locator("xpath=..").inner_text()).strip().replace("\n", " ")
                modified = parent_text.replace("edit", "").replace("delete", "").strip() or None
            except Exception:
                pass
            projects.append({"id": project_id, "href": href, "modified_label": modified})
        return projects

    async def video_capabilities(self, project_id: str | None = None) -> dict:
        if not project_id:
            projects = await self.list_projects()
            if not projects:
                raise FlowBrowserError("Không có Flow project để đọc capability.")
            project_id = projects[0]["id"]
        page = await self.flow_page()
        target_url = f"https://flow.google.com/project/{project_id}"
        if page.url != target_url:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        if "/404" in page.url and "reason=project" in page.url:
            raise FlowBrowserError(f"PROJECT_NOT_FOUND: Flow project {project_id} redirected to {page.url}.")
        await page.wait_for_timeout(2500)
        await self._switch_generation_mode(page, "Video")
        await self._ensure_settings_open(page)
        await page.wait_for_timeout(300)
        buttons = page.locator('button:visible')
        texts = []
        try:
            total_buttons = await buttons.count()
        except Exception:
            total_buttons = 0
        for i in range(min(total_buttons, 120)):
            try:
                text = (await buttons.nth(i).inner_text(timeout=1500)).strip().replace("\n", " ")
            except Exception:
                continue
            if text:
                texts.append(text)
        aspects = sorted({m.group(0) for text in texts for m in [re.search(r'\b\d+:\d+\b', text)] if m})
        resolutions = sorted({m.group(0) for text in texts for m in [re.search(r'\b\d{3,4}p\b', text)] if m})
        durations = sorted({int(m.group(1)) for text in texts for m in [re.search(r'\b(\d+)\s*(?:giây|s)\b', text.lower())] if m})
        outputs = sorted({int(m.group(1)) for text in texts for m in [re.search(r'^x(\d+)$', text.strip().lower())] if m})
        model_button, _strategy = await self._find_model_button(page)
        models = []
        if model_button is not None:
            await model_button.click(force=True)
            await page.wait_for_timeout(600)
            visible = page.locator('button:visible, [role="menuitem"]:visible')
            try:
                total_visible = await visible.count()
            except Exception:
                total_visible = 0
            for i in range(min(total_visible, 60)):
                try:
                    text = (await visible.nth(i).inner_text(timeout=1500)).strip().replace("\n", " ")
                except Exception:
                    continue
                cleaned = text.replace("volume_up", "").replace("arrow_drop_down", "").strip()
                if cleaned.startswith("Veo ") or cleaned.startswith("Omni "):
                    if cleaned not in models:
                        models.append(cleaned)
            await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
        return {
            "project_id": project_id,
            "modes": ["video"],
            "models": models,
            "aspect_ratios": aspects,
            "resolutions": resolutions,
            "durations": durations,
            "output_counts": outputs,
        }

    def _aspect_needles(self, aspect_ratio: str) -> tuple[str, ...]:
        compact = re.sub(r"\s+", "", str(aspect_ratio or ""))
        aliases = {
            "1:1": ("1:1", "1 : 1", "Vuông", "Square", "crop_square"),
            "16:9": ("16:9", "16 : 9", "Ngang", "Landscape", "crop_16_9"),
            "9:16": ("9:16", "9 : 16", "Dọc", "Portrait", "crop_9_16"),
        }
        extra = aliases.get(compact, ())
        ordered = []
        for item in (aspect_ratio, compact, *extra):
            if item and item not in ordered:
                ordered.append(item)
        return tuple(ordered)

    async def _click_visible_button(self, page: Page, *needles: str) -> None:
        locators = [page.locator("button:visible"), page.locator('[role="menuitem"]:visible')]
        candidates = []
        icon_tokens = ("volume_up", "arrow_drop_down", "videocam", "image", "crop_free", "chrome_extension", "crop_16_9", "crop_9_16", "crop_square", "crop_portrait", "crop_landscape")
        raw_needles = [needle for needle in needles if needle]

        def _norm(value: str) -> str:
            text = (value or "").strip().replace("\n", " ")
            for token in icon_tokens:
                text = text.replace(token, "")
            text = text.replace("：", ":").replace("/", ":")
            return re.sub(r"\s+", "", text).lower()

        wanted = [_norm(needle) for needle in raw_needles]
        wanted_raw = [needle.lower() for needle in raw_needles]
        for locator in locators:
            for i in range(await locator.count()):
                button = locator.nth(i)
                try:
                    text = (await button.inner_text(timeout=800)).strip().replace("\n", " ")
                    aria = str(await button.get_attribute("aria-label") or "")
                except Exception:
                    continue
                blob = f"{text} {aria}".strip()
                cleaned = blob
                for token in icon_tokens:
                    cleaned = cleaned.replace(token, "")
                candidates.append((button, blob, cleaned.strip(), _norm(blob)))
        for button, blob, cleaned, normed in candidates:
            blob_l = blob.lower()
            if any(normed == needle for needle in wanted if needle) or any(cleaned == needle for needle in raw_needles):
                await button.click(force=True)
                return
            if any(token in blob_l for token in wanted_raw if token.startswith("crop_")):
                await button.click(force=True)
                return
        for button, blob, cleaned, normed in candidates:
            if any(needle and needle in normed for needle in wanted) or any(needle in cleaned for needle in raw_needles):
                await button.click(force=True)
                return
        raise FlowBrowserError(f"Không tìm thấy nút Flow: {needles}")

    async def _find_model_button(self, page: Page):
        """Find Flow model trigger using live-DOM-backed fallbacks.

        Current UI (2026-09-22) exposes no stable data-* attributes. Prefer:
        aria-label -> Material menu-trigger class + model evidence -> visible text.
        Returns (locator, strategy_name) or (None, 'missing').
        """
        for selector in MODEL_BUTTON_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.count():
                    return loc, f"aria:{selector}"
            except Exception:
                continue

        # Stable framework-class fallback. Never accept a generic menu trigger
        # unless its own text/aria proves it is the model control.
        try:
            candidates = page.locator("button.mat-mdc-menu-trigger:visible")
            total = min(await candidates.count(), 40)
            for i in range(total):
                candidate = candidates.nth(i)
                try:
                    text = (await candidate.inner_text(timeout=300)).strip()
                    aria = str(await candidate.get_attribute("aria-label") or "")
                except Exception:
                    continue
                blob = f"{text} {aria}".lower()
                if "veo" in blob or "omni" in blob or "mô hình" in blob or "model" in blob:
                    return candidate, "class:mat-mdc-menu-trigger+model-evidence"
        except Exception:
            pass

        # role=button + visible text fallback: model family names with dropdown arrow
        try:
            candidates = page.locator("button:visible")
            total = min(await candidates.count(), 60)
            for i in range(total):
                try:
                    text = (await candidates.nth(i).inner_text(timeout=300)).strip()
                except Exception:
                    continue
                if ("Veo" in text or "Omni" in text) and ("arrow_drop_down" in text or "▼" in text or len(text) < 60):
                    return candidates.nth(i), "text:model-family"
        except Exception:
            pass
        return None, "missing"

    async def _selector_evidence(self, page: Page) -> dict:
        """Small non-prompt DOM snapshot for actionable Flow UI failures."""
        evidence: dict = {"url": page.url}
        try:
            radios = page.locator('[role="radio"]:visible')
            values = []
            for i in range(min(await radios.count(), 12)):
                try:
                    values.append((await radios.nth(i).inner_text(timeout=300)).strip().replace("\n", " "))
                except Exception:
                    continue
            evidence["mode_controls"] = values
        except Exception:
            evidence["mode_controls"] = []
        try:
            model_button, strategy = await self._find_model_button(page)
            evidence["model_strategy"] = strategy
            evidence["model_text"] = (
                (await model_button.inner_text(timeout=500)).strip().replace("\n", " ")
                if model_button is not None else None
            )
        except Exception:
            evidence["model_strategy"] = "evidence_error"
            evidence["model_text"] = None
        try:
            settings = page.locator(
                'button.settings-trigger-button:visible, '
                'button[aria-label*="cài đặt"]:visible, '
                'button[aria-label*="settings" i]:visible'
            ).first
            evidence["settings_text"] = (
                (await settings.inner_text(timeout=500)).strip().replace("\n", " ")
                if await settings.count() else None
            )
        except Exception:
            evidence["settings_text"] = None
        return evidence

    async def _wait_for_settings_panel(self, page: Page, timeout_ms: int = 6000) -> bool:
        """Detect the currently-open Flow settings overlay without toggling it.

        Current Flow UI renders the settings body inside a CDK overlay.  The
        overlay itself is the strongest marker; model/x1..x4 controls are kept
        as compatibility fallbacks for older UI variants.
        """
        import asyncio as _asyncio
        start = _asyncio.get_running_loop().time()
        deadline = timeout_ms / 1000.0
        overlay_probe = page.locator(".cdk-overlay-pane:visible")
        x_probe = page.locator("button:visible").filter(has_text=re.compile(r"^\s*x[1-4]\s*$", re.I))
        aria_probes = [page.locator(sel) for sel in MODEL_BUTTON_SELECTORS]
        while _asyncio.get_running_loop().time() - start < deadline:
            try:
                overlays = await overlay_probe.count()
                if overlays:
                    for i in range(min(overlays, 6)):
                        pane = overlay_probe.nth(i)
                        try:
                            if await pane.locator("button:visible").count():
                                return True
                        except Exception:
                            continue
            except Exception:
                pass
            try:
                if await x_probe.count():
                    return True
            except Exception:
                pass
            for probe in aria_probes:
                try:
                    if await probe.count():
                        return True
                except Exception:
                    continue
            try:
                buttons = page.locator("button:visible")
                count = min(await buttons.count(), 50)
                for i in range(count):
                    try:
                        text = (await buttons.nth(i).inner_text(timeout=250)).strip().replace("\n", " ")
                    except Exception:
                        continue
                    cleaned = text.replace("image", "").replace("videocam", "").strip()
                    if cleaned in {"Video", "Videos", "Hình ảnh", "Image", "Images"}:
                        return True
            except Exception:
                pass
            await page.wait_for_timeout(250)
        return False

    async def _wait_for_generation_ui_ready(self, page: Page, timeout_ms: int = 15000) -> None:
        """Wait for Flow Angular composer/settings UI to finish hydrating."""
        settings = page.locator(
            'button.settings-trigger-button:visible, '
            'button[aria-label*="cài đặt"]:visible, '
            'button[aria-label*="settings" i]:visible'
        ).first
        try:
            await settings.wait_for(state="visible", timeout=timeout_ms)
        except Exception as exc:
            raise FlowBrowserError(
                f"FLOW_UI_NOT_READY: settings trigger chưa sẵn sàng sau {timeout_ms}ms. URL={page.url}"
            ) from exc
        # Angular/CDK needs a short stabilization window after the trigger first appears.
        await page.wait_for_timeout(700)

    async def _ensure_settings_open(self, page: Page) -> None:
        # Toggle-aware: never blindly double-click a toggle. Current Flow uses
        # an Angular/Material CDK overlay. In live probes, DOM-native
        # element.click() is the most reliable opener after SPA hydration.
        if await self._wait_for_settings_panel(page, timeout_ms=1000):
            return
        settings = page.locator(
            'button.settings-trigger-button:visible, '
            'button[aria-label*="cài đặt"]:visible, '
            'button[aria-label*="settings" i]:visible'
        ).first
        try:
            await settings.wait_for(state="visible", timeout=15000)
            # Give the SPA one short hydration window after the trigger first
            # becomes visible; visibility alone does not mean handlers are ready.
            await page.wait_for_timeout(500)
        except Exception as exc:
            raise FlowBrowserError(
                f"Không tìm thấy nút cài đặt Generate sau khi chờ project tải xong. URL={page.url}"
            ) from exc

        # Primary: native DOM click. This proved stable on the current Flow UI.
        try:
            await settings.evaluate("(e) => e.click()")
        except Exception:
            pass
        if await self._wait_for_settings_panel(page, timeout_ms=3000):
            return

        # Fallback 1: Playwright locator click.
        try:
            await settings.click(force=True)
        except Exception:
            pass
        if await self._wait_for_settings_panel(page, timeout_ms=2500):
            return

        # Fallback 2: keyboard activation, only while the panel is still closed.
        try:
            await settings.focus()
            await page.keyboard.press("Enter")
        except Exception:
            pass
        if await self._wait_for_settings_panel(page, timeout_ms=2500):
            return

        raise FlowBrowserError(
            f"Không mở được panel cài đặt Flow sau DOM click + locator click + Enter. URL={page.url}"
        )

    async def _switch_generation_mode(self, page: Page, mode: str) -> None:
        aliases = {
            "Hình ảnh": ["Hình ảnh", "Image", "Images", "Ảnh", "Photos"],
            "Video": ["Video", "Videos"],
        }.get(mode, [mode])
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        await self._ensure_settings_open(page)

        async def _click_alias() -> bool:
            # Retry a few times: panel may still be rendering after open.
            for _ in range(3):
                # Current Flow UI exposes generation mode as Material button toggles
                # with role=radio. Prefer that semantic control before generic buttons.
                radios = page.locator('[role="radio"]:visible')
                try:
                    radio_count = min(await radios.count(), 12)
                except Exception:
                    radio_count = 0
                for i in range(radio_count):
                    radio = radios.nth(i)
                    try:
                        text = (await radio.inner_text(timeout=600)).strip().replace("\n", " ")
                        aria = str(await radio.get_attribute("aria-label") or "")
                    except Exception:
                        continue
                    cleaned = f"{text} {aria}".replace("image", "").replace("videocam", "").strip()
                    if any(alias.lower() == cleaned.lower() or alias.lower() in cleaned.lower() for alias in aliases):
                        await radio.click(force=True)
                        await page.wait_for_timeout(400)
                        return True

                buttons = page.locator("button:visible")
                try:
                    count = await buttons.count()
                except Exception:
                    count = 0
                for i in range(count):
                    try:
                        text = (await buttons.nth(i).inner_text(timeout=800)).strip().replace("\n", " ")
                    except Exception:
                        continue
                    cleaned = text.replace("image", "").replace("videocam", "").strip()
                    if cleaned in aliases:
                        await buttons.nth(i).click(force=True)
                        await page.wait_for_timeout(400)
                        return True
                for alias in aliases:
                    loc = page.locator(f'button[aria-label="{alias}"]:visible, button[aria-label*="{alias}" i]:visible').first
                    try:
                        if await loc.count():
                            await loc.click(force=True)
                            await page.wait_for_timeout(400)
                            return True
                    except Exception:
                        continue
                await page.wait_for_timeout(400)
            return False

        if await _click_alias():
            return
        # Fallback via model dropdown: open it to reveal mode list (old UI path),
        # using multi-selector fallback instead of one brittle selector.
        model_button, strategy = await self._find_model_button(page)
        if model_button is None:
            try:
                total = await page.locator("button:visible").count()
            except Exception:
                total = -1
            evidence = await self._selector_evidence(page)
            raise FlowBrowserError(
                f"Không tìm thấy selector model để chuyển sang {mode}. "
                f"(strategy=missing, visible_buttons={total}, evidence={evidence})"
            )
        await model_button.click(force=True)
        await page.wait_for_timeout(600)
        if await _click_alias():
            return
        # Close dropdown before raising so next step starts clean.
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        evidence = await self._selector_evidence(page)
        raise FlowBrowserError(f"Flow không hiển thị mode {mode}. evidence={evidence}")

    async def configure_video(
        self,
        page: Page,
        *,
        model: str | None,
        aspect_ratio: str,
        resolution: str,
        duration: int,
        output_count: int = 1,
    ) -> None:
        await self._switch_generation_mode(page, "Video")

        await self._ensure_settings_open(page)
        await self._click_visible_button(page, aspect_ratio)
        await page.wait_for_timeout(150)

        if model:
            await self._ensure_settings_open(page)
            model_button, _strategy = await self._find_model_button(page)
            if model_button is None:
                evidence = await self._selector_evidence(page)
                raise FlowBrowserError(f"Không tìm thấy selector model Flow. evidence={evidence}")
            await model_button.click(force=True)
            await page.wait_for_timeout(600)
            variants = model_selection_variants(model)
            # Credit guard: khi cấu hình yêu cầu bản [Lower Priority] (không tốn
            # credits), TUYỆT ĐỐI không fallback sang bản base trả phí. Thà fail
            # loudly còn hơn đốt credits của user.
            low_priority_required = "[lower priority]" in str(model).lower()
            if low_priority_required:
                variants = [str(model).strip()]
            last_error: Exception | None = None
            for variant in variants:
                try:
                    await self._click_visible_button(page, variant)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    continue
            if last_error is not None:
                evidence = await self._selector_evidence(page)
                raise FlowBrowserError(
                    f"Không tìm thấy model Flow '{model}' (thử {variants}). evidence={evidence}"
                ) from last_error
            await page.wait_for_timeout(300)
            if low_priority_required:
                # Verify-back: đọc lại nút model, chắc chắn UI đang giữ đúng bản
                # [Lower Priority] trước khi bấm Generate. Sai -> abort, không đốt credits.
                verify_button, _ = await self._find_model_button(page)
                verify_text = ""
                if verify_button is not None:
                    try:
                        verify_text = (await verify_button.inner_text(timeout=2000)).strip()
                    except Exception:
                        verify_text = ""
                if "[lower priority]" not in verify_text.lower():
                    evidence = await self._selector_evidence(page)
                    raise FlowBrowserError(
                        f"MODEL_SELECTION_MISMATCH: yêu cầu '{model}' nhưng UI đang giữ "
                        f"'{verify_text}'. Abort để không tốn credits. evidence={evidence}"
                    )

        await self._ensure_settings_open(page)
        resolution_button = page.get_by_role("button", name=resolution, exact=True)
        if await resolution_button.count():
            await resolution_button.first.click(force=True)
            await page.wait_for_timeout(150)
        else:
            summary = page.locator('button[aria-label*="cài đặt"]').first
            summary_text = (await summary.inner_text()).replace("\n", " ") if await summary.count() else ""
            if resolution not in summary_text:
                raise FlowBrowserError(f"Resolution {resolution} không khả dụng trên model hiện tại.")

        await self._ensure_settings_open(page)
        duration_button = page.locator("button:visible").filter(has_text=re.compile(rf"^\s*{duration}\b"))
        if await duration_button.count() == 0:
            raise FlowBrowserError(f"Không tìm thấy thời lượng {duration}s trên model hiện tại.")
        await duration_button.first.click(force=True)
        await page.wait_for_timeout(150)

        await self._ensure_settings_open(page)
        output_button = page.locator("button:visible").filter(has_text=re.compile(rf"^\s*x{output_count}\s*$", re.I))
        if await output_button.count() == 0:
            raise FlowBrowserError(f"Không tìm thấy output x{output_count} trên model hiện tại.")
        await output_button.first.click(force=True)
        await page.wait_for_timeout(150)
        if await page.locator("button:visible").filter(has_text="x4").count():
            await page.keyboard.press("Escape")

    async def image_capabilities(self, project_id: str | None = None) -> dict:
        if not project_id:
            projects = await self.list_projects()
            if not projects:
                raise FlowBrowserError("Không có Flow project để đọc capability ảnh.")
            project_id = projects[0]["id"]
        page = await self.flow_page()
        target_url = f"https://flow.google.com/project/{project_id}"
        if page.url != target_url:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        if "/404" in page.url and "reason=project" in page.url:
            raise FlowBrowserError(f"PROJECT_NOT_FOUND: Flow project {project_id} redirected to {page.url}.")
        await page.wait_for_timeout(1800)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        try:
            await self._switch_generation_mode(page, "Hình ảnh")
        except FlowBrowserError:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
            await self._switch_generation_mode(page, "Hình ảnh")
        await self._ensure_settings_open(page)
        await page.wait_for_timeout(300)
        buttons = page.locator("button:visible")
        aspects = []
        outputs = []
        try:
            total_img_buttons = await buttons.count()
        except Exception:
            total_img_buttons = 0
        for i in range(min(total_img_buttons, 120)):
            button = buttons.nth(i)
            try:
                text = (await button.inner_text(timeout=1500)).strip().replace("\n", " ")
                aria = str(await button.get_attribute("aria-label", timeout=1500) or "")
            except Exception:
                continue
            blob = f"{text} {aria}"
            for match in re.finditer(r"\b\d+\s*:\s*\d+\b", blob):
                ratio = re.sub(r"\s+", "", match.group(0))
                if ratio not in aspects:
                    aspects.append(ratio)
            lower = blob.lower()
            if "crop_square" in lower and "1:1" not in aspects:
                aspects.append("1:1")
            if "crop_16_9" in lower and "16:9" not in aspects:
                aspects.append("16:9")
            if "crop_9_16" in lower and "9:16" not in aspects:
                aspects.append("9:16")
            out = re.fullmatch(r"x(\d+)", text.strip(), re.I)
            if out:
                value = int(out.group(1))
                if value not in outputs:
                    outputs.append(value)

        model_button, _strategy = await self._find_model_button(page)
        models = []
        if model_button is not None:
            await model_button.click(force=True)
            await page.wait_for_timeout(500)
            items = page.locator('[role="menuitem"]:visible, button:visible')
            try:
                total_items = await items.count()
            except Exception:
                total_items = 0
            for i in range(min(total_items, 60)):
                try:
                    text = (await items.nth(i).inner_text(timeout=1500)).strip().replace("\n", " ")
                except Exception:
                    continue
                cleaned = text.replace("🍌", "").replace("arrow_drop_down", "").replace("volume_up", "").strip()
                found = re.search(r"Nano Banana[^\n]*", cleaned)
                name = found.group(0).strip() if found else ""
                if name and name not in models:
                    models.append(name)
            await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
        if not aspects:
            aspects = ["1:1", "16:9", "9:16"]
        if not models:
            models = ["Nano Banana 2"]
        if not outputs:
            outputs = [1]
        return {
            "project_id": project_id,
            "modes": ["image"],
            "models": models,
            "aspect_ratios": aspects,
            "output_counts": outputs,
        }

    async def configure_image(
        self,
        page: Page,
        *,
        model: str | None,
        aspect_ratio: str,
        output_count: int = 1,
    ) -> None:
        await self._switch_generation_mode(page, "Hình ảnh")

        await self._ensure_settings_open(page)
        aspect_needles = self._aspect_needles(aspect_ratio)
        try:
            await self._click_visible_button(page, *aspect_needles)
        except FlowBrowserError:
            crop = page.locator("button:visible").filter(has_text=re.compile(r"crop_|\d+\s*:\s*\d+|Vuông|Square", re.I)).first
            if await crop.count() == 0:
                raise
            await crop.click(force=True)
            await page.wait_for_timeout(250)
            await self._click_visible_button(page, *aspect_needles)
        await page.wait_for_timeout(150)

        if model:
            await self._ensure_settings_open(page)
            model_button, _strategy = await self._find_model_button(page)
            if model_button is None:
                raise FlowBrowserError("Không tìm thấy selector model ảnh Flow.")
            await model_button.click(force=True)
            await page.wait_for_timeout(500)
            await self._click_visible_button(page, model)
            await page.wait_for_timeout(250)

        await self._ensure_settings_open(page)
        output_button = page.locator("button:visible").filter(has_text=re.compile(rf"^\s*x{output_count}\s*$", re.I))
        if await output_button.count():
            await output_button.first.click(force=True)
            await page.wait_for_timeout(150)
        elif int(output_count) != 1:
            raise FlowBrowserError(f"Không tìm thấy output x{output_count} cho Image mode.")
        if await page.locator("button:visible").filter(has_text="x4").count():
            await page.keyboard.press("Escape")

    async def _download_flow_image_bytes(self, page: Page, url: str) -> tuple[bytes, dict]:
        kind = classify_flow_image_url(url)
        if kind == "invalid":
            raise FlowBrowserError("FLOW_IMAGE_INVALID_RESPONSE: URL ảnh Flow không hợp lệ.")

        if kind == "signed_cdn":
            try:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    response = await client.get(url)
                    content = response.content or b""
                    content_type = str(response.headers.get("content-type") or "")
                    info = validate_downloaded_image(content, content_type, str(response.url))
                    info["source_kind"] = kind
                    return content, info
            except FlowBrowserError:
                pass
            except Exception:
                pass

        content = b""
        content_type = ""
        final_url = url
        if url.startswith(("blob:", "data:")):
            payload = await page.evaluate(
                """async (src) => {
                    const res = await fetch(src, {credentials: 'include', redirect: 'follow'});
                    const buf = await res.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    const chunk = 0x8000;
                    let binary = '';
                    for (let i = 0; i < bytes.length; i += chunk) {
                      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                    }
                    return {
                      status: res.status,
                      contentType: res.headers.get('content-type') || '',
                      url: res.url || src,
                      b64: btoa(binary)
                    };
                }""",
                url,
            )
            status = int((payload or {}).get("status") or 0)
            if status != 200:
                raise FlowBrowserError(
                    f"FLOW_IMAGE_DOWNLOAD_HTTP_{status}: không tải được blob ảnh Flow."
                )
            content = base64.b64decode((payload or {}).get("b64") or "")
            content_type = str((payload or {}).get("contentType") or "")
            final_url = str((payload or {}).get("url") or url)
        else:
            response = await page.context.request.get(url, timeout=60_000, max_redirects=10)
            if response.status != 200:
                raise FlowBrowserError(
                    f"FLOW_IMAGE_DOWNLOAD_HTTP_{response.status}: authenticated download thất bại ({kind})."
                )
            content = await response.body()
            content_type = str(response.headers.get("content-type") or "")
            final_url = str(response.url)
        info = validate_downloaded_image(content, content_type, final_url)
        info["source_kind"] = kind
        return content, info

    async def _image_items(self, page: Page) -> list[dict]:
        items = []
        locator = page.locator('img[data-media-id], img[alt="Ô hiển thị hình ảnh của người dùng"], img[alt*="hình ảnh" i]')
        for i in range(await locator.count()):
            image = locator.nth(i)
            media_id = str(await image.get_attribute("data-media-id") or "")
            src = str(await image.get_attribute("src") or "")
            if media_id and src:
                items.append({"media_id": media_id, "src": src})
        return items

    async def generate_image(
        self,
        *,
        job_id: str,
        project_id: str,
        prompt: str,
        media_project_id: str | None = None,
        model: str | None = None,
        aspect_ratio: str = "16:9",
        output_count: int = 1,
        timeout_seconds: int = 600,
    ) -> dict:
        async with self._ui_lock:
            page = await self.flow_page()
            target_url = f"https://flow.google.com/project/{project_id}"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1800)
            if not page.url.startswith(target_url):
                raise FlowBrowserError(
                    f"SESSION_EXPIRED: Flow project navigation redirected to {page.url}. "
                    "Dedicated Flow session cần đăng nhập lại."
                )

            before = await self._image_items(page)
            before_ids = {item["media_id"] for item in before}

            await self.configure_image(
                page,
                model=model,
                aspect_ratio=aspect_ratio,
                output_count=1,
            )
            editor = page.locator('.ProseMirror[contenteditable="true"]').first
            if await editor.count() == 0:
                raise FlowBrowserError("Không tìm thấy ô nhập prompt Flow.")
            await editor.fill(prompt)

            generate = page.locator('button[aria-label="Bắt đầu tạo"]').first
            if await generate.count() == 0:
                generate = page.locator('button[aria-label*="Start"]').first
            if await generate.count() == 0:
                raise FlowBrowserError("Không tìm thấy nút Generate Flow cho Image mode.")

            ready_deadline = asyncio.get_running_loop().time() + 8
            while await generate.is_disabled() and asyncio.get_running_loop().time() < ready_deadline:
                await page.wait_for_timeout(250)
            if await generate.is_disabled():
                raise FlowBrowserError("Nút Generate ảnh vẫn disabled sau khi chờ composer ổn định.")
            await generate.click(force=True)

            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                await page.wait_for_timeout(2500)
                if not page.url.startswith(target_url):
                    raise FlowBrowserError(
                        f"SESSION_EXPIRED: Flow left project page during image generation and is now at {page.url}."
                    )
                current = await self._image_items(page)
                new_items = [item for item in current if item["media_id"] not in before_ids]
                if new_items:
                    item = new_items[0]
                    content, info = await self._download_flow_image_bytes(page, item["src"])
                    output_dir = (project_media_root(media_project_id) / "flow_image_downloads" / job_id) if media_project_id else (MEDIA_DIR / "flow_image_downloads" / job_id)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    result_path = output_dir / f"result{info['suffix']}"
                    result_path.write_bytes(content)
                    return {
                        "project_id": project_id,
                        "result_path": str(result_path),
                        "media_id": item["media_id"],
                        "source_url": item["src"],
                        "model": model,
                        "aspect_ratio": aspect_ratio,
                        "mime_type": info["mime"],
                        "width": info.get("width"),
                        "height": info.get("height"),
                    }

                try:
                    body = (await page.locator("body").inner_text(timeout=10000))[-5000:].lower()
                except Exception:
                    continue
                if "không đủ tín dụng" in body or "not enough credits" in body:
                    raise FlowBrowserError("FLOW_CREDITS_INSUFFICIENT: Google Flow không đủ tín dụng để tạo ảnh.")
                if any(x in body for x in ["không thể tạo", "couldn't generate", "generation failed"]):
                    raise FlowBrowserError("Flow báo tạo ảnh thất bại.")
            raise FlowBrowserError("Hết thời gian chờ ảnh mới từ Google Flow.")

    def _video_play_icons(self, page: Page):
        return page.locator(".google-symbols").filter(has_text="play_arrow")

    async def _download_video_card(self, page: Page, card_index: int, output_dir: Path, resolution: str) -> Path:
        icons = self._video_play_icons(page)
        if card_index < 0 or card_index >= await icons.count():
            raise FlowBrowserError("Không tìm thấy video card mới để tải xuống.")
        icon = icons.nth(card_index)
        card = icon.locator("xpath=/../../..")
        buttons = card.locator("button")
        if await buttons.count() < 3:
            raise FlowBrowserError("Video card không có menu tùy chọn.")
        await buttons.nth(2).evaluate("(e) => e.click()")
        await page.wait_for_timeout(300)

        menuitems = page.locator('[role="menuitem"]:visible')
        download_action = None
        for i in range(await menuitems.count()):
            item = menuitems.nth(i)
            text = (await item.inner_text()).strip().replace("\n", " ")
            if text.endswith("Tải xuống") or text.lower().endswith("download"):
                download_action = item
                break
        if download_action is None:
            raise FlowBrowserError("Không tìm thấy action Download của video card.")
        await download_action.click(force=True)
        await page.wait_for_timeout(300)

        # Scope to the open menu. Scanning every visible page button (nth(57)) times out.
        menu = page.locator('[role="menu"]:visible, [role="listbox"]:visible').last
        if await menu.count() > 0:
            choices = menu.locator('[role="menuitem"]:visible, button:visible')
        else:
            choices = page.locator('[role="menuitem"]:visible')
        resolution_choice = None
        for i in range(await choices.count()):
            item = choices.nth(i)
            text = (await item.inner_text()).strip().replace("\n", " ")
            if text.startswith(resolution):
                resolution_choice = item
                break
        if resolution_choice is None:
            raise FlowBrowserError(f"Không tìm thấy lựa chọn tải {resolution} cho video.")

        output_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.time()
        async with page.expect_download(timeout=20000) as info:
            await resolution_choice.click(force=True)
        download = await info.value
        suffix = Path(download.suggested_filename).suffix.lower() or ".mp4"
        target = output_dir / f"result{suffix}"
        await download.save_as(str(target))
        if not target.exists() or target.stat().st_size <= 0:
            recovered = self._recover_chrome_download(output_dir, started_at)
            if recovered is not None:
                return recovered
            raise FlowBrowserError("Flow download hoàn tất nhưng file video rỗng.")
        return target

    def _recover_chrome_download(self, output_dir: Path, started_at: float) -> Path | None:
        folder = Path.home() / "Downloads"
        if not folder.exists():
            return None
        newest = None
        newest_mtime = 0.0
        for item in folder.glob("*.mp4"):
            try:
                st = item.stat()
            except OSError:
                continue
            if st.st_size <= 0 or st.st_mtime < started_at - 5:
                continue
            if st.st_mtime >= newest_mtime:
                newest = item
                newest_mtime = st.st_mtime
        if newest is None:
            return None
        target = output_dir / "result.mp4"
        shutil.copy2(newest, target)
        if target.exists() and target.stat().st_size > 0:
            return target
        return None

    def _extract_video_from_zip(self, archive: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        media_exts = {".mp4", ".mov", ".webm", ".m4v"}
        with zipfile.ZipFile(archive) as zf:
            candidates = [item for item in zf.infolist() if Path(item.filename).suffix.lower() in media_exts]
            if not candidates:
                raise FlowBrowserError("Flow download ZIP không chứa file video.")
            item = candidates[0]
            suffix = Path(item.filename).suffix.lower() or ".mp4"
            target = output_dir / f"result{suffix}"
            with zf.open(item) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            return target

    def _extract_boundary_frames(self, video_path: Path, output_dir: Path, job_id: str) -> tuple[Path, Path]:
        first = output_dir / f"first_frame_{job_id[:8]}.jpg"
        last = output_dir / f"last_frame_{job_id[:8]}.jpg"
        common = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE, "text": True, "timeout": 45}
        try:
            subprocess.run(
                [ffmpeg_path(), "-y", "-ss", "0.05", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(first)],
                check=True, **common,
            )
            subprocess.run(
                [ffmpeg_path(), "-y", "-sseof", "-0.12", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(last)],
                check=True, **common,
            )
        except Exception as exc:
            raise FlowBrowserError(f"Không tách được boundary frames: {exc}") from exc
        return first, last

    async def _attach_reference_image(self, page: Page, reference_path: Path) -> None:
        if not reference_path.exists():
            raise FlowBrowserError(f"Reference image không tồn tại: {reference_path}")
        add = page.locator('button[aria-label*="Thêm thành phần vào ô nhập câu lệnh"]').first
        if await add.count() == 0:
            add = page.locator('button[aria-label*="Add ingredient"]').first
        if await add.count() == 0:
            raise FlowBrowserError("Không tìm thấy nút thêm ingredient.")
        await add.click(force=True)
        await page.wait_for_timeout(250)

        async def ready_asset_button():
            matches = page.locator("button:visible").filter(has_text=reference_path.name)
            ready = []
            for i in range(await matches.count()):
                button = matches.nth(i)
                text = (await button.inner_text()).strip().replace("\n", " ")
                if "Đang tải lên" not in text and "Uploading" not in text:
                    ready.append(button)
            return ready[-1] if ready else None

        existing = await ready_asset_button()
        if existing is not None:
            await existing.click(force=True)
            await page.wait_for_timeout(500)
            return

        upload = page.locator('button[aria-label*="Tải nội dung nghe nhìn lên"]').first
        if await upload.count() == 0:
            upload = page.locator('button[aria-label*="Upload"]').first
        if await upload.count() == 0:
            raise FlowBrowserError("Không tìm thấy nút upload asset Flow.")
        async with page.expect_file_chooser(timeout=7000) as info:
            await upload.click(force=True)
        chooser = await info.value
        await chooser.set_files(str(reference_path))

        deadline = asyncio.get_running_loop().time() + 12
        asset_button = None
        while asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(500)
            asset_button = await ready_asset_button()
            if asset_button is not None:
                break
        if asset_button is None:
            await page.keyboard.press("Escape")
            raise FlowBrowserError("Upload reference thành công nhưng asset chưa sẵn sàng trong picker.")
        await asset_button.click(force=True)
        await page.wait_for_timeout(500)

    async def generate_video(
        self,
        *,
        job_id: str,
        project_id: str,
        prompt: str,
        media_project_id: str | None = None,
        reference_image_paths: list[Path] | None = None,
        model: str | None = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        duration: int = 8,
        output_count: int = 1,
        timeout_seconds: int = 900,
    ) -> dict:
        async with self._ui_lock:
            page = await self.flow_page()
            target_url = f"https://flow.google.com/project/{project_id}"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await self._wait_for_generation_ui_ready(page, timeout_ms=15000)
            if not page.url.startswith(target_url):
                raise FlowBrowserError(
                    f"SESSION_EXPIRED: Flow project navigation redirected to {page.url}. "
                    "Dedicated Flow session cần đăng nhập lại."
                )

            before_icons = self._video_play_icons(page)
            before_videos = await before_icons.count()
            before_error_tiles = page.locator("flow-error-tile:visible")
            before_error_count = await before_error_tiles.count()
            before_error_texts = set()
            for i in range(before_error_count):
                try:
                    text = " ".join((await before_error_tiles.nth(i).inner_text(timeout=2000)).split())
                    if text:
                        before_error_texts.add(text)
                except Exception:
                    pass
            before_video_srcs = set()
            for i in range(before_videos):
                try:
                    card = before_icons.nth(i).locator("xpath=/../../..")
                    src = await card.locator("img").first.get_attribute("src")
                    if src:
                        before_video_srcs.add(src)
                except Exception:
                    pass

            await self.configure_video(
                page,
                model=model,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration=duration,
                output_count=1,
            )
            editor = page.locator('.ProseMirror[contenteditable="true"]').first
            if await editor.count() == 0:
                raise FlowBrowserError("Không tìm thấy ô nhập prompt Flow.")
            await editor.fill(prompt)
            for reference_image_path in (reference_image_paths or []):
                await self._attach_reference_image(page, reference_image_path)

            generate = page.locator('button[aria-label="Bắt đầu tạo"]').first
            if await generate.count() == 0:
                generate = page.locator('button[aria-label*="Start"]').first
            if await generate.count() == 0:
                raise FlowBrowserError("Không tìm thấy nút Generate Flow.")

            ready_deadline = asyncio.get_running_loop().time() + 8
            while await generate.is_disabled() and asyncio.get_running_loop().time() < ready_deadline:
                await page.wait_for_timeout(250)
            if await generate.is_disabled():
                raise FlowBrowserError("Nút Generate vẫn disabled sau khi chờ composer ổn định.")
            await generate.click(force=True)

            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                await page.wait_for_timeout(3000)
                if not page.url.startswith(target_url):
                    raise FlowBrowserError(
                        f"SESSION_EXPIRED: Flow left project page during generation and is now at {page.url}."
                    )

                # Flow renders generation failures as error tiles. Detect only a
                # tile created/changed after this request, so stale errors from
                # older attempts do not poison a new generation.
                try:
                    error_tiles = page.locator("flow-error-tile:visible")
                    error_count = await error_tiles.count()
                    error_texts = []
                    for i in range(error_count):
                        raw = " ".join((await error_tiles.nth(i).inner_text(timeout=2000)).split())
                        if raw:
                            error_texts.append(raw)
                    candidates = [
                        raw for raw in error_texts
                        if raw not in before_error_texts
                    ]
                    if error_count > before_error_count and not candidates:
                        candidates = error_texts[before_error_count:]
                    for raw in candidates:
                        classified = classify_flow_generation_error_text(raw)
                        if classified:
                            code, detail = classified
                            raise FlowBrowserError(f"{code}: {detail[:1600]}")
                except FlowBrowserError:
                    raise
                except Exception:
                    # DOM reads can transiently fail while Flow updates the tile.
                    pass

                try:
                    icons = self._video_play_icons(page)
                    current_videos = await icons.count()
                except Exception:
                    # Transient CDP/DOM hiccup: keep polling, don't kill the job.
                    continue
                # Detect a completed output by identity, not only by card count.
                # Flow can recycle/reorder tiles, so a new result may replace an old
                # tile while current_videos == before_videos.
                new_index = None
                for i in range(current_videos):
                    try:
                        card = icons.nth(i).locator("xpath=/../../..")
                        src = await card.locator("img.thumbnail, img").first.get_attribute("src", timeout=3000)
                        if src and src not in before_video_srcs:
                            new_index = i
                            break
                    except Exception:
                        pass
                if new_index is None and current_videos > before_videos:
                    new_index = current_videos - 1

                if new_index is not None:
                    output_dir = (project_media_root(media_project_id) / "flow_downloads" / job_id) if media_project_id else (MEDIA_DIR / "flow_downloads" / job_id)
                    result_path = await self._download_video_card(
                        page,
                        new_index,
                        output_dir,
                        resolution,
                    )
                    first_frame, last_frame = self._extract_boundary_frames(result_path, output_dir, job_id)
                    return {
                        "project_id": project_id,
                        "result_path": str(result_path),
                        "result_url": f"/api/flow/render/{job_id}/file",
                        "first_frame_path": str(first_frame),
                        "last_frame_path": str(last_frame),
                        "first_frame_url": f"/api/flow/render/{job_id}/first-frame",
                        "last_frame_url": f"/api/flow/render/{job_id}/last-frame",
                    }

                try:
                    body = (await page.locator("body").inner_text(timeout=10000))[-5000:].lower()
                except Exception:
                    # Transient DOM read hiccup: skip banner checks this round.
                    continue
                if (
                    "tạo video không có âm thanh" in body
                    and ("chưa bị tính phí" in body or "not charged" in body)
                ):
                    raise FlowBrowserError("AUDIO_GENERATION_FAILED: Flow không tạo được audio cho lượt này; chưa bị tính phí.")
                if any(x in body for x in ["không thể tạo", "couldn't generate", "generation failed"]):
                    raise FlowBrowserError("Flow báo generation thất bại.")
            raise FlowBrowserError("Hết thời gian chờ video card mới từ Flow.")

    async def open_login_window(self) -> dict:
        # Never spawn a second Chrome window for login. Connect to the dedicated
        # persistent profile and bring its canonical Flow/login tab back on screen.
        profile = ACTIVE_PROFILE
        profile.mkdir(parents=True, exist_ok=True)
        cfg = load_config()

        page = await self.flow_page()
        browser = await self.connect()

        # Clean up stale duplicate Flow/login tabs left by older runtime versions.
        for context in browser.contexts:
            for candidate in list(context.pages):
                if candidate == page or candidate.is_closed():
                    continue
                if "flow.google.com" in candidate.url or "accounts.google.com" in candidate.url:
                    try:
                        await candidate.close()
                    except Exception:
                        pass

        await asyncio.to_thread(set_flow_chrome_visibility, True)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return {
            "ok": True,
            "profile_dir": str(profile),
            "cdp_url": cfg["cdp_url"],
            "reused_existing_tab": True,
            "message": "Đã mở lại đúng Chrome Flow profile/tab hiện tại. Hoàn tất đăng nhập; ứng dụng sẽ tự phát hiện và đưa Chrome xuống nền.",
        }

    async def session_status(self) -> dict:
        try:
            page = await self.flow_page()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            url = page.url
            title = await page.title()
            try:
                body = (await page.locator("body").inner_text(timeout=5000))[:3000]
            except Exception:
                body = ""
            lowered = body.lower()
            redirected_to_login = "accounts.google.com" in url
            on_public_about = "/about" in url
            login_prompt = any(x in lowered for x in ["sign in", "đăng nhập", "choose an account"])
            add_buttons = page.locator("button").filter(has=page.locator(".google-symbols").filter(has_text="add"))
            workspace_ready = await add_buttons.count() > 0
            if not workspace_ready and "flow.google.com" in url and not redirected_to_login and not on_public_about and "/404" not in url:
                try:
                    await add_buttons.first.wait_for(state="attached", timeout=8000)
                    workspace_ready = await add_buttons.count() > 0
                except Exception:
                    workspace_ready = False
            classified = classify_flow_session(
                url=url,
                title=title,
                workspace_ready=workspace_ready,
                login_prompt=login_prompt,
                connected=True,
            )
            if classified.get("authenticated"):
                await asyncio.to_thread(set_flow_chrome_visibility, False)
            return classified
        except Exception as exc:
            return {
                "connected": False,
                "authenticated": False,
                "state": "BRIDGE_DISCONNECTED",
                "url": None,
                "title": None,
                "reason": str(exc),
            }

    async def restore_workspace(self, project_id: str | None = None) -> dict:
        page = await self.flow_page()
        current = classify_flow_session(url=page.url, connected=True)
        if current.get("state") in {"PROJECT_NOT_FOUND", "SESSION_EXPIRED"} or "/404" in page.url:
            await page.goto(load_config()["flow_url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
        status = await self.session_status()
        pid = project_id
        if not pid:
            try:
                projects = await self.list_projects()
                pid = projects[0]["id"] if projects else None
            except Exception:
                pid = None
        if pid:
            await page.goto(f"https://flow.google.com/project/{pid}", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            if "/404" in page.url and "reason=project" in page.url:
                await page.goto(load_config()["flow_url"], wait_until="domcontentloaded", timeout=30000)
                try:
                    projects = await self.list_projects()
                except Exception:
                    projects = []
                if projects:
                    pid = projects[0]["id"]
                    await page.goto(f"https://flow.google.com/project/{pid}", wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1000)
        return await self.session_status()

    async def close(self) -> None:
        self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


flow_browser = FlowBrowser()
