"""
Browser-driven RedGIFs uploader using AdsPower + Playwright.

This path is used when a specific AdsPower profile is selected in the GUI.
It performs upload/publish through studio.redgifs.com instead of direct API calls.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)


class AdsPowerClient:
    """Small AdsPower API client for starting/stopping local profiles."""

    def __init__(self, api_base: str, api_key: str):
        self.api_base = (api_base or "http://127.0.0.1:50325").rstrip("/")
        self.api_key = (api_key or "").strip()

    def start_browser(self, profile_id: str) -> Dict[str, Any]:
        params = {"user_id": profile_id}
        if self.api_key:
            params["api_key"] = self.api_key
        resp = requests.get(f"{self.api_base}/api/v1/browser/start", params=params, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"AdsPower start HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"AdsPower start failed: {data}")
        node = data.get("data") or {}
        if not isinstance(node, dict):
            raise RuntimeError(f"AdsPower start payload malformed: {data}")
        return node

    def stop_browser(self, profile_id: str) -> None:
        params = {"user_id": profile_id}
        if self.api_key:
            params["api_key"] = self.api_key
        try:
            requests.get(f"{self.api_base}/api/v1/browser/stop", params=params, timeout=15)
        except Exception:
            pass


class RedGifsBrowserUploader:
    """Upload/publish files through RedGIFs Studio UI."""

    def __init__(
        self,
        profile_id: str,
        account_name: str,
        config_path: Optional[Path] = None,
    ):
        if not profile_id:
            raise ValueError("profile_id is required for browser uploader")

        self.profile_id = profile_id
        self.account_name = account_name
        self.config_path = config_path or (Path(__file__).parent / "adspower_config.json")

        self._adspower: Optional[AdsPowerClient] = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started_profile = False

    def _load_adspower_client(self) -> AdsPowerClient:
        if self._adspower:
            return self._adspower

        api_base = "http://127.0.0.1:50325"
        api_key = ""
        if self.config_path.exists():
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            api_base = (cfg.get("adspower_api_base") or api_base).strip()
            api_key = (cfg.get("api_key") or "").strip()

        self._adspower = AdsPowerClient(api_base=api_base, api_key=api_key)
        return self._adspower

    def _ensure_connected(self) -> None:
        if self._page is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Browser uploader requires Playwright. Install with: pip install playwright"
            ) from e

        client = self._load_adspower_client()
        browser_data = client.start_browser(self.profile_id)
        self._started_profile = True

        ws_node = browser_data.get("ws") or {}
        if not isinstance(ws_node, dict):
            raise RuntimeError(f"AdsPower browser/start missing ws node: {browser_data}")
        ws_endpoint = (ws_node.get("puppeteer") or "").strip()
        if not ws_endpoint:
            raise RuntimeError(f"AdsPower browser/start missing puppeteer endpoint: {browser_data}")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(ws_endpoint)
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.set_default_timeout(45000)

    def close(self, stop_profile: bool = False) -> None:
        """Close playwright handles. Profile stop is optional."""
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        if stop_profile and self._started_profile:
            try:
                client = self._load_adspower_client()
                client.stop_browser(self.profile_id)
            except Exception:
                pass

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started_profile = False

    def upload_file(
        self,
        file_path: str,
        tags: Optional[List[str]] = None,
        description: str = "",
        content_type: str = "Solo Female",
        keep_audio: bool = False,
        index: int = 1,
        total: int = 1,
    ) -> Dict[str, Any]:
        """Upload one file using the Studio UI."""
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "url": None,
                "error": f"File not found: {file_path}",
                "filename": path.name,
            }

        try:
            self._ensure_connected()
            assert self._page is not None
            page = self._page
            submit_probe: Dict[str, Any] = {
                "seen": False,
                "status": None,
                "id": None,
                "body": "",
            }

            def _on_response(resp) -> None:
                try:
                    url = (resp.url or "").lower()
                    if "/v2/gifs/submit" not in url:
                        return
                    submit_probe["seen"] = True
                    submit_probe["status"] = int(resp.status)
                    text = ""
                    try:
                        text = resp.text() or ""
                    except Exception:
                        text = ""
                    submit_probe["body"] = text[:1200]
                    if text:
                        try:
                            payload = json.loads(text)
                            if isinstance(payload, dict) and payload.get("id"):
                                submit_probe["id"] = str(payload["id"]).strip()
                        except Exception:
                            pass
                except Exception:
                    # Keep response listener non-fatal.
                    return

            page.on("response", _on_response)

            logger.info(f"[Thread {index}/{total}] Browser upload: {path.name}")

            page.goto("https://studio.redgifs.com/upload", wait_until="domcontentloaded")
            if "login" in (page.url or "").lower():
                raise RuntimeError(
                    f"Profile {self.profile_id} is not logged into RedGIFs Studio (redirected to login)"
                )

            file_input = page.locator("input[type='file'][accept*='video'], input[type='file']").first
            file_input.set_input_files(str(path))

            # Wait for metadata step after file is accepted.
            self._wait_for_metadata_step(page)

            self._fill_content_type(page, content_type)
            self._fill_description(page, description)
            self._set_keep_audio(page, keep_audio)
            self._fill_tags(page, tags or [])
            # Re-assert content type because opening other editors can unset it.
            self._fill_content_type(page, content_type)

            publish_btn = self._advance_to_publish(page)
            if publish_btn is not None:
                publish_btn.click()

            watch_url = self._wait_for_publish_success(page, submit_probe)
            logger.info(f"[Thread {index}] Browser publish success: {watch_url or '(no watch URL found)'}")
            return {
                "success": True,
                "url": watch_url,
                "error": None,
                "filename": path.name,
            }
        except Exception as e:
            debug_path = self._dump_debug_artifacts(path.name)
            msg = str(e)
            if debug_path:
                msg = f"{msg} (debug: {debug_path})"
            logger.error(f"[Thread {index}] Browser upload failed: {msg}")
            return {
                "success": False,
                "url": None,
                "error": msg,
                "filename": path.name,
            }
        finally:
            try:
                page.remove_listener("response", _on_response)  # type: ignore[name-defined]
            except Exception:
                pass

    def _wait_for_metadata_step(self, page) -> None:
        deadline = time.time() + 420  # allow large uploads/processing
        while time.time() < deadline:
            url = page.url or ""
            if "/upload/metadata" in url:
                return
            # Metadata textarea appears on some sessions before URL transition.
            if page.locator("textarea[placeholder*='description' i]").count() > 0:
                return
            page.wait_for_timeout(1000)
        raise RuntimeError("Timed out waiting for RedGIFs metadata step")

    def _fill_content_type(self, page, content_type: str) -> None:
        if not content_type:
            return
        # Try native radio first if editor is already open.
        radio = page.locator(f"input[type='radio'][value='{content_type}']")
        if radio.count() == 0:
            self._open_content_type_editor(page)
            radio = page.locator(f"input[type='radio'][value='{content_type}']")

        if radio.count() > 0:
            try:
                radio.first.check(force=True)
            except Exception:
                try:
                    radio.first.click(force=True)
                except Exception:
                    pass

        # Fallback: click matching text label.
        if radio.count() == 0:
            try:
                page.get_by_text(re.compile(rf"^{re.escape(content_type)}$", re.IGNORECASE)).first.click()
            except Exception:
                logger.warning(f"Content type selector not found for '{content_type}'")

        # Close editor when its Continue action is visible.
        try:
            cont = page.get_by_role("button", name=re.compile("^Continue$", re.IGNORECASE))
            if cont.count() > 0 and self._is_enabled(cont.first):
                cont.first.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

    def _fill_description(self, page, description: str) -> None:
        if description is None:
            return
        targets = [
            "textarea[placeholder*='description' i]",
            "textarea[name='description']",
            "textarea",
        ]
        for selector in targets:
            loc = page.locator(selector)
            if loc.count() > 0:
                try:
                    loc.first.fill(description)
                    return
                except Exception:
                    continue

    def _set_keep_audio(self, page, keep_audio: bool) -> None:
        # Optional toggle. Keep errors non-fatal because control is not always present.
        labels = ["Keep Audio", "Keep audio"]
        for label in labels:
            try:
                toggle = page.get_by_label(label)
                if toggle.count() > 0:
                    checked = toggle.first.is_checked()
                    if bool(checked) != bool(keep_audio):
                        toggle.first.click()
                    return
            except Exception:
                continue

    def _fill_tags(self, page, tags: List[str]) -> None:
        if not tags:
            return

        self._open_tags_editor(page)
        self._wait_for_tag_options(page)

        # Current Studio UI uses checkbox tags. Select target tags and then
        # enforce minimum 3 checked tags before continuing.
        logger.info(f"[{self.account_name}] Tag checkboxes before selection: {self._count_checked_tag_boxes(page)}")
        for tag in tags:
            clean = (tag or "").strip()
            if not clean:
                continue
            selectors = [
                page.locator("label").filter(has_text=re.compile(rf"#{re.escape(clean)}\\b", re.IGNORECASE)),
                page.locator("label").filter(has_text=re.compile(rf"\\b{re.escape(clean)}\\b", re.IGNORECASE)),
            ]
            clicked = False
            for group in selectors:
                if group.count() == 0:
                    continue
                for i in range(group.count()):
                    item = group.nth(i)
                    cb = item.locator("input[type='checkbox']")
                    if cb.count() == 0:
                        continue
                    try:
                        if not cb.first.is_checked():
                            cb.first.check(force=True)
                            page.wait_for_timeout(120)
                        logger.info(f"[{self.account_name}] Tag selected: {clean}")
                        clicked = True
                        break
                    except Exception:
                        continue
                if clicked:
                    break
            if not clicked:
                logger.info(
                    f"[{self.account_name}] Tag '{clean}' not found in current visible list; "
                    "fallback tag selection will be used"
                )

        # Fallback for dynamic/search UI: try entering tags if fewer than 3 checkbox tags are checked.
        checked = self._count_checked_tag_boxes(page)
        if checked < 3:
            input_candidates = [
                "input[placeholder*='typing' i]",
                "input[placeholder*='tag' i]",
                "input[type='search']",
            ]
            for selector in input_candidates:
                loc = page.locator(selector)
                if loc.count() == 0:
                    continue
                tag_input = loc.first
                for tag in tags:
                    clean = (tag or "").strip()
                    if not clean:
                        continue
                    try:
                        tag_input.fill(clean)
                        page.wait_for_timeout(350)
                        option = page.get_by_text(re.compile(rf"^#{re.escape(clean)}$", re.IGNORECASE))
                        if option.count() == 0:
                            option = page.get_by_text(re.compile(rf"^{re.escape(clean)}$", re.IGNORECASE))
                        if option.count() > 0:
                            option.first.click()
                        else:
                            tag_input.press("Enter")
                        page.wait_for_timeout(200)
                    except Exception:
                        continue
                break

        # Ensure minimum 3 checked tag checkboxes.
        checked = self._ensure_minimum_tag_selection(page, min_count=3)
        logger.info(f"[{self.account_name}] Tag checkboxes after selection: {checked}")

        # Continue from tags editor if action is visible.
        try:
            cont = page.get_by_role("button", name=re.compile("^Continue$", re.IGNORECASE))
            if cont.count() > 0:
                for i in range(cont.count() - 1, -1, -1):
                    btn = cont.nth(i)
                    if self._is_enabled(btn):
                        btn.click()
                        page.wait_for_timeout(600)
                        break
        except Exception:
            pass

        # If UI still complains about missing tags, force-fill again and continue.
        try:
            if page.locator("text=/Please select 3 or more tags/i").count() > 0:
                checked = self._ensure_minimum_tag_selection(page, min_count=3)
                logger.warning(
                    f"[{self.account_name}] Tags still invalid after first continue, "
                    f"forcing second pass (checked={checked})"
                )
                cont2 = page.get_by_role("button", name=re.compile("^Continue$", re.IGNORECASE))
                if cont2.count() > 0 and self._is_enabled(cont2.first):
                    cont2.first.click()
                    page.wait_for_timeout(600)
        except Exception:
            pass

    def _tag_checkbox_labels(self, page):
        base = page.locator("label").filter(
            has=page.locator("input[type='checkbox']")
        )
        with_posts = base.filter(has_text=re.compile(r"Posts", re.IGNORECASE))
        if with_posts.count() > 0:
            return with_posts
        with_hash = base.filter(has_text=re.compile(r"#"))
        if with_hash.count() > 0:
            return with_hash
        return base

    def _ensure_minimum_tag_selection(self, page, min_count: int = 3) -> int:
        # Reset tag search filter so broad list is available.
        try:
            search = page.locator("input[placeholder*='typing to add tags' i]")
            if search.count() > 0:
                search.first.fill("")
                page.wait_for_timeout(150)
        except Exception:
            pass

        checked = self._count_checked_tag_boxes(page)
        if checked >= min_count:
            return checked

        all_opts = self._tag_checkbox_labels(page)
        for i in range(all_opts.count()):
            if checked >= min_count:
                break
            item = all_opts.nth(i)
            cb = item.locator("input[type='checkbox']")
            if cb.count() == 0:
                continue
            try:
                if cb.first.is_checked():
                    continue
                cb.first.check(force=True)
                page.wait_for_timeout(100)
                if cb.first.is_checked():
                    checked += 1
            except Exception:
                continue
        return checked

    def _is_tags_editor_open(self, page) -> bool:
        try:
            if page.locator("input[placeholder*='typing to add tags' i]").count() > 0:
                return True
            if page.get_by_role("button", name=re.compile("^Suggested$", re.IGNORECASE)).count() > 0:
                return True
            if page.get_by_role("button", name=re.compile("^Trending$", re.IGNORECASE)).count() > 0:
                return True
            if self._tag_checkbox_labels(page).count() > 0:
                return True
        except Exception:
            return False
        return False

    def _open_content_type_editor(self, page, timeout_sec: int = 20) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if page.locator("input[type='radio'][value='Solo Female']").count() > 0:
                return
            selectors = [
                (
                    "xpath=//span[normalize-space()='Content Type']"
                    "/ancestor::div[contains(@class,'shadow-100')][1]"
                    "//button[.//span[normalize-space()='Edit'] or normalize-space()='Edit']"
                ),
            ]
            clicked = False
            for selector in selectors:
                try:
                    btns = page.locator(selector)
                    if btns.count() == 0:
                        continue
                    for i in range(btns.count()):
                        btn = btns.nth(i)
                        if not self._is_enabled(btn):
                            continue
                        btn.click()
                        clicked = True
                        page.wait_for_timeout(400)
                        if page.locator("input[type='radio'][value='Solo Female']").count() > 0:
                            return
                except Exception:
                    continue
            if not clicked:
                page.wait_for_timeout(300)
            else:
                page.wait_for_timeout(200)

    def _open_tags_editor(self, page, timeout_sec: int = 30) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._is_tags_editor_open(page):
                return
            # Strict selectors bound to the Tags card only.
            selectors = [
                (
                    "xpath=//div[.//span[normalize-space()='Tags'] "
                    "and .//span[contains(normalize-space(),'Select 3-10 tags')]]"
                    "//button[.//span[normalize-space()='Edit'] or normalize-space()='Edit']"
                ),
                (
                    "xpath=//span[normalize-space()='Tags']"
                    "/ancestor::div[contains(@class,'shadow-100')][1]"
                    "//button[.//span[normalize-space()='Edit'] or normalize-space()='Edit']"
                ),
            ]
            clicked = False
            for selector in selectors:
                try:
                    btns = page.locator(selector)
                    if btns.count() == 0:
                        continue
                    for i in range(btns.count()):
                        btn = btns.nth(i)
                        if not self._is_enabled(btn):
                            continue
                        btn.click()
                        page.wait_for_timeout(500)
                        clicked = True
                        if self._is_tags_editor_open(page):
                            return
                except Exception:
                    continue
            if not clicked:
                page.wait_for_timeout(300)
            else:
                page.wait_for_timeout(300)
        logger.warning(f"[{self.account_name}] Could not open Tags editor before timeout")

    def _wait_for_tag_options(self, page, timeout_sec: int = 25) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._tag_checkbox_labels(page).count() > 0:
                return
            # Some sessions require opening Suggested/Trending once list mounts.
            for name in ("Suggested", "Trending"):
                try:
                    btn = page.get_by_role("button", name=re.compile(rf"^{name}$", re.IGNORECASE))
                    if btn.count() > 0 and self._is_enabled(btn.first):
                        btn.first.click()
                        page.wait_for_timeout(200)
                except Exception:
                    continue
            page.wait_for_timeout(250)
        logger.warning(f"[{self.account_name}] Tag options did not appear before timeout")

    def _count_checked_tag_boxes(self, page) -> int:
        count = 0
        labels = self._tag_checkbox_labels(page)
        for i in range(labels.count()):
            cb = labels.nth(i).locator("input[type='checkbox']")
            if cb.count() == 0:
                continue
            try:
                if cb.first.is_checked():
                    count += 1
            except Exception:
                continue
        return count

    def _advance_to_publish(self, page):
        deadline = time.time() + 360
        while time.time() < deadline:
            # Already published state can appear without a visible Publish button.
            existing_watch = self._extract_watch_url(page)
            if existing_watch:
                return None
            try:
                body_text = (page.inner_text("body") or "").upper()
                if "PUBLISHED" in body_text and "VIEW ON REDGIFS" in body_text:
                    return None
            except Exception:
                pass

            publish_btn = page.get_by_role("button", name=re.compile("^Publish$", re.IGNORECASE))
            if publish_btn.count() > 0 and self._is_enabled(publish_btn.first):
                return publish_btn.first

            # Continue button can appear before Publish is enabled.
            try:
                continue_btn = page.get_by_role("button", name=re.compile("^Continue$", re.IGNORECASE))
                if continue_btn.count() > 0 and self._is_enabled(continue_btn.first):
                    continue_btn.first.click()
                    page.wait_for_timeout(600)
            except Exception:
                pass

            # Surface explicit page-side errors quickly.
            err = page.locator("text=/required|error|failed/i")
            if err.count() > 0:
                preview = err.first.inner_text().strip()[:220]
                if preview:
                    logger.info(f"Metadata validation message: {preview}")

            page.wait_for_timeout(1000)

        raise RuntimeError("Publish button never became enabled")

    def _wait_for_publish_success(self, page, submit_probe: Optional[Dict[str, Any]] = None) -> Optional[str]:
        deadline = time.time() + 240
        while time.time() < deadline:
            if submit_probe:
                if submit_probe.get("seen") and int(submit_probe.get("status") or 0) >= 400:
                    body = (submit_probe.get("body") or "").strip()
                    raise RuntimeError(
                        f"Publish submit failed with HTTP {submit_probe.get('status')}: {body[:240]}"
                    )
                submit_id = (submit_probe.get("id") or "").strip()
                if submit_id:
                    # A successful submit response includes definitive gif id.
                    return f"https://www.redgifs.com/watch/{submit_id}"

            direct = self._extract_watch_url(page)
            if direct:
                return direct

            current = page.url or ""
            if "/watch/" in current:
                return current
            if "/gifs/" in current and "/upload" not in current:
                return current

            # Sometimes watch URL is rendered as a link on confirmation screen.
            watch_link = page.locator("a[href*='/watch/']")
            if watch_link.count() > 0:
                href = watch_link.first.get_attribute("href")
                if href:
                    href = href.strip()
                    if href.startswith("http"):
                        return href
                    return f"https://www.redgifs.com{href}"

            # Hard error surfaced by UI.
            err_text = page.locator("text=/upload failed|could not be processed|error/i")
            if err_text.count() > 0:
                msg = err_text.first.inner_text().strip()[:240]
                if msg:
                    raise RuntimeError(msg)

            page.wait_for_timeout(1000)

        if submit_probe and submit_probe.get("seen"):
            body = (submit_probe.get("body") or "").strip()
            raise RuntimeError(
                f"Timed out waiting for watch URL after submit HTTP {submit_probe.get('status')}: {body[:240]}"
            )
        raise RuntimeError("Timed out waiting for publish confirmation (no submit response observed)")

    @staticmethod
    def _extract_watch_url(page) -> Optional[str]:
        """Get watch URL from current page if present."""
        try:
            watch_link = page.locator("a[href*='/watch/']")
            if watch_link.count() > 0:
                href = watch_link.first.get_attribute("href")
                if href:
                    href = href.strip()
                    if href.startswith("http"):
                        return href
                    return f"https://www.redgifs.com{href}"
        except Exception:
            pass
        return None

    @staticmethod
    def _is_enabled(locator) -> bool:
        try:
            disabled = locator.get_attribute("disabled")
            aria_disabled = locator.get_attribute("aria-disabled")
            classes = (locator.get_attribute("class") or "").lower()
            if disabled is not None:
                return False
            if str(aria_disabled).lower() == "true":
                return False
            if "disabled" in classes:
                return False
            return True
        except Exception:
            return False

    def _dump_debug_artifacts(self, file_label: str) -> Optional[str]:
        if self._page is None:
            return None
        try:
            root = Path(__file__).resolve().parents[3]
            debug_dir = root / "data" / "debug" / "redgifs_browser"
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_file = re.sub(r"[^A-Za-z0-9._-]+", "_", file_label or "upload")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = debug_dir / f"{stamp}_{self.profile_id}_{safe_file}"

            screenshot = base.with_suffix(".png")
            html_dump = base.with_suffix(".html")
            self._page.screenshot(path=str(screenshot), full_page=False)
            html_dump.write_text(self._page.content(), encoding="utf-8")
            return str(base)
        except Exception:
            return None
