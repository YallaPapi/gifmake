"""
Auto Poster tab for GifMake GUI.
Folder = Profile = Creator campaign model.
Analyzes content with Claude Vision, matches to subs, generates titles,
posts via AdsPower browser profiles with full humanization.
"""
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import webbrowser
import os
import json
import sys
import logging
import time
import random
from datetime import datetime
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.vision_matcher import (
    analyze_image, load_profiles, match_content, random_select_subs,
    select_r4r_subs, scan_content_folder, content_file_hash,
    IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
)
from core.title_generator import generate_titles_batch, generate_r4r_titles
from core.post_history import (
    add_post, get_posted_subs, get_banned_subs, get_posts_today, export_results_csv,
    record_activity, get_sub_performance, get_content_performance
)
from core.post_checker import run_check_cycle
from core.humanizer import Humanizer
from core.ban_detector import check_post_result, check_account_health, BanStatus
from core.spoofer import spoof_file, cleanup_spoof_dir
from core.account_warmer import AccountWarmer
from core.post_history import get_warmup_status, get_warmup_day
from processors.account_profile import (
    ProfileManager,
    AccountProfile,
    AccountAttributes,
    PersonaInterests,
    RedditAccount,
)
import requests as _requests

logger = logging.getLogger(__name__)


def _auto_wrap(label):
    """Bind a label's wraplength to its actual allocated width so text never clips."""
    def _on_resize(event):
        new = event.width - 10
        if new > 50:
            label.configure(wraplength=new)
    label.bind("<Configure>", _on_resize)


def _tooltip(widget, text):
    """Attach a hover tooltip to any widget. Appears after 400ms delay."""
    tip_window = [None]
    delay_id = [None]

    def _show(event):
        def _create():
            if tip_window[0]:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw = __import__("tkinter").Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tw.attributes("-topmost", True)
            lbl = __import__("tkinter").Label(
                tw, text=text, justify="left", wraplength=300,
                background="#1E293B", foreground="#E2E8F0",
                font=("Segoe UI", 10), padx=8, pady=4,
                borderwidth=1, relief="solid",
            )
            lbl.pack()
            tip_window[0] = tw
        delay_id[0] = widget.after(400, _create)

    def _hide(event):
        if delay_id[0]:
            widget.after_cancel(delay_id[0])
            delay_id[0] = None
        tw = tip_window[0]
        if tw:
            tw.destroy()
            tip_window[0] = None

    widget.bind("<Enter>", _show, add="+")
    widget.bind("<Leave>", _hide, add="+")


def _detect_log_tag(msg):
    """Detect the appropriate color tag for a log message."""
    upper = msg.upper()
    if any(k in upper for k in ("SUCCESS", "VERIFIED", " OK", "COMPLETE", "DONE")):
        return "success"
    if any(k in upper for k in ("ERROR", "FAILED", "FATAL", "BANNED", "SUSPENDED")):
        return "error"
    if any(k in upper for k in ("WARN", "SKIP", "RATE LIMIT", "STOPPED", "CAPPED")):
        return "warning"
    if any(k in upper for k in ("ANALYZ", "GENERAT", "CONNECT", "STARTING", "LOADING",
                                 "WARMUP", "PERSONA", "SPOOFED")):
        return "info"
    if msg.lstrip().startswith("===") or msg.lstrip().startswith("---"):
        return "header"
    return None


def _setup_log_tags(textbox):
    """Configure color tags on a CTkTextbox for styled log output."""
    tags = {
        "success": {"foreground": "#22C55E"},
        "error":   {"foreground": "#EF4444"},
        "warning": {"foreground": "#F59E0B"},
        "info":    {"foreground": "#60A5FA"},
        "header":  {"foreground": "#2DD4BF", "font": ("Consolas", 11, "bold")},
        "muted":   {"foreground": "#94A3B8"},
        "link":    {"foreground": "#38BDF8", "underline": True},
    }
    for tag, opts in tags.items():
        textbox._textbox.tag_config(tag, **opts)

    # Make "link" tags clickable — open URL in default browser
    import webbrowser as _wb
    import re as _re

    def _on_link_click(event):
        widget = event.widget
        idx = widget.index(f"@{event.x},{event.y}")
        tag_ranges = widget.tag_ranges("link")
        for i in range(0, len(tag_ranges), 2):
            start, end = str(tag_ranges[i]), str(tag_ranges[i + 1])
            if widget.compare(idx, ">=", start) and widget.compare(idx, "<=", end):
                url = widget.get(start, end).strip()
                if url.startswith("http"):
                    _wb.open(url)
                break

    textbox._textbox.tag_bind("link", "<Button-1>", _on_link_click)
    textbox._textbox.tag_bind("link", "<Enter>",
        lambda e: textbox._textbox.configure(cursor="hand2"))
    textbox._textbox.tag_bind("link", "<Leave>",
        lambda e: textbox._textbox.configure(cursor=""))


_URL_RE = __import__("re").compile(r'https?://\S+')


def _apply_link_tags(textbox, msg):
    """Scan the last inserted line for URLs and apply the 'link' tag."""
    for m in _URL_RE.finditer(msg):
        url = m.group()
        # Find the URL in the last line of the textbox
        line_end = textbox._textbox.index("end-1c")
        line_start = textbox._textbox.index(f"{line_end} linestart")
        line_text = textbox._textbox.get(line_start, line_end)
        url_pos = line_text.find(url)
        if url_pos >= 0:
            start = f"{line_start}+{url_pos}c"
            end = f"{start}+{len(url)}c"
            textbox._textbox.tag_add("link", start, end)

ADSPOWER_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "uploaders", "redgifs", "adspower_config.json"
)
ACCOUNT_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "account_profiles.json"
)
CARD_FG = ("#F7F9FC", "#1E2633")
HERO_FG = ("#EAF3FF", "#1B2A40")
ACCENT = "#0F766E"
ACCENT_HOVER = "#115E59"
SUCCESS = "#166534"
SUCCESS_HOVER = "#14532D"
WARN = "#B91C1C"
WARN_HOVER = "#991B1B"
SECONDARY = "#334155"
SECONDARY_HOVER = "#1F2937"


def _collapsible_section(parent, title, row, default_open=True, right_widgets=None):
    """Create a collapsible card section with arrow toggle.

    Args:
        parent: Parent frame to grid into.
        title: Section title text.
        row: Grid row in parent.
        default_open: Whether section starts expanded.
        right_widgets: List of (text, command, fg_color, hover_color) for header buttons.

    Returns:
        (card, content_frame, toggle_fn, title_label)
    """
    card = ctk.CTkFrame(parent, fg_color=CARD_FG, corner_radius=12,
                         border_width=1, border_color=("#D1D9E6", "#2E3A4F"))
    card.grid(row=row, column=0, sticky="ew", padx=5, pady=(0, 6))
    card.grid_columnconfigure(0, weight=1)

    header = ctk.CTkFrame(card, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    header.grid_columnconfigure(1, weight=1)

    visible = [default_open]

    arrow_label = ctk.CTkLabel(
        header, text="\u25BC" if default_open else "\u25B6",
        font=("Segoe UI", 12), width=20, cursor="hand2")
    arrow_label.grid(row=0, column=0, padx=(2, 4))

    title_label = ctk.CTkLabel(
        header, text=title, font=("Segoe UI", 14, "bold"), cursor="hand2")
    title_label.grid(row=0, column=1, sticky="w")

    if right_widgets:
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.grid(row=0, column=2)
        for btn_text, btn_cmd, btn_fg, btn_hover in right_widgets:
            ctk.CTkButton(
                btn_frame, text=btn_text, width=110,
                fg_color=btn_fg, hover_color=btn_hover,
                font=("Segoe UI", 11, "bold"), command=btn_cmd,
            ).pack(side="left", padx=3)

    content = ctk.CTkFrame(card, fg_color="transparent")
    content.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
    content.grid_columnconfigure(0, weight=1)
    if not default_open:
        content.grid_remove()

    def toggle():
        visible[0] = not visible[0]
        if visible[0]:
            content.grid()
            arrow_label.configure(text="\u25BC")
        else:
            content.grid_remove()
            arrow_label.configure(text="\u25B6")

    arrow_label.bind("<Button-1>", lambda e: toggle())
    title_label.bind("<Button-1>", lambda e: toggle())

    return card, content, toggle, title_label


class _GUILogHandler(logging.Handler):
    """Routes log records from core.account_warmer to a CTkTextbox."""

    def __init__(self, textbox, app):
        super().__init__()
        self.textbox = textbox
        self.app = app

    def emit(self, record):
        msg = self.format(record)
        try:
            self.app.after(0, self._append, msg)
        except Exception:
            pass

    def _append(self, msg):
        tag = _detect_log_tag(msg)
        if tag:
            self.textbox.insert("end", f"{msg}\n", tag)
        else:
            self.textbox.insert("end", f"{msg}\n")
        _apply_link_tags(self.textbox, msg)
        self.textbox.see("end")


class Campaign:
    """One campaign = one folder + one AdsPower profile."""

    def __init__(self, folder_path, profile_id, profile_name):
        self.folder = folder_path
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.files = []
        self.analysis = {}       # file_path -> vision_result
        self.posting_plan = []   # list of PostItem dicts
        self.status = "idle"     # idle, analyzing, ready, posting, done, error
        self.stop_requested = False
        self.warmer = None


class AutoPosterTab:
    """Full campaign-based auto poster tab."""

    def __init__(self, parent_frame, app):
        self.parent = parent_frame
        self.app = app
        self.campaigns = []
        self.profiles_data = {}
        self.adspower_config = {}
        self.sub_profiles = {}
        self.sub_tiers = {}
        self.persona_profiles = {}
        self.is_running = False
        self.stop_all = False
        self._active_warmups = {}   # {profile_id: {"warmer": warmer, "stop": False}}
        self._active_proxy_groups = {}  # {proxy_group: profile_id} — prevents concurrent use
        self._warmup_log_handler = None
        self._last_action_log = []

        self._load_adspower_config()
        self._load_sub_data()
        self._load_persona_profiles()
        self._queue_config = self._load_queue_config()
        self._create_widgets()

    def _load_adspower_config(self):
        """Load AdsPower profile config."""
        try:
            if os.path.exists(ADSPOWER_CONFIG_PATH):
                with open(ADSPOWER_CONFIG_PATH, encoding="utf-8") as f:
                    self.adspower_config = json.load(f)
                self.profiles_data = {
                    p["profile_id"]: p["account_name"]
                    for p in self.adspower_config.get("profiles", [])
                }
        except Exception as e:
            logger.error(f"Failed to load AdsPower config: {e}")

    def _load_sub_data(self):
        """Load subreddit profiles and tiers."""
        try:
            self.sub_profiles, self.sub_tiers, self.sub_data = load_profiles()
        except Exception as e:
            logger.error(f"Failed to load sub data: {e}")

    def _load_persona_profiles(self):
        try:
            if os.path.exists(ACCOUNT_PROFILES_PATH):
                with open(ACCOUNT_PROFILES_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                self.persona_profiles = data.get("profiles", {})
        except Exception as e:
            logger.error(f"Failed to load persona profiles: {e}")

    def _load_queue_config(self):
        """Load queue config for proxy group rotation URLs."""
        qc_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "queue_config.json")
        try:
            if os.path.exists(qc_path):
                with open(qc_path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load queue config: {e}")
        return {}

    def _get_account_proxy_group(self, profile_id):
        """Get proxy_group for an account from persona_profiles."""
        # Search by adspower_id
        for key, prof in self.persona_profiles.items():
            if prof.get("adspower_id") == profile_id:
                return prof.get("proxy_group", "")
        return ""

    def _rotate_proxy(self, proxy_group):
        """Rotate the proxy for a given proxy group by hitting its rotation URL."""
        if not proxy_group:
            return True
        pg = self._queue_config.get("proxy_groups", {}).get(proxy_group, {})
        rotation_url = pg.get("rotation_url", "").strip()
        if not rotation_url:
            self.app.after(0, self._log,
                f"[Proxy] No rotation URL for {proxy_group}, skipping rotation")
            return True
        wait_sec = pg.get("wait_after_rotate_sec", 10)
        try:
            self.app.after(0, self._log,
                f"[Proxy] Rotating {proxy_group}...")
            resp = _requests.get(rotation_url, timeout=30)
            snippet = resp.text[:100].strip()
            self.app.after(0, self._log,
                f"[Proxy] Rotation response: {snippet}")
            time.sleep(wait_sec)
            self.app.after(0, self._log,
                f"[Proxy] Waited {wait_sec}s after rotation")
            return True
        except Exception as e:
            self.app.after(0, self._log,
                f"[Proxy] ERROR rotating {proxy_group}: {e}")
            return False

    def _acquire_proxy_group(self, proxy_group, profile_id):
        """Try to acquire exclusive access to a proxy group. Returns True if acquired."""
        if not proxy_group:
            return True  # No proxy group = no restriction
        current_user = self._active_proxy_groups.get(proxy_group)
        if current_user and current_user != profile_id:
            return False
        self._active_proxy_groups[proxy_group] = profile_id
        return True

    def _release_proxy_group(self, proxy_group, profile_id):
        """Release a proxy group lock."""
        if proxy_group and self._active_proxy_groups.get(proxy_group) == profile_id:
            del self._active_proxy_groups[proxy_group]

    def _build_account_map(self):
        """Build dropdown options with persona-linked accounts first."""
        self._acct_map = {}
        options = []
        persona_by_ads = {}
        for key, profile in self.persona_profiles.items():
            ads_id = (profile.get("adspower_id") or "").strip()
            if ads_id:
                persona_by_ads[ads_id] = key

        def _sort_key(item):
            ads_id = item[0]
            has_persona = 0 if ads_id in persona_by_ads else 1
            return (has_persona, item[1].lower())

        for ads_id, account_name in sorted(self.profiles_data.items(), key=_sort_key):
            persona_key = persona_by_ads.get(ads_id)
            username = account_name
            if persona_key and persona_key in self.persona_profiles:
                username = self.persona_profiles[persona_key].get(
                    "reddit_account", {}).get("username", account_name)
            display = f"{username} ({ads_id})"
            self._acct_map[display] = (ads_id, persona_key)
            options.append(display)

        if not options:
            options = ["No accounts configured"]
        return options

    def _build_account_table(self):
        """Build/rebuild the account table rows with status tags and action buttons."""
        # Clear existing rows
        for row_info in self._acct_table_rows:
            row_info["frame"].destroy()
        self._acct_table_rows = []

        ROW_BG = ("#FFFFFF", "#111827")
        ROW_HOVER = ("#F1F5F9", "#1E293B")
        ROW_SELECTED = ("#DBEAFE", "#1E3A5F")

        acct_options = list(self._acct_map.keys())
        for row_idx, display_str in enumerate(acct_options):
            ads_id, persona_key = self._acct_map[display_str]

            # Get proxy group and day from profile data
            prof = self.persona_profiles.get(persona_key, {})
            proxy_group = prof.get("proxy_group", "")
            username = prof.get("reddit_account", {}).get("username", "") or display_str

            # Get warmup day
            try:
                day = get_warmup_day(ads_id)
                day_text = f"Day {day}"
            except Exception:
                day_text = "--"

            # Get status
            status_text, status_color = self._get_account_status(ads_id)

            # Determine row bg based on selection
            is_selected = (display_str == self._selected_acct_display)
            bg = ROW_SELECTED if is_selected else ROW_BG

            row_frame = ctk.CTkFrame(
                self._acct_table_frame, fg_color=bg, height=32,
                corner_radius=4,
            )
            row_frame.grid(row=row_idx, column=0, sticky="ew", pady=1)
            row_frame.grid_columnconfigure(0, weight=1)
            row_frame.grid_columnconfigure(1, minsize=70)
            row_frame.grid_columnconfigure(2, minsize=80)
            row_frame.grid_columnconfigure(3, minsize=50)
            row_frame.grid_columnconfigure(4, minsize=150)

            # Col 0: Username
            uname_lbl = ctk.CTkLabel(
                row_frame, text=username, font=("Segoe UI", 11),
                text_color=("#1F2937", "#E5E7EB"), anchor="w",
            )
            uname_lbl.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=2)

            # Col 1: Status tag
            status_label = ctk.CTkLabel(
                row_frame, text=status_text, font=("Segoe UI", 10, "bold"),
                text_color=status_color, anchor="w",
            )
            status_label.grid(row=0, column=1, sticky="w", padx=4, pady=2)

            # Col 2: Proxy group
            proxy_lbl = ctk.CTkLabel(
                row_frame, text=proxy_group or "-", font=("Segoe UI", 11),
                text_color=("#6366F1", "#818CF8") if proxy_group else ("#9CA3AF", "#6B7280"),
                anchor="w",
            )
            proxy_lbl.grid(row=0, column=2, sticky="w", padx=4, pady=2)

            # Col 3: Warmup day
            day_lbl = ctk.CTkLabel(
                row_frame, text=day_text, font=("Segoe UI", 11),
                text_color=("#475569", "#94A3B8"), anchor="w",
            )
            day_lbl.grid(row=0, column=3, sticky="w", padx=4, pady=2)

            # Col 4: Actions (Start Warmup / Stop / Edit)
            action_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            action_frame.grid(row=0, column=4, padx=(4, 4), pady=2)

            is_active = (ads_id in self._active_warmups or
                         any(c.profile_id == ads_id and c.status == "posting"
                             for c in self.campaigns))

            warmup_btn = ctk.CTkButton(
                action_frame, text="\u25B6 Warm", width=55, height=24,
                fg_color="#1E40AF", hover_color="#1E3A8A",
                font=("Segoe UI", 10), corner_radius=6,
                command=lambda ds=display_str: self._start_warmup_for_account(ds),
            )
            warmup_btn.pack(side="left", padx=2)
            if is_active:
                warmup_btn.configure(state="disabled")

            stop_btn = ctk.CTkButton(
                action_frame, text="\u25A0 Stop", width=50, height=24,
                fg_color=WARN, hover_color=WARN_HOVER,
                font=("Segoe UI", 10), corner_radius=6,
                command=lambda ds=display_str: self._stop_single_account(ds),
            )
            if is_active:
                stop_btn.pack(side="left", padx=2)

            edit_btn = ctk.CTkButton(
                action_frame, text="Edit", width=36, height=24,
                fg_color="#7C3AED", hover_color="#6D28D9",
                font=("Segoe UI", 10), corner_radius=6,
                command=lambda ds=display_str: self._edit_account_from_table(ds),
            )
            edit_btn.pack(side="left", padx=2)

            del_btn = ctk.CTkButton(
                action_frame, text="✕", width=28, height=24,
                fg_color="#DC2626", hover_color="#B91C1C",
                font=("Segoe UI", 10, "bold"), corner_radius=6,
                command=lambda ds=display_str: self._remove_account_from_table(ds),
            )
            del_btn.pack(side="left", padx=2)
            _tooltip(del_btn, "Delete this account from the app")

            # Click anywhere on row to select
            def _on_click(event, ds=display_str):
                self._select_account_row(ds)
            for widget in [row_frame, uname_lbl, status_label, proxy_lbl, day_lbl]:
                widget.bind("<Button-1>", _on_click)

            # Hover effect
            def _on_enter(event, f=row_frame, ds=display_str):
                if ds != self._selected_acct_display:
                    f.configure(fg_color=ROW_HOVER)
            def _on_leave(event, f=row_frame, ds=display_str):
                if ds != self._selected_acct_display:
                    f.configure(fg_color=ROW_BG)
            for widget in [row_frame, uname_lbl, status_label, proxy_lbl, day_lbl]:
                widget.bind("<Enter>", _on_enter)
                widget.bind("<Leave>", _on_leave)

            self._acct_table_rows.append({
                "frame": row_frame,
                "display_str": display_str,
                "ads_id": ads_id,
                "persona_key": persona_key,
                "status_label": status_label,
                "warmup_btn": warmup_btn,
                "stop_btn": stop_btn,
            })

    def _select_account_row(self, display_str):
        """Select an account row — update highlights and all dependent state."""
        if display_str not in self._acct_map:
            return

        self._selected_acct_display = display_str
        self.acct_var.set(display_str)

        ROW_BG = ("#FFFFFF", "#111827")
        ROW_SELECTED = ("#DBEAFE", "#1E3A5F")

        # Update row highlights
        for row_info in self._acct_table_rows:
            if row_info["display_str"] == display_str:
                row_info["frame"].configure(fg_color=ROW_SELECTED)
            else:
                row_info["frame"].configure(fg_color=ROW_BG)

        # Update profile_entry and persona_var (backward compat)
        ads_id, persona_key = self._acct_map[display_str]
        self.profile_entry.delete(0, "end")
        self.profile_entry.insert(0, ads_id)
        self.persona_var.set(persona_key or "")

        # Build persona summary
        prof = self.persona_profiles.get(persona_key, {})
        persona = prof.get("persona", {})
        parts = []
        display_name = prof.get("display_name") or persona_key or "Unknown"
        parts.append(str(display_name))
        loc = persona.get("location", "")
        if loc:
            parts.append(str(loc))
        hobbies = persona.get("hobbies", [])
        if hobbies:
            parts.append(", ".join(str(h) for h in hobbies[:5] if h))
        traits = persona.get("personality_traits", [])
        if traits:
            parts.append(f"({', '.join(str(t) for t in traits[:3] if t)})")

        self.persona_info_label.configure(
            text=f"Persona: {' - '.join(p for p in parts if p)}")

        # Refresh lifetime stats
        self._refresh_life_stats(silent=True)

    def _edit_account_from_table(self, display_str):
        """Select account and open edit dialog."""
        self._select_account_row(display_str)
        self._open_add_account_dialog()

    def _remove_account_from_table(self, display_str):
        """Remove an account via its table row."""
        if display_str not in self._acct_map:
            return
        ads_id, persona_key = self._acct_map[display_str]
        username = display_str.split(" (")[0]

        if not messagebox.askyesno(
            "Remove Account",
            f"Remove '{username}' ({ads_id})?\n\n"
            "This removes the account from AdsPower config and persona profiles.",
        ):
            return

        # Remove from AdsPower config
        try:
            if os.path.exists(ADSPOWER_CONFIG_PATH):
                with open(ADSPOWER_CONFIG_PATH, encoding="utf-8") as f:
                    config = json.load(f)
                config["profiles"] = [
                    p for p in config.get("profiles", [])
                    if p.get("profile_id") != ads_id
                ]
                with open(ADSPOWER_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to remove from AdsPower config: {e}")

        # Remove from persona profiles
        if persona_key:
            try:
                manager = ProfileManager(ACCOUNT_PROFILES_PATH)
                manager.remove_profile(persona_key)
            except Exception as e:
                logger.error(f"Failed to remove persona profile: {e}")

        # Reload everything
        self._reload_accounts()
        self._log(f"Removed account: {username} ({ads_id})")

    def _get_account_status(self, profile_id):
        """Return (status_text, color) for an account's current state."""
        if profile_id in self._active_warmups:
            return "Warmup", "#2563EB"
        for campaign in self.campaigns:
            if campaign.profile_id == profile_id and campaign.status == "posting":
                return "Posting", "#16A34A"
        for campaign in self.campaigns:
            if campaign.profile_id == profile_id and campaign.status == "analyzing":
                return "Analyzing", "#D97706"
        return "Idle", "#6B7280"

    def _start_warmup_for_account(self, display_str):
        """Start warmup for a specific account from its table row."""
        self._select_account_row(display_str)
        self._start_standalone_warmup()

    def _stop_single_account(self, display_str):
        """Stop warmup/posting for a specific account."""
        if display_str not in self._acct_map:
            return
        ads_id, _ = self._acct_map[display_str]
        # Stop warmup if running
        if ads_id in self._active_warmups:
            self._active_warmups[ads_id]["stop"] = True
            w = self._active_warmups[ads_id].get("warmer")
            if w:
                w.stop_requested = True
        # Stop campaigns for this profile
        for campaign in self.campaigns:
            if campaign.profile_id == ads_id:
                campaign.stop_requested = True
                if campaign.warmer:
                    campaign.warmer.stop_requested = True
        self._log(f"Stop requested for {display_str.split(' (')[0]}")
        self._refresh_account_statuses()

    def _refresh_account_statuses(self):
        """Update all status labels and button visibility in the account table."""
        for row_info in self._acct_table_rows:
            pid = row_info["ads_id"]
            status_text, status_color = self._get_account_status(pid)
            sl = row_info.get("status_label")
            if sl:
                sl.configure(text=status_text, text_color=status_color)
            # Show/hide stop button, enable/disable warmup button
            wb = row_info.get("warmup_btn")
            sb = row_info.get("stop_btn")
            is_active = (pid in self._active_warmups or
                         any(c.profile_id == pid and c.status == "posting"
                             for c in self.campaigns))
            if sb:
                if is_active:
                    sb.pack(side="left", padx=2)
                else:
                    sb.pack_forget()
            if wb:
                wb.configure(state="disabled" if is_active else "normal")

    def _open_apply_settings_dialog(self):
        """Open dialog to apply current settings to multiple accounts."""
        if not self.persona_profiles:
            messagebox.showinfo("No Accounts", "Add accounts first.")
            return

        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Apply Settings to Accounts")
        dialog.geometry("500x500")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(dialog,
            text="Select accounts to apply the current warmup & posting settings to.\n"
                 "'Same creator' also copies content folders.",
            font=("Segoe UI", 12), justify="left", wraplength=460,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))

        mode_var = ctk.StringVar(value="different")
        mode_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        mode_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkRadioButton(mode_frame, text="Settings only (diff. creators)",
                            variable=mode_var, value="different",
                            font=("Segoe UI", 11)).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(mode_frame, text="Settings + content (same creator)",
                            variable=mode_var, value="same",
                            font=("Segoe UI", 11)).pack(side="left")

        scroll = ctk.CTkScrollableFrame(dialog, height=260)
        scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        scroll.grid_columnconfigure(0, weight=1)

        check_vars = {}
        for key, prof in self.persona_profiles.items():
            var = ctk.BooleanVar(value=False)
            display = prof.get("display_name", key)
            username = prof.get("reddit_account", {}).get("username", "")
            proxy = prof.get("proxy_group", "")
            label_text = f"{display} ({username})"
            if proxy:
                label_text += f"  [{proxy}]"
            ctk.CTkCheckBox(
                scroll, text=label_text, variable=var, font=("Segoe UI", 12),
            ).pack(anchor="w", padx=8, pady=2)
            check_vars[key] = var

        sel_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        sel_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkButton(sel_frame, text="Select All", width=90,
                       fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                       font=("Segoe UI", 11),
                       command=lambda: [v.set(True) for v in check_vars.values()]
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(sel_frame, text="Deselect All", width=100,
                       fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                       font=("Segoe UI", 11),
                       command=lambda: [v.set(False) for v in check_vars.values()]
        ).pack(side="left")

        def _apply():
            selected = [k for k, v in check_vars.items() if v.get()]
            if not selected:
                messagebox.showwarning("Nothing Selected",
                                        "Select at least one account.",
                                        parent=dialog)
                return
            include_content = (mode_var.get() == "same")
            self._apply_settings_to_accounts(selected, include_content)
            dialog.destroy()

        ctk.CTkButton(dialog, text="Apply Settings", height=40, width=160,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                       font=("Segoe UI", 13, "bold"), command=_apply
        ).grid(row=4, column=0, pady=(0, 12))

    def _apply_settings_to_accounts(self, persona_keys, include_content=False):
        """Apply current settings to selected accounts.

        Settings are global (one set of widgets), so they already apply
        to whichever account runs next. The real value is in 'same creator'
        mode which clones content folder campaigns to other accounts.
        """
        if include_content:
            current_ads_id = self.profile_entry.get().strip()
            source_folders = [
                c.folder for c in self.campaigns
                if c.profile_id == current_ads_id
            ]
            for persona_key in persona_keys:
                prof = self.persona_profiles.get(persona_key, {})
                ads_id = prof.get("adspower_id", "")
                if not ads_id or ads_id == current_ads_id:
                    continue
                for folder in source_folders:
                    self.add_campaign_from_folder(folder, preferred_profile_id=ads_id)

        count = len(persona_keys)
        suffix = " with content folders" if include_content else ""
        self._log(f"Settings applied to {count} account(s){suffix}")

    # ── Warmup All dialog ──────────────────────────────────────────

    def _open_warmup_all_dialog(self):
        """Open dialog to select accounts and launch warmup for all of them."""
        if not self.persona_profiles:
            messagebox.showinfo("No Accounts", "Add accounts first.")
            return

        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Warmup All Accounts")
        dialog.geometry("520x520")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(dialog,
            text="Select accounts to launch warmup for.\n"
                 "Accounts already warming up are skipped.\n"
                 "Accounts sharing a proxy group will queue automatically.",
            font=("Segoe UI", 12), justify="left", wraplength=480,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))

        # Group accounts by proxy_group
        groups = {}  # {group_name: [(persona_key, prof, ads_id), ...]}
        for key, prof in self.persona_profiles.items():
            ads_id = prof.get("adspower_id", "")
            pg = prof.get("proxy_group", "") or "Ungrouped"
            groups.setdefault(pg, []).append((key, prof, ads_id))

        scroll = ctk.CTkScrollableFrame(dialog, height=300)
        scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        scroll.grid_columnconfigure(0, weight=1)

        check_vars = {}   # {persona_key: BooleanVar}
        group_vars = {}   # {group_name: BooleanVar}

        for group_name in sorted(groups.keys()):
            accounts = groups[group_name]

            # Group header with select-all checkbox
            g_var = ctk.BooleanVar(value=True)
            group_vars[group_name] = g_var

            g_frame = ctk.CTkFrame(scroll, fg_color=("#E2E8F0", "#1E293B"),
                                   corner_radius=8)
            g_frame.pack(fill="x", padx=4, pady=(8, 2))
            g_frame.grid_columnconfigure(1, weight=1)

            def _make_group_toggle(gn, gv, accts):
                def _toggle():
                    val = gv.get()
                    for pk, _, _ in accts:
                        if pk in check_vars:
                            check_vars[pk].set(val)
                return _toggle

            g_cb = ctk.CTkCheckBox(
                g_frame,
                text=f"  {group_name}  ({len(accounts)} accounts)",
                variable=g_var, font=("Segoe UI", 12, "bold"),
                command=_make_group_toggle(group_name, g_var, accounts),
            )
            g_cb.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=4)

            # Individual accounts under this group
            for persona_key, prof, ads_id in accounts:
                var = ctk.BooleanVar(value=True)
                check_vars[persona_key] = var

                display_name = prof.get("display_name", persona_key)
                username = (prof.get("reddit_account") or {}).get("username", "")
                status_text, status_color = self._get_account_status(ads_id)

                row_f = ctk.CTkFrame(scroll, fg_color="transparent")
                row_f.pack(fill="x", padx=20, pady=1)
                row_f.grid_columnconfigure(1, weight=1)

                ctk.CTkCheckBox(
                    row_f, text=f"{display_name} ({username})",
                    variable=var, font=("Segoe UI", 11),
                ).grid(row=0, column=0, sticky="w")

                ctk.CTkLabel(
                    row_f, text=status_text, font=("Segoe UI", 10, "bold"),
                    text_color=status_color,
                ).grid(row=0, column=1, sticky="e", padx=(8, 4))

        # Select/Deselect buttons
        sel_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        sel_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkButton(sel_frame, text="Select All", width=90,
                       fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                       font=("Segoe UI", 11),
                       command=lambda: [v.set(True)
                                        for d in (check_vars, group_vars)
                                        for v in d.values()]
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(sel_frame, text="Deselect All", width=100,
                       fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                       font=("Segoe UI", 11),
                       command=lambda: [v.set(False)
                                        for d in (check_vars, group_vars)
                                        for v in d.values()]
        ).pack(side="left")

        def _launch():
            selected_keys = [k for k, v in check_vars.items() if v.get()]
            if not selected_keys:
                messagebox.showwarning("Nothing Selected",
                                       "Select at least one account.",
                                       parent=dialog)
                return
            dialog.destroy()
            self._launch_warmup_batch(selected_keys)

        ctk.CTkButton(dialog, text="Launch Warmup", height=40, width=180,
                       fg_color="#2563EB", hover_color="#1D4ED8",
                       font=("Segoe UI", 13, "bold"), command=_launch
        ).grid(row=4, column=0, pady=(0, 12))

    def _launch_warmup_batch(self, persona_keys):
        """Launch warmup for a list of accounts by persona key.

        Iterates through each account, selects it, and calls the existing
        _start_standalone_warmup(). Skips accounts already warming up.
        """
        launched = 0
        skipped_active = 0
        for key in persona_keys:
            prof = self.persona_profiles.get(key, {})
            ads_id = prof.get("adspower_id", "")
            if not ads_id:
                continue

            # Skip already-active accounts
            if ads_id in self._active_warmups:
                skipped_active += 1
                continue

            # Find the display_str for this account
            display_str = None
            for ds, (a_id, _) in self._acct_map.items():
                if a_id == ads_id:
                    display_str = ds
                    break
            if not display_str:
                continue

            # Select the row and launch warmup
            self._select_account_row(display_str)
            self._start_standalone_warmup()
            launched += 1

        parts = [f"Launched warmup for {launched} account(s)"]
        if skipped_active:
            parts.append(f"{skipped_active} already running")
        self._log(" | ".join(parts))

    def _load_api_keys(self):
        """Load API keys from config/api_keys.json."""
        keys_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "api_keys.json")
        try:
            if os.path.exists(keys_path):
                with open(keys_path, encoding="utf-8-sig") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _resolve_api_keys(self):
        """Resolve API keys from hidden entries, config file, and environment."""
        saved_keys = self._load_api_keys()

        claude_key = self.claude_key_entry.get().strip()
        if not claude_key:
            claude_key = (
                (saved_keys.get("claude_api_key") or "").strip()
                or (saved_keys.get("anthropic_api_key") or "").strip()
                or os.environ.get("ANTHROPIC_API_KEY", "").strip()
                or os.environ.get("CLAUDE_API_KEY", "").strip()
            )
            if claude_key:
                self.claude_key_entry.delete(0, "end")
                self.claude_key_entry.insert(0, claude_key)

        grok_key = self.grok_key_entry.get().strip()
        if not grok_key:
            grok_key = (
                (saved_keys.get("grok_api_key") or "").strip()
                or (saved_keys.get("xai_api_key") or "").strip()
                or os.environ.get("GROK_API_KEY", "").strip()
            )
            if grok_key:
                self.grok_key_entry.delete(0, "end")
                self.grok_key_entry.insert(0, grok_key)

        return claude_key, grok_key

    def _slugify_profile_id(self, username):
        base = re.sub(r"[^a-z0-9]+", "_", (username or "").lower()).strip("_")
        return base or "reddit_account"

    def _build_profile_options(self):
        options = [
            f"{name} ({pid})"
            for pid, name in sorted(self.profiles_data.items(), key=lambda item: item[1].lower())
        ]
        return options or ["No accounts configured"]

    def _parse_profile_selection(self, selected_value):
        selected = (selected_value or "").strip()
        if "(" in selected and selected.endswith(")"):
            pid = selected.rsplit("(", 1)[-1].rstrip(")").strip()
            name = selected.rsplit(" (", 1)[0].strip()
            return pid, name
        return selected, selected

    def _persist_adspower_mapping(self, adspower_id, account_name):
        """Ensure AdsPower config has profile_id -> account_name mapping."""
        try:
            config = dict(self.adspower_config or {})
            profiles = list(config.get("profiles", []))
            found = False
            for profile in profiles:
                if profile.get("profile_id") == adspower_id:
                    profile["account_name"] = account_name
                    found = True
                    break
            if not found:
                profiles.append({
                    "profile_id": adspower_id,
                    "account_name": account_name,
                })
            config["profiles"] = profiles
            with open(ADSPOWER_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            self.adspower_config = config
            self.profiles_data = {
                p.get("profile_id", ""): p.get("account_name", "")
                for p in profiles if p.get("profile_id")
            }
        except Exception as e:
            raise RuntimeError(f"Could not update AdsPower config: {e}") from e

    def _refresh_stats_bar(self):
        green_count = sum(1 for v in self.sub_tiers.values() if v.get("tier") == "GREEN")
        profiles_count = len(self.sub_profiles)
        ads_count = len(self.profiles_data)

        key_status = []
        key_status.append("Claude OK" if self._claude_key else "Claude key missing")
        key_status.append("Grok OK" if self._grok_key else "Grok key missing")

        self.stats_label.configure(
            text=f"{profiles_count:,} sub profiles | {green_count:,} GREEN tier | "
                 f"{ads_count} accounts | {' | '.join(key_status)}"
        )

    def _refresh_campaign_profile_options(self, preferred_profile_id=None):
        options = self._build_profile_options()
        has_real_accounts = bool(self.profiles_data)

        for widgets in self.campaign_widgets:
            dropdown = widgets["profile_dropdown"]
            profile_var = widgets["profile_var"]
            campaign = widgets["campaign"]
            dropdown.configure(values=options)

            selected = profile_var.get()
            if preferred_profile_id:
                preferred = next(
                    (opt for opt in options if opt.endswith(f"({preferred_profile_id})")),
                    None,
                )
                if preferred:
                    selected = preferred
            if selected not in options:
                selected = options[0]
            profile_var.set(selected)

            pid, pname = self._parse_profile_selection(selected)
            if has_real_accounts:
                campaign.profile_id = pid
                campaign.profile_name = self.profiles_data.get(pid, pname)

    def _reload_accounts(self):
        self._load_adspower_config()
        self._load_persona_profiles()
        self._queue_config = self._load_queue_config()
        # Rebuild account map and table
        acct_options = self._build_account_map()
        self._build_account_table()
        current = self.acct_var.get()
        if current not in self._acct_map and acct_options:
            self._select_account_row(acct_options[0])
        self._refresh_stats_bar()
        self._refresh_campaign_profile_options()
        self._log("Account list reloaded.")

    def _remove_account_dialog(self):
        """Show a picker to remove an account from configs."""
        if not self.profiles_data:
            messagebox.showinfo("No Accounts", "No accounts to remove.")
            return

        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Remove Account")
        dialog.geometry("400x200")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            dialog, text="Select account to remove:",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))

        options = [f"{name} ({pid})" for pid, name in sorted(self.profiles_data.items())]
        pick_var = ctk.StringVar(value=options[0])
        ctk.CTkOptionMenu(dialog, variable=pick_var, values=options).grid(
            row=1, column=0, sticky="ew", padx=16, pady=4)

        def _do_remove():
            selected = pick_var.get()
            pid = selected.rsplit("(", 1)[-1].rstrip(")").strip() if "(" in selected else ""
            username = selected.rsplit(" (", 1)[0].strip() if " (" in selected else selected
            if not pid:
                return

            # Remove from adspower_config.json
            config = dict(self.adspower_config or {})
            config["profiles"] = [
                p for p in config.get("profiles", [])
                if p.get("profile_id") != pid
            ]
            with open(ADSPOWER_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)

            # Remove from account_profiles.json if present
            try:
                manager = ProfileManager(ACCOUNT_PROFILES_PATH)
                to_del = [
                    k for k, v in manager.profiles.items()
                    if v.adspower_id == pid
                ]
                for k in to_del:
                    del manager.profiles[k]
                if to_del:
                    manager.save()
            except Exception:
                pass

            self._reload_accounts()
            self._log(f"Removed account: {username} ({pid})")
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(10, 12))
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            btn_frame, text="Remove", height=34,
            fg_color=WARN, hover_color=WARN_HOVER,
            font=("Segoe UI", 12, "bold"), command=_do_remove,
        ).grid(row=0, column=0, sticky="ew", padx=5)
        ctk.CTkButton(
            btn_frame, text="Cancel", height=34,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            font=("Segoe UI", 12), command=dialog.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=5)

    def _open_add_account_dialog(self, blank=False):
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("New Account" if blank else "Edit Persona")
        dialog.geometry("520x700")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.grab_set()
        dialog.after(100, dialog.focus_force)
        dialog.lift()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        # ── Card-based color palette ──
        BG_CARD = ("#FFFFFF", "#1E293B")
        BG_INPUT = ("#F1F5F9", "#0F172A")
        BORDER = ("#E2E8F0", "#334155")
        LABEL_CLR = ("#475569", "#94A3B8")
        SECTION_CLR = ("#0F766E", "#2DD4BF")
        MUTED = ("#64748B", "#64748B")

        entries = {}

        # ── ROW 0: Header + action buttons at TOP ──
        top_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        top_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        top_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top_frame,
            text="Account & Persona Setup",
            font=("Segoe UI", 17, "bold"),
            text_color=("#0F172A", "#F1F5F9"),
        ).grid(row=0, column=0, sticky="w")

        btn_bar = ctk.CTkFrame(top_frame, fg_color="transparent")
        btn_bar.grid(row=1, column=0, sticky="w", pady=(10, 0))

        # ── ROW 1: Scrollable card form ──
        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        scroll.grid_columnconfigure(0, weight=1)

        card_row = [0]  # mutable row counter for scroll frame

        def _card(title):
            """Create a rounded card frame with a section header."""
            card = ctk.CTkFrame(
                scroll, fg_color=BG_CARD, corner_radius=12,
                border_width=1, border_color=BORDER,
            )
            card.grid(row=card_row[0], column=0, sticky="ew", padx=6, pady=(8, 0))
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=1)
            card_row[0] += 1
            ctk.CTkLabel(
                card, text=title, font=("Segoe UI", 12, "bold"),
                text_color=SECTION_CLR,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2), columnspan=2)
            card._r = 1
            return card

        def _field(card, label, key, default="", placeholder=""):
            """Label-above-entry field inside a card (full width)."""
            r = card._r
            ctk.CTkLabel(
                card, text=label, font=("Segoe UI", 11),
                text_color=LABEL_CLR,
            ).grid(row=r, column=0, sticky="w", padx=14, pady=(6, 0), columnspan=2)
            entry = ctk.CTkEntry(
                card, height=36, fg_color=BG_INPUT,
                border_width=0, corner_radius=8,
                placeholder_text=placeholder, font=("Segoe UI", 12),
            )
            if default:
                entry.insert(0, default)
            entry.grid(row=r + 1, column=0, sticky="ew", padx=14, pady=(2, 6),
                       columnspan=2)
            entries[key] = entry
            card._r = r + 2

        def _inline_pair(card, lbl1, key1, lbl2, key2,
                         default1="", dropdown_vals=None, dropdown_default=""):
            """Two fields side-by-side: entry on left, dropdown on right."""
            r = card._r
            # Left: label + entry
            ctk.CTkLabel(
                card, text=lbl1, font=("Segoe UI", 11),
                text_color=LABEL_CLR,
            ).grid(row=r, column=0, sticky="w", padx=(14, 4), pady=(6, 0))
            e1 = ctk.CTkEntry(
                card, height=36, width=120, fg_color=BG_INPUT,
                border_width=0, corner_radius=8, font=("Segoe UI", 12),
            )
            if default1:
                e1.insert(0, default1)
            e1.grid(row=r + 1, column=0, sticky="w", padx=(14, 4), pady=(2, 6))
            entries[key1] = e1
            # Right: label + dropdown
            ctk.CTkLabel(
                card, text=lbl2, font=("Segoe UI", 11),
                text_color=LABEL_CLR,
            ).grid(row=r, column=1, sticky="w", padx=(4, 14), pady=(6, 0))
            if dropdown_vals:
                var = ctk.StringVar(value=dropdown_default)
                ctk.CTkOptionMenu(
                    card, variable=var, values=dropdown_vals,
                    width=100, height=36, corner_radius=8,
                    fg_color=BG_INPUT, button_color=ACCENT,
                    button_hover_color=ACCENT_HOVER, font=("Segoe UI", 12),
                ).grid(row=r + 1, column=1, sticky="w", padx=(4, 14), pady=(2, 6))
                entries[key2] = var
            card._r = r + 2

        # ═══ CARD 1: Account ═══
        c1 = _card("Account")
        _field(c1, "Reddit Username *", "username", placeholder="e.g. midnightmaemood")
        _field(c1, "AdsPower ID *", "adspower_id", placeholder="e.g. k19mnl5p")
        _field(c1, "Display Name", "display_name", placeholder="e.g. Mae")
        _field(c1, "Account Age (days)", "age_days", "0")
        _field(c1, "Proxy Group", "proxy_group", placeholder="proxy_1")

        # ═══ CARD 2: Persona ═══
        c2 = _card("Persona")
        _inline_pair(c2, "Age", "persona_age", "Gender", "gender",
                     default1="25", dropdown_vals=["F", "M", "NB"],
                     dropdown_default="F")
        _field(c2, "Location", "location", placeholder="Austin, TX")

        # ═══ CARD 3: Personality & Interests ═══
        c3 = _card("Personality & Interests")
        _field(c3, "Hobbies", "hobbies", placeholder="art, music, yoga, gaming")
        _field(c3, "Interests", "interests",
               placeholder="horror, coffee, cats, astrology")
        _field(c3, "Personality Traits", "personality",
               placeholder="witty, moody, creative")
        _field(c3, "Favorite Subs", "favorite_subs",
               placeholder="Austin, tattoos, cats, Coffee, horror")

        # ── Auto-detect info note ──
        ctk.CTkLabel(
            scroll,
            text="Physical attributes & content tags are auto-detected "
                 "when you analyze content.",
            font=("Segoe UI", 10), text_color=MUTED, justify="left",
        ).grid(row=card_row[0], column=0, sticky="w", padx=20, pady=(10, 4))
        card_row[0] += 1

        # ── Randomize helper ──
        def _randomize():
            import random as _rng
            _NAMES = [
                "Luna", "Sage", "Ivy", "Jade", "Violet", "Scarlett", "Ruby",
                "Daisy", "Willow", "Aurora", "Stella", "Cleo", "Nora", "Aria",
                "Zara", "Mila", "Ember", "Lily", "Nova", "Maya", "Kira",
                "Raven", "Hazel", "Sienna", "Freya", "Camille", "Vera",
            ]
            _CITIES = [
                "Austin, TX", "Miami, FL", "Portland, OR", "Seattle, WA",
                "Denver, CO", "Nashville, TN", "Chicago, IL", "Brooklyn, NY",
                "Los Angeles, CA", "Atlanta, GA", "San Diego, CA", "Phoenix, AZ",
                "Tampa, FL", "Charlotte, NC", "Dallas, TX", "Houston, TX",
                "New Orleans, LA", "Minneapolis, MN", "Raleigh, NC", "Columbus, OH",
                "Las Vegas, NV", "Orlando, FL", "Salt Lake City, UT", "Sacramento, CA",
            ]
            _HOBBIES = [
                "yoga", "hiking", "cooking", "photography", "painting", "gaming",
                "reading", "thrifting", "dancing", "surfing", "skateboarding",
                "tattoos", "music", "baking", "gardening", "traveling", "pilates",
                "cycling", "running", "pottery", "journaling", "singing",
                "swimming", "rock climbing", "camping", "karaoke",
            ]
            _INTERESTS = [
                "horror", "coffee", "cats", "dogs", "astrology", "true crime",
                "anime", "fashion", "vintage", "plants", "crystals", "tarot",
                "vinyl", "film", "90s nostalgia", "streetwear", "skincare",
                "memes", "sci-fi", "mythology", "philosophy", "cocktails",
                "brunch", "sneakers", "K-pop", "indie music", "thrift fashion",
                "sustainability", "space", "psychology", "board games",
            ]
            _TRAITS = [
                "witty", "sarcastic", "chill", "moody", "creative", "bubbly",
                "introverted", "chaotic", "laid-back", "ambitious", "goofy",
                "blunt", "curious", "flirty", "nerdy", "spontaneous", "dry humor",
            ]
            _SUBS_POOL = [
                "AskReddit", "cats", "dogs", "Coffee", "tattoos", "yoga",
                "houseplants", "horror", "movies", "Music", "food", "cooking",
                "hiking", "photography", "art", "gaming", "books", "fashion",
                "ThriftStoreHauls", "CozyPlaces", "itookapicture", "Astronomy",
                "SkincareAddiction", "MakeupAddiction", "TwoXChromosomes",
                "memes", "funny", "aww", "NatureIsFuckingLit", "EarthPorn",
                "Showerthoughts", "TrueOffMyChest", "unpopularopinion",
            ]
            name = _rng.choice(_NAMES)
            city = _rng.choice(_CITIES)
            age = _rng.randint(21, 29)
            hobbies = _rng.sample(_HOBBIES, _rng.randint(3, 6))
            interests = _rng.sample(_INTERESTS, _rng.randint(4, 7))
            traits = _rng.sample(_TRAITS, _rng.randint(3, 5))
            fav_subs = _rng.sample(_SUBS_POOL, _rng.randint(8, 14))
            city_name = city.split(",")[0].strip().replace(" ", "")
            fav_subs.insert(0, city_name)
            fill = {
                "display_name": name,
                "persona_age": str(age),
                "location": city,
                "proxy_group": _rng.choice(["proxy_1", "proxy_2", "proxy_3", "proxy_4"]),
                "hobbies": ", ".join(hobbies),
                "interests": ", ".join(interests),
                "personality": ", ".join(traits),
                "favorite_subs": ", ".join(fav_subs),
            }
            for key, val in fill.items():
                w = entries.get(key)
                if w and isinstance(w, ctk.CTkEntry):
                    w.delete(0, "end")
                    w.insert(0, val)
            entries.get("gender", ctk.StringVar()).set("F")

        # ── Pre-fill if editing existing account ──
        if not blank:
            try:
                sel = self.acct_var.get()
                if sel in self._acct_map:
                    ads_id, persona_key = self._acct_map[sel]
                    if persona_key and persona_key in self.persona_profiles:
                        p = self.persona_profiles[persona_key]
                        _prefill = {
                            "username": p.get("reddit_account", {}).get("username", ""),
                            "adspower_id": p.get("adspower_id", ""),
                            "display_name": p.get("display_name", ""),
                            "age_days": str(p.get("reddit_account", {}).get("age_days", 0)),
                            "proxy_group": p.get("proxy_group", ""),
                            "persona_age": str(p.get("attributes", {}).get("age", 25)),
                            "location": p.get("persona", {}).get("location", ""),
                            "hobbies": ", ".join(p.get("persona", {}).get("hobbies", [])),
                            "interests": ", ".join(p.get("persona", {}).get("interests", [])),
                            "personality": ", ".join(
                                p.get("persona", {}).get("personality_traits", [])),
                            "favorite_subs": ", ".join(
                                p.get("persona", {}).get("favorite_subs", [])),
                        }
                        for key, val in _prefill.items():
                            if key in entries and val:
                                w = entries[key]
                                if isinstance(w, ctk.CTkEntry):
                                    w.delete(0, "end")
                                    w.insert(0, val)
                        if "gender" in entries:
                            entries["gender"].set(
                                p.get("attributes", {}).get("gender", "F"))
            except Exception:
                pass

        # ── Value helpers ──
        def _get(key, fallback=""):
            w = entries.get(key)
            if w is None:
                return fallback
            if isinstance(w, ctk.StringVar):
                return w.get().strip() or fallback
            return w.get().strip() or fallback

        def _csv(key):
            return [x.strip() for x in _get(key).split(",") if x.strip()]

        # ── Save handler ──
        def _save():
            username = _get("username")
            adspower_id = _get("adspower_id")
            display_name = _get("display_name") or username

            if not username or not adspower_id:
                messagebox.showerror(
                    "Missing Fields", "Username and AdsPower ID are required.",
                    parent=dialog)
                return

            try:
                age_days = max(0, int(_get("age_days", "0")))
            except ValueError:
                messagebox.showerror(
                    "Invalid", "Account Age must be a number.", parent=dialog)
                return
            try:
                persona_age = max(18, int(_get("persona_age", "25")))
            except ValueError:
                persona_age = 25

            try:
                manager = ProfileManager(ACCOUNT_PROFILES_PATH)
                existing = None
                for profile in manager.get_all_profiles():
                    if (profile.adspower_id == adspower_id
                            or profile.reddit_account.username.lower() == username.lower()):
                        existing = profile
                        break

                profile_id = (existing.profile_id if existing
                              else self._slugify_profile_id(username))
                if not existing and profile_id in manager.profiles:
                    suffix = 2
                    while f"{profile_id}_{suffix}" in manager.profiles:
                        suffix += 1
                    profile_id = f"{profile_id}_{suffix}"

                if existing:
                    attributes = existing.attributes
                    attributes.age = persona_age
                    attributes.gender = _get("gender", "F")
                else:
                    attributes = AccountAttributes(
                        age=persona_age, gender=_get("gender", "F"))

                persona = PersonaInterests(
                    location=_get("location"),
                    hobbies=_csv("hobbies"),
                    interests=_csv("interests"),
                    personality_traits=_csv("personality"),
                    favorite_subs=_csv("favorite_subs"),
                    comment_style="casual",
                )

                reddit_account = (existing.reddit_account if existing
                                  else RedditAccount(username=username))
                reddit_account.username = username
                reddit_account.age_days = age_days

                new_profile = AccountProfile(
                    profile_id=profile_id,
                    display_name=display_name,
                    attributes=attributes,
                    adspower_id=adspower_id,
                    persona=persona,
                    title_templates=(existing.title_templates if existing
                                    else {"default": "{title}"}),
                    flair_mappings=existing.flair_mappings if existing else {},
                    content_tags=existing.content_tags if existing else [],
                    reddit_account=reddit_account,
                    created_at=(existing.created_at if existing
                                else datetime.now().isoformat()),
                    creator=existing.creator if existing else "",
                    proxy_group=_get("proxy_group"),
                )
                manager.add_profile(new_profile)
                self._persist_adspower_mapping(adspower_id, username)
                self._load_adspower_config()
                self._load_persona_profiles()
                self._build_account_map()
                self._build_account_table()
                self._refresh_stats_bar()
                self._refresh_campaign_profile_options(
                    preferred_profile_id=adspower_id)
                # Re-select this account in the table
                for ds, (aid, _) in self._acct_map.items():
                    if aid == adspower_id:
                        self._select_account_row(ds)
                        break
                self._log(f"Account saved: {username} ({adspower_id})")
                dialog.destroy()
            except Exception as e:
                import traceback; traceback.print_exc()
                messagebox.showerror(
                    "Save Failed", f"Could not save account:\n{e}",
                    parent=dialog)

        # ── Create top action buttons ──
        ctk.CTkButton(
            btn_bar, text="Save Account", height=38, width=140,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=("Segoe UI", 12, "bold"), corner_radius=10,
            command=_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_bar, text="Randomize", height=38, width=120,
            fg_color="#D97706", hover_color="#B45309",
            font=("Segoe UI", 12, "bold"), corner_radius=10,
            command=_randomize,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_bar, text="Cancel", height=38, width=100,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            font=("Segoe UI", 12), corner_radius=10,
            command=dialog.destroy,
        ).pack(side="left")

    def _create_widgets(self):
        """Build single-page Reddit interface with collapsible sections."""
        self.parent.grid_columnconfigure(0, weight=1)

        # Load API keys silently from config (no visible entry fields)
        saved_keys = self._load_api_keys()
        self._claude_key = saved_keys.get("claude_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self._grok_key = saved_keys.get("grok_api_key", "") or os.environ.get("GROK_API_KEY", "")

        # Hidden entry widgets (other code reads from these)
        self.claude_key_entry = ctk.CTkEntry(self.parent)
        self.grok_key_entry = ctk.CTkEntry(self.parent)
        if self._claude_key:
            self.claude_key_entry.insert(0, self._claude_key)
        if self._grok_key:
            self.grok_key_entry.insert(0, self._grok_key)

        sec_row = 0  # Track section rows in self.parent

        # ═════════════════════════════════════════════════════════════
        # SECTION 0: ACCOUNTS (default open)
        # ═════════════════════════════════════════════════════════════
        _, acct_content, _, _ = _collapsible_section(
            self.parent, "Accounts", row=sec_row, default_open=True,
            right_widgets=[
                ("+ New Account",
                 lambda: self._open_add_account_dialog(blank=True),
                 ACCENT, ACCENT_HOVER),
                ("Warmup All", self._open_warmup_all_dialog,
                 "#2563EB", "#1D4ED8"),
                ("Apply Settings", self._open_apply_settings_dialog,
                 "#D97706", "#B45309"),
                ("Reload", self._reload_accounts, SECONDARY, SECONDARY_HOVER),
            ])

        # Table header
        TBL_HDR_BG = ("#E2E8F0", "#1E293B")
        tbl_hdr = ctk.CTkFrame(acct_content, fg_color=TBL_HDR_BG, height=28,
                                corner_radius=0)
        tbl_hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(0, 0))
        tbl_hdr.grid_columnconfigure(0, weight=1)
        _col_tips = {
            "Status": "Shows what each account is doing right now (Idle, Warmup, Posting)",
            "Proxy": "Which mobile proxy group this account uses",
            "Day": "How many days this account has been warming up",
            "Actions": "Start warmup, stop, or edit each account",
        }
        for col_idx, (txt, w) in enumerate([
            ("Username", 0), ("Status", 70), ("Proxy", 80), ("Day", 50),
            ("Actions", 150),
        ]):
            lbl = ctk.CTkLabel(tbl_hdr, text=txt, font=("Segoe UI", 11, "bold"),
                                text_color=("#475569", "#94A3B8"))
            if w:
                lbl.grid(row=0, column=col_idx, sticky="w", padx=4)
                tbl_hdr.grid_columnconfigure(col_idx, minsize=w)
            else:
                lbl.grid(row=0, column=col_idx, sticky="ew", padx=4)
                tbl_hdr.grid_columnconfigure(col_idx, weight=1)
            if txt in _col_tips:
                _tooltip(lbl, _col_tips[txt])

        # Scrollable table body
        self._acct_table_frame = ctk.CTkScrollableFrame(
            acct_content, height=180,
            fg_color=("#F8FAFC", "#0F172A"),
            border_width=1, border_color=("#D1D9E6", "#2E3A4F"),
        )
        self._acct_table_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._acct_table_frame.grid_columnconfigure(0, weight=1)
        self._acct_table_rows = []
        self._selected_acct_display = None

        # Build initial account map and table
        acct_options = self._build_account_map()
        self.acct_var = ctk.StringVar(value=acct_options[0] if acct_options else "")
        self._build_account_table()

        # Persona info line (for selected account)
        self.persona_info_label = ctk.CTkLabel(
            acct_content, text="Select an account above", font=("Segoe UI", 12),
            text_color=("#374151", "#CBD5E1"), justify="left"
        )
        self.persona_info_label.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 2))
        _auto_wrap(self.persona_info_label)

        # Lifetime stats line
        life_row = ctk.CTkFrame(acct_content, fg_color="transparent")
        life_row.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))

        self.day_label = ctk.CTkLabel(
            life_row, text="Day --", font=("Segoe UI", 13, "bold"),
            text_color=("#334155", "#E2E8F0"))
        self.day_label.pack(side="left", padx=(0, 14))

        self.life_upvotes = ctk.CTkLabel(
            life_row, text="0 votes", font=("Segoe UI", 12, "bold"),
            text_color=("#16A34A", "#22C55E"))
        self.life_upvotes.pack(side="left", padx=(0, 12))

        self.life_comments = ctk.CTkLabel(
            life_row, text="0 comments", font=("Segoe UI", 12, "bold"),
            text_color=("#2563EB", "#3B82F6"))
        self.life_comments.pack(side="left", padx=(0, 12))

        self.life_joins = ctk.CTkLabel(
            life_row, text="0 joins", font=("Segoe UI", 12, "bold"),
            text_color=("#D97706", "#F59E0B"))
        self.life_joins.pack(side="left", padx=(0, 12))

        self.life_posts = ctk.CTkLabel(
            life_row, text="0 posts", font=("Segoe UI", 12, "bold"),
            text_color=("#7C3AED", "#A78BFA"))
        self.life_posts.pack(side="left")

        # Hidden profile_entry for backward compat with warmup worker
        self.profile_entry = ctk.CTkEntry(self.parent)
        self.persona_var = ctk.StringVar(value="")

        # Select first account
        if acct_options and acct_options[0] in self._acct_map:
            self._select_account_row(acct_options[0])

        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 1: CONTENT CAMPAIGNS (default collapsed)
        # ═════════════════════════════════════════════════════════════
        self._camp_card, camp_content, self._camp_toggle, self._camp_title = \
            _collapsible_section(
                self.parent, "Content Campaigns", row=sec_row, default_open=False,
                right_widgets=[
                    ("+ Add Folder", self._add_campaign, ACCENT, ACCENT_HOVER),
                ])
        self._camp_visible = False

        self.campaigns_frame = ctk.CTkScrollableFrame(
            camp_content, height=120,
            fg_color=("#EEF2F7", "#1A2230"),
            border_width=1, border_color=("#D1D9E6", "#2E3A4F"),
        )
        self.campaigns_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.campaigns_frame.grid_columnconfigure(0, weight=1)
        self.campaign_widgets = []
        self.stats_label = ctk.CTkLabel(
            camp_content, text="", font=("Segoe UI", 11),
            text_color=("#4B5563", "#94A3B8"))
        self.stats_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))
        self._refresh_stats_bar()
        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 2: WARMUP SETTINGS (default collapsed)
        # ═════════════════════════════════════════════════════════════
        _, warmup_content, _, _ = _collapsible_section(
            self.parent, "Warmup Settings", row=sec_row, default_open=False,
            right_widgets=[
                ("Warmup Only", self._start_standalone_warmup,
                 "#1E40AF", "#1E3A8A"),
            ])

        # Warmup mode radios
        wrow1 = ctk.CTkFrame(warmup_content, fg_color="transparent")
        wrow1.grid(row=0, column=0, sticky="ew", padx=12, pady=(2, 4))

        ctk.CTkLabel(wrow1, text="Mode:", font=("Segoe UI", 12, "bold")).pack(
            side="left", padx=(0, 8))
        self.warmup_mode_var = ctk.StringVar(value="interleaved")
        _mode_tips = {
            "Skip": "Don't do any warmup — jump straight to posting",
            "Browse between posts": "Browse Reddit naturally between each post to look more human",
            "Full warmup first": "Complete a full warmup session before any posting begins",
        }
        for label_text, value in [("Skip", "none"),
                                   ("Browse between posts", "interleaved"),
                                   ("Full warmup first", "full")]:
            rb = ctk.CTkRadioButton(wrow1, text=label_text,
                                variable=self.warmup_mode_var, value=value,
                                font=("Segoe UI", 11))
            rb.pack(side="left", padx=(0, 12))
            _tooltip(rb, _mode_tips[label_text])

        # 2x2 control grid
        wgrid = ctk.CTkFrame(warmup_content, fg_color="transparent")
        wgrid.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 4))
        wgrid.grid_columnconfigure((0, 1), weight=1)

        # Row 1: Browse time range + Session range
        wleft = ctk.CTkFrame(wgrid, fg_color="transparent")
        wleft.grid(row=0, column=0, columnspan=2, sticky="ew")

        browse_lbl = ctk.CTkLabel(wleft, text="Browse:", font=("Segoe UI", 11))
        browse_lbl.pack(side="left", padx=(0, 4))
        _tooltip(browse_lbl, "How long (in seconds) to browse Reddit between posts.\nA random value is picked from this range each time.")
        self.browse_min_entry = ctk.CTkEntry(wleft, width=36)
        self.browse_min_entry.pack(side="left")
        self.browse_min_entry.insert(0, "80")
        ctk.CTkLabel(wleft, text="-", font=("Segoe UI", 11)).pack(
            side="left", padx=2)
        self.browse_max_entry = ctk.CTkEntry(wleft, width=36)
        self.browse_max_entry.pack(side="left")
        self.browse_max_entry.insert(0, "150")
        ctk.CTkLabel(wleft, text="sec", font=("Segoe UI", 10),
                     text_color=("#666", "#999")).pack(side="left", padx=(4, 16))

        session_lbl = ctk.CTkLabel(wleft, text="Session:", font=("Segoe UI", 11))
        session_lbl.pack(side="left", padx=(0, 4))
        _tooltip(session_lbl, "Total warmup session length in minutes.\nThe account will browse, vote, and comment for this duration.")
        self.session_min_entry = ctk.CTkEntry(wleft, width=36)
        self.session_min_entry.pack(side="left")
        self.session_min_entry.insert(0, "15")
        ctk.CTkLabel(wleft, text="-", font=("Segoe UI", 11)).pack(
            side="left", padx=2)
        self.session_max_entry = ctk.CTkEntry(wleft, width=36)
        self.session_max_entry.pack(side="left")
        self.session_max_entry.insert(0, "30")
        ctk.CTkLabel(wleft, text="min", font=("Segoe UI", 10),
                     text_color=("#666", "#999")).pack(side="left", padx=(4, 0))

        # Row 2: Hijack + Comments range
        wright = ctk.CTkFrame(wgrid, fg_color="transparent")
        wright.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        hijack_lbl = ctk.CTkLabel(wright, text="Hijack:", font=("Segoe UI", 11))
        hijack_lbl.pack(side="left", padx=(0, 4))
        _tooltip(hijack_lbl, "Chance of clicking into a post to read/engage with it\ninstead of just scrolling past. Higher = more realistic browsing.")
        self.hijack_slider = ctk.CTkSlider(wright, from_=0, to=100,
                                            width=80, number_of_steps=10)
        self.hijack_slider.pack(side="left")
        self.hijack_slider.set(40)
        self.hijack_label = ctk.CTkLabel(wright, text="40%",
                                          font=("Segoe UI", 11), width=30)
        self.hijack_label.pack(side="left", padx=(2, 16))
        self.hijack_slider.configure(
            command=lambda v: self.hijack_label.configure(text=f"{int(v)}%"))

        comments_lbl = ctk.CTkLabel(wright, text="Comments:", font=("Segoe UI", 11))
        comments_lbl.pack(side="left", padx=(0, 4))
        _tooltip(comments_lbl, "Number of comments to leave per warmup session.\nComments are AI-generated based on actual post content.")
        self.max_comments_entry = ctk.CTkEntry(wright, width=36)
        self.max_comments_entry.pack(side="left")
        self.max_comments_entry.insert(0, "10")
        ctk.CTkLabel(wright, text="-", font=("Segoe UI", 11)).pack(
            side="left", padx=2)
        self.max_comments_high_entry = ctk.CTkEntry(wright, width=36)
        self.max_comments_high_entry.pack(side="left")
        self.max_comments_high_entry.insert(0, "20")

        ctk.CTkLabel(warmup_content,
                     text="(0 = unlimited, random value picked from range each session)",
                     font=("Segoe UI", 10),
                     text_color=("#666", "#999")).grid(
            row=2, column=0, sticky="w", padx=16, pady=(0, 4))

        # ── Advanced Warmup Settings (nested collapsible) ──
        self._adv_warmup_visible = False

        def _toggle_advanced():
            self._adv_warmup_visible = not self._adv_warmup_visible
            if self._adv_warmup_visible:
                adv_frame.grid()
                adv_toggle.configure(text="\u25B2  Hide Advanced Settings")
            else:
                adv_frame.grid_remove()
                adv_toggle.configure(text="\u25BC  Advanced Settings")

        adv_toggle = ctk.CTkButton(
            warmup_content, text="\u25BC  Advanced Settings",
            fg_color="transparent", text_color=ACCENT,
            hover_color=("#E0F2FE", "#1E3A5F"),
            font=("Segoe UI", 12), anchor="w", height=28,
            command=_toggle_advanced)
        adv_toggle.grid(row=3, column=0, sticky="w", padx=8, pady=(2, 4))

        adv_frame = ctk.CTkFrame(warmup_content,
                                  fg_color=("#F1F5F9", "#141D2B"),
                                  corner_radius=8, border_width=1,
                                  border_color=("#D1D9E6", "#2E3A4F"))
        adv_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        adv_frame.grid_remove()  # Start hidden
        adv_frame.grid_columnconfigure((0, 1), weight=1)

        def _adv_field(parent, r, c, label, default=""):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.grid(row=r, column=c, sticky="ew", padx=8, pady=4)
            ctk.CTkLabel(f, text=label, font=("Segoe UI", 11)).pack(
                side="left", padx=(0, 4))
            e = ctk.CTkEntry(f, width=50)
            e.pack(side="left")
            if default:
                e.insert(0, default)
            return e

        self.adv_max_comments = _adv_field(adv_frame, 0, 0, "Max comments/day:")
        self.adv_max_votes = _adv_field(adv_frame, 0, 1, "Max votes/day:")
        self.adv_max_joins = _adv_field(adv_frame, 1, 0, "Max joins/day:")
        self.adv_min_nsfw_days = _adv_field(adv_frame, 1, 1,
                                             "Min days before NSFW:", "14")

        self.adv_cqs_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(adv_frame, text="Run CQS check after warmup",
                         variable=self.adv_cqs_var,
                         font=("Segoe UI", 11)).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 2))

        ctk.CTkLabel(adv_frame,
                     text="Leave blank to use automatic phase-based caps.",
                     font=("Segoe UI", 10),
                     text_color=("#4B5563", "#94A3B8")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 3: POSTING SETTINGS (default collapsed)
        # ═════════════════════════════════════════════════════════════
        _, post_content, _, _ = _collapsible_section(
            self.parent, "Posting Settings", row=sec_row, default_open=False)

        # Hidden compat widget
        self.subs_per_file = ctk.CTkEntry(post_content, width=40)
        self.subs_per_file.insert(0, "1")

        # Main posting controls
        prow1 = ctk.CTkFrame(post_content, fg_color="transparent")
        prow1.grid(row=0, column=0, sticky="ew", padx=12, pady=(2, 4))

        daily_lbl = ctk.CTkLabel(prow1, text="Daily limit:", font=("Segoe UI", 12, "bold"))
        daily_lbl.pack(side="left", padx=(0, 6))
        _tooltip(daily_lbl, "Maximum number of posts per account per day.\nKeep this low for new accounts to avoid suspicion.")
        self.daily_limit_entry = ctk.CTkEntry(prow1, width=50)
        self.daily_limit_entry.pack(side="left", padx=(0, 4))
        self.daily_limit_entry.insert(0, "8")
        ctk.CTkLabel(prow1, text="posts/day", font=("Segoe UI", 11)).pack(
            side="left", padx=(0, 16))

        self.spoof_var = ctk.BooleanVar(value=True)
        spoof_cb = ctk.CTkCheckBox(prow1, text="Spoof files", variable=self.spoof_var,
                         font=("Segoe UI", 12))
        spoof_cb.pack(side="left", padx=(0, 12))
        _tooltip(spoof_cb, "Slightly alter each file before posting so Reddit\ncan't detect duplicate uploads across accounts.")

        self.auto_mode_var = ctk.BooleanVar(value=False)
        auto_cb = ctk.CTkCheckBox(prow1, text="Auto-post (no preview)",
                         variable=self.auto_mode_var,
                         font=("Segoe UI", 12))
        auto_cb.pack(side="left", padx=(0, 8))
        _tooltip(auto_cb, "Skip the preview step and post immediately\nafter analysis. Use when you trust the AI picks.")

        # Subreddit Filters
        ctk.CTkLabel(post_content, text="Subreddit Filters",
                     font=("Segoe UI", 11, "bold"),
                     text_color=ACCENT).grid(
            row=1, column=0, sticky="w", padx=12, pady=(2, 2))

        prow2 = ctk.CTkFrame(post_content, fg_color="transparent")
        prow2.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))

        _filter_tips = {
            "Max subs:": "Only post to subreddits smaller than this.\nSmaller subs have less strict automod.",
            "Min subs:": "Only post to subreddits with at least this many members.\nToo small = not worth posting.",
            "Match quality:": "How closely the content must match the subreddit topic.\nHigher = stricter matching, fewer but better-targeted posts.",
        }
        for lbl, attr, w, default in [
            ("Max subs:", "max_subs_entry", 70, "100000"),
            ("Min subs:", "min_subs_entry", 60, "2500"),
            ("Match quality:", "min_score_entry", 50, "20"),
        ]:
            filter_lbl = ctk.CTkLabel(prow2, text=lbl, font=("Segoe UI", 11))
            filter_lbl.pack(side="left", padx=(0, 4))
            _tooltip(filter_lbl, _filter_tips[lbl])
            entry = ctk.CTkEntry(prow2, width=w)
            entry.pack(side="left", padx=(0, 12))
            entry.insert(0, default)
            setattr(self, attr, entry)

        # R4R Mode
        r4r_hdr = ctk.CTkLabel(post_content, text="R4R Mode",
                     font=("Segoe UI", 11, "bold"),
                     text_color=ACCENT)
        r4r_hdr.grid(row=3, column=0, sticky="w", padx=12, pady=(2, 2))
        _tooltip(r4r_hdr, "R4R = 'Redditor for Redditor' (hookup/personals subs).\nThese subs have different rules and title formats.")

        prow3 = ctk.CTkFrame(post_content, fg_color="transparent")
        prow3.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 4))

        ctk.CTkLabel(prow3, text="Mode:", font=("Segoe UI", 11)).pack(
            side="left", padx=(0, 4))
        self.r4r_mode_var = ctk.StringVar(value="off")
        self.r4r_mode_menu = ctk.CTkOptionMenu(
            prow3, variable=self.r4r_mode_var,
            values=["Off", "Trickle", "Area Focus", "Blast"],
            width=110, font=("Segoe UI", 11))
        self.r4r_mode_menu.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(prow3, text="R4R subs/file:", font=("Segoe UI", 11)).pack(
            side="left", padx=(0, 4))
        self.r4r_count_entry = ctk.CTkEntry(prow3, width=36)
        self.r4r_count_entry.pack(side="left")
        self.r4r_count_entry.insert(0, "2")

        # Campaign context
        prow4 = ctk.CTkFrame(post_content, fg_color="transparent")
        prow4.grid(row=5, column=0, sticky="ew", padx=12, pady=(2, 6))
        ctk.CTkLabel(prow4, text="Campaign context:", font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=(0, 4))
        self.campaign_context_entry = ctk.CTkEntry(prow4, width=400,
            placeholder_text="e.g. 'traveling to NYC this weekend' (optional)")
        self.campaign_context_entry.pack(side="left", padx=(0, 4), fill="x", expand=True)

        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 4: ACTIONS & PROGRESS (always open, not collapsible)
        # ═════════════════════════════════════════════════════════════
        action_card = ctk.CTkFrame(self.parent, fg_color=CARD_FG, corner_radius=12,
                                    border_width=1, border_color=("#D1D9E6", "#2E3A4F"))
        action_card.grid(row=sec_row, column=0, sticky="ew", padx=5, pady=(0, 6))
        action_card.grid_columnconfigure(0, weight=1)

        btn_frame = ctk.CTkFrame(action_card, fg_color="transparent")
        btn_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.analyze_btn = ctk.CTkButton(
            btn_frame, text="Analyze Content", font=("Segoe UI", 13, "bold"),
            height=40, fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._start_analysis
        )
        self.analyze_btn.grid(row=0, column=0, sticky="ew", padx=3)
        _tooltip(self.analyze_btn,
                 "Use AI vision to scan your content folders,\n"
                 "match images/videos to the best subreddits,\n"
                 "and generate titles. Does NOT post yet.")

        self.post_btn = ctk.CTkButton(
            btn_frame, text="Post to Reddit", font=("Segoe UI", 13, "bold"),
            height=40, fg_color=SUCCESS, hover_color=SUCCESS_HOVER,
            command=self._start_posting, state="disabled"
        )
        self.post_btn.grid(row=0, column=1, sticky="ew", padx=3)
        _tooltip(self.post_btn,
                 "Post all analyzed content to Reddit.\n"
                 "Runs warmup first if configured above.\n"
                 "Only available after analysis is complete.")

        self.warmup_btn = ctk.CTkButton(
            btn_frame, text="Warmup Only", font=("Segoe UI", 13, "bold"),
            height=40, fg_color="#1E40AF", hover_color="#1E3A8A",
            command=self._start_standalone_warmup
        )
        self.warmup_btn.grid(row=0, column=2, sticky="ew", padx=3)
        _tooltip(self.warmup_btn,
                 "Run a warmup session for the selected account\n"
                 "without posting any content. The account will\n"
                 "browse, vote, and comment to build karma.")

        self.stop_btn = ctk.CTkButton(
            btn_frame, text="Stop", font=("Segoe UI", 13, "bold"),
            height=40, fg_color=WARN, hover_color=WARN_HOVER,
            command=self._stop_everything, state="disabled"
        )
        self.stop_btn.grid(row=0, column=3, sticky="ew", padx=3)
        _tooltip(self.stop_btn,
                 "Stop all running warmups and posting.\n"
                 "The current action will finish before stopping.")

        self.progress_bar = ctk.CTkProgressBar(action_card, progress_color=ACCENT)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 2))
        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(
            action_card, text="", font=("Segoe UI", 11), text_color=("#4B5563", "#94A3B8")
        )
        self.progress_label.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 5: ACTIVITY LOG (default open)
        # ═════════════════════════════════════════════════════════════
        _, log_content, _, _ = _collapsible_section(
            self.parent, "Activity Log", row=sec_row, default_open=True,
            right_widgets=[
                ("Check Performance", self._start_perf_check,
                 "#7C3AED", "#6D28D9"),
                ("Export CSV", self._export_results,
                 SECONDARY, SECONDARY_HOVER),
                ("View Activity", self._show_activity_popout,
                 "#334155", "#1F2937"),
            ])
        # Store ref for perf check button state toggling
        self._perf_check_btn = None  # buttons in collapsible header not easily referenceable

        self.log_box = ctk.CTkTextbox(
            log_content, height=300, font=("Consolas", 11),
            fg_color=("#F9FAFB", "#111827"),
            border_width=1, border_color=("#D1D5DB", "#374151"),
        )
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=(0, 8))
        log_content.grid_rowconfigure(0, weight=1)
        self.parent.grid_rowconfigure(sec_row, weight=1)
        _setup_log_tags(self.log_box)
        sec_row += 1

        # ═════════════════════════════════════════════════════════════
        # SECTION 6: PERFORMANCE (default collapsed)
        # ═════════════════════════════════════════════════════════════
        _, perf_content, _, _ = _collapsible_section(
            self.parent, "Performance", row=sec_row, default_open=False)
        perf_content.grid_columnconfigure((0, 1), weight=1)

        import tkinter.ttk as ttk
        style = ttk.Style()
        style.configure("Perf.Treeview", rowheight=22, font=("Segoe UI", 10))
        style.configure("Perf.Treeview.Heading", font=("Segoe UI", 10, "bold"))

        # Sub performance table (left)
        ctk.CTkLabel(
            perf_content, text="Subreddit Performance",
            font=("Segoe UI", 12, "bold")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))

        sub_cols = ("subreddit", "posts", "avg", "best", "removed")
        self._sub_tree = ttk.Treeview(
            perf_content, columns=sub_cols, show="headings",
            height=6, style="Perf.Treeview")
        for col, hdr, w in [
            ("subreddit", "Subreddit", 140), ("posts", "Posts", 50),
            ("avg", "Avg Score", 70), ("best", "Best", 50), ("removed", "Removed", 60),
        ]:
            self._sub_tree.heading(col, text=hdr)
            self._sub_tree.column(col, width=w, minwidth=40)
        self._sub_tree.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 8))
        self._sub_tree.bind("<Double-1>", self._on_sub_tree_dblclick)

        # Content performance table (right)
        ctk.CTkLabel(
            perf_content, text="Content Performance",
            font=("Segoe UI", 12, "bold")
        ).grid(row=0, column=1, sticky="w", padx=10, pady=(8, 2))

        content_cols = ("file", "subs", "avg", "best_sub", "best_score")
        self._content_tree = ttk.Treeview(
            perf_content, columns=content_cols, show="headings",
            height=6, style="Perf.Treeview")
        for col, hdr, w in [
            ("file", "Content File", 160), ("subs", "Subs", 40),
            ("avg", "Avg Score", 70), ("best_sub", "Best Sub", 120),
            ("best_score", "Best", 50),
        ]:
            self._content_tree.heading(col, text=hdr)
            self._content_tree.column(col, width=w, minwidth=40)
        self._content_tree.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=(0, 8))

        # Timer handle for periodic checks
        self._perf_timer = None

    def _add_campaign(self):
        """Add a new campaign row via folder picker dialog."""
        folder = filedialog.askdirectory(title="Select Content Folder")
        if not folder:
            return
        self.add_campaign_from_folder(folder)

    def add_campaign_from_folder(self, folder, preferred_profile_id=None):
        """Add a campaign row for *folder*. Called from dialog or externally (Video Converter).

        Args:
            folder: Path to content folder.
            preferred_profile_id: AdsPower profile ID to pre-select (optional).

        Returns:
            True if campaign was added, False otherwise.
        """
        files = scan_content_folder(folder)
        if not files:
            messagebox.showwarning("No Content", f"No images or videos found in:\n{folder}")
            return False

        profile_options = self._build_profile_options()
        if not self.profiles_data:
            messagebox.showwarning(
                "No Accounts",
                "No AdsPower accounts are configured yet.\nUse '+ Add Account' first.",
            )
            return False

        row_idx = len(self.campaign_widgets)
        frame = self.campaigns_frame

        # Pre-select the preferred profile if given
        default_profile = profile_options[row_idx % len(profile_options)] if profile_options else ""
        if preferred_profile_id:
            match = next(
                (opt for opt in profile_options if opt.endswith(f"({preferred_profile_id})")),
                None,
            )
            if match:
                default_profile = match

        profile_var = ctk.StringVar(value=default_profile)

        row_frame = ctk.CTkFrame(
            frame,
            fg_color=("#FFFFFF", "#111827"),
            border_width=1,
            border_color=("#D1D5DB", "#334155"),
        )
        row_frame.grid(row=row_idx, column=0, sticky="ew", pady=2, columnspan=4)
        row_frame.grid_columnconfigure(1, weight=1)

        index_label = ctk.CTkLabel(
            row_frame, text=f"#{row_idx+1}", width=30, font=("Segoe UI", 12, "bold")
        )
        index_label.grid(row=0, column=0, padx=(5, 5))

        folder_label = ctk.CTkLabel(
            row_frame, text=f"{os.path.basename(folder)} ({len(files)} files)",
            font=("Segoe UI", 12), anchor="w")
        folder_label.grid(row=0, column=1, sticky="ew", padx=5)

        profile_dropdown = ctk.CTkOptionMenu(row_frame, variable=profile_var,
                                             values=profile_options, width=220)
        profile_dropdown.grid(row=0, column=2, padx=5)

        status_label = ctk.CTkLabel(
            row_frame, text="idle", font=("Segoe UI", 11), text_color="#64748B", width=90
        )
        status_label.grid(row=0, column=3, padx=(5, 10))

        widget_info = {}
        remove_btn = ctk.CTkButton(
            row_frame,
            text="Remove",
            width=70,
            fg_color=SECONDARY,
            hover_color=SECONDARY_HOVER,
            command=lambda w=widget_info: self._remove_campaign(w),
        )
        remove_btn.grid(row=0, column=4, padx=5)

        selected = profile_var.get()
        profile_id, selected_name = self._parse_profile_selection(selected)
        profile_name = self.profiles_data.get(profile_id, selected_name)

        campaign = Campaign(folder, profile_id, profile_name)
        campaign.files = files

        widget_info.update({
            "frame": row_frame,
            "index_label": index_label,
            "folder_label": folder_label,
            "profile_var": profile_var,
            "profile_dropdown": profile_dropdown,
            "status_label": status_label,
            "remove_btn": remove_btn,
            "campaign": campaign,
        })
        profile_dropdown.configure(
            command=lambda selection, w=widget_info: self._on_campaign_profile_change(w, selection)
        )
        self.campaign_widgets.append(widget_info)
        self.campaigns.append(campaign)
        self._refresh_campaign_rows()

        self._log(f"Added campaign: {os.path.basename(folder)} -> {profile_name} ({len(files)} files)")

        # Auto-open campaigns section when first campaign added
        if len(self.campaign_widgets) == 1 and not self._camp_visible:
            self._camp_toggle()
            self._camp_visible = True
        self._camp_title.configure(
            text=f"Content Campaigns ({len(self.campaign_widgets)})")
        return True

    def _refresh_campaign_rows(self):
        """Re-index and rebind campaign row controls after list changes."""
        for idx, widgets in enumerate(self.campaign_widgets):
            widgets["frame"].grid_configure(row=idx)
            widgets["index_label"].configure(text=f"#{idx + 1}")
            widgets["remove_btn"].configure(
                command=lambda w=widgets: self._remove_campaign(w)
            )

    def _on_campaign_profile_change(self, widget_info, selected):
        """Update campaign profile binding when dropdown changes."""
        campaign = widget_info.get("campaign")
        if not campaign:
            return
        profile_id, profile_name = self._parse_profile_selection(selected)
        campaign.profile_id = profile_id
        campaign.profile_name = self.profiles_data.get(profile_id, profile_name)

    def _remove_campaign(self, campaign_ref):
        """Remove a campaign row by stable object reference."""
        remove_idx = None
        if isinstance(campaign_ref, int):
            if 0 <= campaign_ref < len(self.campaign_widgets):
                remove_idx = campaign_ref
        else:
            for idx, widgets in enumerate(self.campaign_widgets):
                if widgets is campaign_ref or widgets.get("campaign") is campaign_ref:
                    remove_idx = idx
                    break

        if remove_idx is None:
            return

        widgets = self.campaign_widgets.pop(remove_idx)
        try:
            widgets["frame"].destroy()
        except Exception:
            pass

        if remove_idx < len(self.campaigns):
            self.campaigns.pop(remove_idx)

        self._refresh_campaign_rows()

        # Auto-collapse if all campaigns removed
        if not self.campaign_widgets and self._camp_visible:
            self._camp_toggle()
            self._camp_visible = False
        if self.campaign_widgets:
            self._camp_title.configure(
                text=f"Content Campaigns ({len(self.campaign_widgets)})")
        else:
            self._camp_title.configure(text="Content Campaigns")

    def _log(self, msg):
        """Append to the log box with color coding."""
        tag = _detect_log_tag(msg)
        if tag:
            self.log_box.insert("end", f"{msg}\n", tag)
        else:
            self.log_box.insert("end", f"{msg}\n")
        _apply_link_tags(self.log_box, msg)
        self.log_box.see("end")

    def _update_campaign_status(self, index, status, color="gray"):
        """Update a campaign's status label and refresh account status tags."""
        if index < len(self.campaign_widgets):
            self.campaign_widgets[index]["status_label"].configure(
                text=status, text_color=color)
            # Also update the campaign object status for account status tracking
            if index < len(self.campaigns):
                self.campaigns[index].status = status
            self._refresh_account_statuses()

    # ===================== ANALYSIS =====================

    def _start_analysis(self):
        """Analyze all campaigns."""
        if self.is_running:
            return

        if not self.campaigns:
            messagebox.showerror("Error", "Add at least one campaign folder first.")
            return

        claude_key, grok_key = self._resolve_api_keys()
        if not claude_key:
            messagebox.showerror(
                "Missing Claude Key",
                "Claude key not found.\n"
                "Set `claude_api_key` in config/api_keys.json "
                "or set ANTHROPIC_API_KEY.",
            )
            return
        if not grok_key:
            messagebox.showerror(
                "Missing Grok Key",
                "Grok key not found.\n"
                "Set `grok_api_key` in config/api_keys.json "
                "or set GROK_API_KEY.",
            )
            return

        self.analyze_btn.configure(state="disabled", text="Analyzing...")
        self.is_running = True

        thread = threading.Thread(target=self._analysis_worker,
                                  args=(claude_key, grok_key), daemon=True)
        thread.start()

    def _analysis_worker(self, claude_key, grok_key):
        """Background: analyze all campaigns then generate titles."""
        error_msg = None
        try:
            total_files = sum(len(c.files) for c in self.campaigns)
            processed = 0

            try:
                subs_per = int(self.subs_per_file.get())
            except ValueError:
                subs_per = 8
            try:
                max_subs = int(self.max_subs_entry.get())
            except ValueError:
                max_subs = 100000
            try:
                min_subs = int(self.min_subs_entry.get())
            except ValueError:
                min_subs = 2500
            try:
                min_score = int(self.min_score_entry.get())
            except ValueError:
                min_score = 20

            # R4R settings
            r4r_mode = self.r4r_mode_var.get().lower().replace(" ", "_")  # off/trickle/area_focus/blast
            try:
                r4r_count = int(self.r4r_count_entry.get())
            except (ValueError, AttributeError):
                r4r_count = 2
            campaign_context = (self.campaign_context_entry.get() or "").strip() or None

            for camp_idx, campaign in enumerate(self.campaigns):
                self.app.after(0, self._update_campaign_status, camp_idx, "analyzing", "yellow")
                campaign.status = "analyzing"
                campaign.posting_plan = []
                selected_subs_seen = set()

                # Re-read profile_id from dropdown (user might have changed it)
                selected = self.campaign_widgets[camp_idx]["profile_var"].get()
                campaign.profile_id, selected_name = self._parse_profile_selection(selected)
                campaign.profile_name = self.profiles_data.get(campaign.profile_id, selected_name)

                # Get exclusions for this profile
                banned = get_banned_subs(campaign.profile_id)

                for file_path in campaign.files:
                    fname = os.path.basename(file_path)
                    processed += 1
                    self.app.after(0, lambda p=processed, t=total_files, n=fname:
                        self._update_progress(p, t, f"Analyzing: {n}"))

                    try:
                        # Vision analysis
                        vision = analyze_image(file_path, claude_key)
                        if not vision:
                            self.app.after(0, self._log, f"  SKIP {fname}: vision analysis failed")
                            continue
                        campaign.analysis[file_path] = vision

                        # Get already-posted subs for this content
                        file_hash = content_file_hash(file_path)
                        posted = get_posted_subs(file_hash)
                        excluded = banned | posted | selected_subs_seen

                        # Match + random select
                        all_matches = match_content(
                            vision,
                            self.sub_profiles,
                            self.sub_tiers,
                            excluded_subs=excluded,
                            sub_data=self.sub_data,
                            max_subscribers=max_subs,
                            min_subscribers=min_subs,
                            exclude_low_quality_subs=True,
                            exclude_strict_new_account_subs=True,
                            exclude_high_risk_niche_subs=True,
                        )
                        selected_subs = random_select_subs(
                            all_matches,
                            count=subs_per,
                            min_score=min_score,
                        )
                        # Fallback: if strict de-dupe exhausted choices, allow previously seen subs.
                        if not selected_subs and selected_subs_seen:
                            all_matches = match_content(
                                vision,
                                self.sub_profiles,
                                self.sub_tiers,
                                excluded_subs=banned | posted,
                                sub_data=self.sub_data,
                                max_subscribers=max_subs,
                                min_subscribers=min_subs,
                                exclude_low_quality_subs=True,
                                exclude_strict_new_account_subs=True,
                                exclude_high_risk_niche_subs=True,
                            )
                            selected_subs = random_select_subs(
                                all_matches,
                                count=subs_per,
                                min_score=min_score,
                            )

                        # Build pairing data for title generation
                        for sub_name, score, theme, tags in selected_subs:
                            campaign.posting_plan.append({
                                "file_path": file_path,
                                "file_hash": file_hash,
                                "file_name": fname,
                                "sub_name": sub_name,
                                "score": score,
                                "sub_theme": theme,
                                "content_tags": vision.get("tags", []),
                                "body_type": vision.get("body_type", ""),
                                "action": vision.get("action", ""),
                                "setting": vision.get("setting", ""),
                                "title": "",  # filled by title gen
                                "selected": True,  # for manual review
                            })
                            selected_subs_seen.add(sub_name)

                        tags_str = ", ".join(vision.get("tags", [])[:5])
                        r4r_msg = ""

                        # R4R sub selection (parallel to normal subs)
                        if r4r_mode != "off":
                            # Determine location: context box → profile persona
                            r4r_location = ""
                            if campaign_context:
                                # Try to extract a city/state from the context
                                r4r_location = campaign_context
                            if not r4r_location or r4r_mode != "area_focus":
                                # Fall back to profile persona location
                                persona_key = campaign.profile_name.lower().replace(" ", "_") if campaign.profile_name else ""
                                for pkey, pdata in self.persona_profiles.items():
                                    if pkey == persona_key or pkey == campaign.profile_id:
                                        persona_loc = (pdata.get("persona") or {}).get("location", "")
                                        if persona_loc:
                                            r4r_location = persona_loc
                                        break

                            if r4r_location:
                                r4r_subs = select_r4r_subs(
                                    r4r_location, self.sub_profiles, self.sub_tiers,
                                    excluded_subs=excluded,
                                    sub_data=self.sub_data,
                                    count=r4r_count,
                                    mode=r4r_mode,
                                )
                                for sub_name, score, theme, tag_overlap in r4r_subs:
                                    campaign.posting_plan.append({
                                        "file_path": file_path,
                                        "file_hash": file_hash,
                                        "file_name": fname,
                                        "sub_name": sub_name,
                                        "score": score,
                                        "sub_theme": theme,
                                        "content_tags": vision.get("tags", []),
                                        "body_type": vision.get("body_type", ""),
                                        "action": vision.get("action", ""),
                                        "setting": vision.get("setting", ""),
                                        "title": "",
                                        "selected": True,
                                        "is_r4r": True,
                                    })
                                    selected_subs_seen.add(sub_name)
                                r4r_msg = f" + {len(r4r_subs)} r4r"

                        self.app.after(0, self._log,
                            f"  {fname}: {tags_str} -> {len(selected_subs)} subs{r4r_msg}")

                    except Exception as e:
                        self.app.after(0, self._log, f"  ERROR {fname}: {e}")

                # Generate titles for this campaign's plan
                if campaign.posting_plan:
                    self.app.after(0, self._log,
                        f"\nGenerating titles for {len(campaign.posting_plan)} posts...")
                    self.app.after(0, lambda p=processed, t=total_files:
                        self._update_progress(p, t, "Generating titles..."))

                    # Split into normal vs r4r for different title styles
                    normal_indices = [i for i, p in enumerate(campaign.posting_plan) if not p.get("is_r4r")]
                    r4r_indices = [i for i, p in enumerate(campaign.posting_plan) if p.get("is_r4r")]

                    # Normal titles
                    if normal_indices:
                        normal_pairings = [{
                            "sub_name": campaign.posting_plan[i]["sub_name"],
                            "sub_theme": campaign.posting_plan[i]["sub_theme"],
                            "content_tags": campaign.posting_plan[i]["content_tags"],
                            "body_type": campaign.posting_plan[i]["body_type"],
                            "action": campaign.posting_plan[i]["action"],
                            "setting": campaign.posting_plan[i]["setting"],
                        } for i in normal_indices]
                        normal_titles = generate_titles_batch(
                            normal_pairings, grok_key, campaign_context=campaign_context)
                        for j, idx in enumerate(normal_indices):
                            if j < len(normal_titles) and normal_titles[j]:
                                campaign.posting_plan[idx]["title"] = normal_titles[j]

                    # R4R titles
                    if r4r_indices:
                        r4r_pairings = [{
                            "sub_name": campaign.posting_plan[i]["sub_name"],
                            "sub_theme": campaign.posting_plan[i]["sub_theme"],
                        } for i in r4r_indices]
                        # Build persona info from profile
                        persona_info = {"age": 25, "gender": "F", "location": ""}
                        persona_key = campaign.profile_name.lower().replace(" ", "_") if campaign.profile_name else ""
                        for pkey, pdata in self.persona_profiles.items():
                            if pkey == persona_key or pkey == campaign.profile_id:
                                attrs = pdata.get("attributes", {})
                                persona_info["age"] = attrs.get("age", 25)
                                persona_info["gender"] = attrs.get("gender", "F")
                                persona_info["location"] = (pdata.get("persona") or {}).get("location", "")
                                break
                        if campaign_context:
                            persona_info["context"] = campaign_context
                        r4r_titles = generate_r4r_titles(r4r_pairings, grok_key, persona_info)
                        for j, idx in enumerate(r4r_indices):
                            if j < len(r4r_titles) and r4r_titles[j]:
                                campaign.posting_plan[idx]["title"] = r4r_titles[j]

                    # Fill any None titles with a generic fallback
                    for item in campaign.posting_plan:
                        if not item["title"]:
                            if item.get("is_r4r"):
                                item["title"] = f"[25F] looking for fun tonight"
                            else:
                                action = (item.get("action") or "posing").replace("_", " ")
                                setting = (item.get("setting") or "here").replace("_", " ")
                                tags = [t.replace("_", " ") for t in item.get("content_tags", [])[:2]]
                                if tags:
                                    item["title"] = f"{action} in {setting} with {', '.join(tags)}"
                                else:
                                    item["title"] = f"{action} in {setting} tonight"

                # Auto-detect physical attributes from vision results
                # Aggregate across all analyzed files — majority wins
                if campaign.analysis:
                    self._auto_update_profile_attributes(campaign)

                campaign.status = "ready"
                self.app.after(0, self._update_campaign_status, camp_idx, "ready", "green")
                self.app.after(0, self._log,
                    f"Campaign {camp_idx+1} ready: {len(campaign.posting_plan)} posts planned\n")
        except Exception as e:
            error_msg = str(e)
            logger.exception("Analysis worker failed")
            self.app.after(0, self._log, f"ANALYSIS FAILED: {e}")
        finally:
            self.app.after(0, self._analysis_complete, error_msg)

    def _auto_update_profile_attributes(self, campaign):
        """Auto-detect physical attributes + content tags from vision results
        and update the account profile. Majority vote across all analyzed files."""
        from collections import Counter

        body_types = Counter()
        ethnicities = Counter()
        hair_colors = Counter()
        all_tags = Counter()

        for file_path, vision in campaign.analysis.items():
            bt = (vision.get("body_type") or "").lower().strip()
            eth = (vision.get("ethnicity") or "").lower().strip()
            hair = (vision.get("hair_color") or "").lower().strip()
            if bt:
                body_types[bt] += 1
            if eth:
                ethnicities[eth] += 1
            if hair:
                hair_colors[hair] += 1
            for tag in vision.get("tags", []):
                t = tag.lower().strip()
                if t:
                    all_tags[t] += 1

        if not body_types and not ethnicities:
            return  # nothing detected

        # Find the persona profile for this campaign's account
        try:
            manager = ProfileManager(ACCOUNT_PROFILES_PATH)
            profile = None
            for p in manager.get_all_profiles():
                if p.adspower_id == campaign.profile_id:
                    profile = p
                    break
            if not profile:
                return

            # Update attributes with most common values
            if body_types:
                profile.attributes.body_type = body_types.most_common(1)[0][0]
            if ethnicities:
                profile.attributes.ethnicity = ethnicities.most_common(1)[0][0]
            if hair_colors:
                profile.attributes.hair_color = hair_colors.most_common(1)[0][0]

            # Build content tags from top detected tags
            top_tags = [tag for tag, _ in all_tags.most_common(10)]
            if top_tags:
                profile.content_tags = top_tags

            manager.add_profile(profile)
            self.app.after(0, self._log,
                f"  Auto-detected: {profile.attributes.body_type}, "
                f"{profile.attributes.ethnicity}, {profile.attributes.hair_color} "
                f"| tags: {', '.join(top_tags[:5])}")
            self._load_persona_profiles()
        except Exception as e:
            logger.debug(f"Auto-update profile failed: {e}")

    def _analysis_complete(self, error_msg=None):
        """Called when all analysis is done."""
        self.is_running = False
        self.analyze_btn.configure(state="normal", text="Analyze Content")
        if error_msg:
            self.post_btn.configure(state="disabled")
            self.progress_label.configure(text="Analysis failed")
            return

        self.progress_bar.set(1.0)
        total_posts = sum(len(c.posting_plan) for c in self.campaigns)
        self.progress_label.configure(text=f"Analysis complete: {total_posts} total posts planned")

        if total_posts > 0:
            self.post_btn.configure(state="normal")
            # Show posting plan in log
            if not self.auto_mode_var.get():
                self._show_posting_plan()
        else:
            self.post_btn.configure(state="disabled")

    def _show_posting_plan(self):
        """Display the posting plan for manual review."""
        self._log("\n" + "=" * 60)
        self._log("POSTING PLAN - Review below, then click 'Start Posting'")
        self._log("=" * 60)

        for camp_idx, campaign in enumerate(self.campaigns):
            self._log(f"\n--- Campaign {camp_idx+1}: {campaign.profile_name} ---")
            current_file = ""
            for item in campaign.posting_plan:
                if item["file_name"] != current_file:
                    current_file = item["file_name"]
                    self._log(f"\n  {current_file}:")
                self._log(f"    r/{item['sub_name']} (score:{item['score']:.0f}) - \"{item['title']}\"")

        self._log(f"\n{'=' * 60}")
        total = sum(len(c.posting_plan) for c in self.campaigns)
        self._log(f"Total: {total} posts across {len(self.campaigns)} campaign(s)")
        self._log("Click 'Start Posting' to begin, or re-analyze to change.\n")

    def _update_progress(self, current, total, msg=""):
        """Update progress bar and label."""
        if total > 0:
            self.progress_bar.set(current / total)
        self.progress_label.configure(text=f"[{current}/{total}] {msg}")

    # ===================== POSTING =====================

    def _start_posting(self):
        """Start posting across all campaigns in parallel."""
        if self.is_running:
            return

        # Validate no proxy group conflicts between campaigns
        proxy_usage = {}  # proxy_group -> campaign profile_name
        for campaign in self.campaigns:
            if not campaign.posting_plan:
                continue
            pg = self._get_account_proxy_group(campaign.profile_id)
            if pg and pg in proxy_usage:
                messagebox.showerror(
                    "Proxy Conflict",
                    f"Cannot run campaigns on the same proxy simultaneously.\n\n"
                    f"{proxy_usage[pg]} and {campaign.profile_name} both use {pg}.\n"
                    f"Move one to a different proxy group first.")
                return
            if pg:
                proxy_usage[pg] = campaign.profile_name

        self.is_running = True
        self.stop_all = False
        self.post_btn.configure(state="disabled")
        self.analyze_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        # Launch one thread per campaign
        threads = []
        for camp_idx, campaign in enumerate(self.campaigns):
            if not campaign.posting_plan:
                continue
            campaign.stop_requested = False
            t = threading.Thread(
                target=self._campaign_posting_worker,
                args=(camp_idx, campaign),
                daemon=True
            )
            threads.append(t)
            t.start()

        # Monitor thread that waits for all campaign threads to finish
        def monitor():
            for t in threads:
                t.join()
            self.app.after(0, self._posting_complete)

        threading.Thread(target=monitor, daemon=True).start()

    def _wait_with_stop(self, campaign, seconds, label):
        """Interruptible sleep that reacts quickly to stop requests."""
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if self.stop_all or campaign.stop_requested:
                self.app.after(0, self._log,
                    f"[Campaign] Stop requested during {label}, skipping wait")
                return False
            time.sleep(min(0.5, max(0.0, end - time.time())))
        return True

    def _campaign_posting_worker(self, camp_idx, campaign):
        """Post all items for one campaign (one AdsPower profile). Runs in its own thread."""
        import requests as req

        self.app.after(0, self._update_campaign_status, camp_idx, "connecting", "yellow")
        self.app.after(0, self._log,
            f"\n[Campaign {camp_idx+1}] Starting profile: {campaign.profile_name}")

        # Proxy rotation — acquire proxy group and rotate before browser start
        proxy_group = self._get_account_proxy_group(campaign.profile_id)
        if proxy_group:
            if not self._acquire_proxy_group(proxy_group, campaign.profile_id):
                current = self._active_proxy_groups.get(proxy_group, "?")
                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] BLOCKED: {proxy_group} in use by {current}")
                self.app.after(0, self._update_campaign_status,
                    camp_idx, "proxy busy", "orange")
                return
            self._rotate_proxy(proxy_group)

        # Start AdsPower browser
        api_base = self.adspower_config.get("adspower_api_base", "http://127.0.0.1:50325")
        api_key = self.adspower_config.get("api_key", "")

        try:
            start_url = (f"{api_base}/api/v1/browser/start"
                        f"?user_id={campaign.profile_id}&api_key={api_key}")
            resp = req.get(start_url, timeout=60)
            data = resp.json()
            if data.get("code") != 0:
                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] ERROR: Failed to start browser: {data}")
                self.app.after(0, self._update_campaign_status, camp_idx, "error", "red")
                return
            ws_endpoint = data["data"]["ws"]["puppeteer"]
        except Exception as e:
            self.app.after(0, self._log,
                f"[Campaign {camp_idx+1}] ERROR: AdsPower connection failed: {e}")
            self.app.after(0, self._update_campaign_status, camp_idx, "error", "red")
            return

        # Connect Playwright
        try:
            from playwright.sync_api import sync_playwright
            from uploaders.reddit.reddit_poster_playwright import post_file_to_subreddit

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(ws_endpoint)
                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    pages = context.pages
                    page = pages[0] if pages else context.new_page()
                else:
                    context = browser.new_context()
                    page = context.new_page()

                # Setup humanizer
                try:
                    daily_limit = int(self.daily_limit_entry.get())
                except ValueError:
                    daily_limit = 8

                humanizer = Humanizer(page, {"daily_limit": daily_limit})

                # Check account health
                status, detail = check_account_health(page)
                if status == BanStatus.ACCOUNT_SUSPENDED:
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] ACCOUNT SUSPENDED: {detail}")
                    self.app.after(0, self._update_campaign_status,
                        camp_idx, "suspended", "red")
                    return

                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] Account OK: {detail}")

                # Account warmup - persona-driven activities based on account age
                # Try to find persona data for this profile
                persona = None
                profile_attributes = None
                account_age_days = None
                account_created_at = None
                try:
                    pm = ProfileManager()
                    for ap in pm.get_all_profiles():
                        if (ap.reddit_account.username.lower() ==
                                campaign.profile_name.lower()):
                            persona = ap.persona
                            profile_attributes = getattr(ap, "attributes", None)
                            account_created_at = getattr(ap, "created_at", None)
                            try:
                                raw_age = getattr(ap.reddit_account, "age_days", None)
                                if raw_age is not None:
                                    account_age_days = max(0, int(raw_age))
                            except Exception:
                                account_age_days = None

                            if account_age_days is None and account_created_at:
                                try:
                                    dt = datetime.fromisoformat(str(account_created_at).replace("Z", "+00:00"))
                                    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                                    account_age_days = max(0, (now - dt).days)
                                except Exception:
                                    account_age_days = None

                            self.app.after(0, self._log,
                                f"[Campaign {camp_idx+1}] Persona loaded: "
                                f"{ap.display_name} ({len(persona.favorite_subs)} "
                                f"fav subs, {len(persona.hobbies)} hobbies)")
                            break
                except Exception as e:
                    logger.debug(f"Persona lookup failed (using defaults): {e}")

                grok_key = self.grok_key_entry.get().strip()
                warmup_mode = self.warmup_mode_var.get()  # "none", "interleaved", "full"

                # Browse duration per session (user-configurable, seconds range)
                try:
                    browse_lo = max(10, int(self.browse_min_entry.get()))
                except ValueError:
                    browse_lo = 80
                try:
                    browse_hi = max(browse_lo, int(self.browse_max_entry.get()))
                except ValueError:
                    browse_hi = 150

                warmer = AccountWarmer(
                    campaign.profile_id, page,
                    persona=persona,
                    attributes=profile_attributes,
                    grok_api_key=grok_key,
                    account_age_days=account_age_days,
                    account_created_at=account_created_at,
                )
                warmer.hijack_ratio = self.hijack_slider.get() / 100.0
                self._apply_warmup_overrides(warmer)
                campaign.warmer = warmer
                warmup_day = warmer.get_day()

                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] Warmup day {warmup_day}, "
                    f"mode: {warmup_mode}, browse: {browse_lo}-{browse_hi}s, "
                    f"hijack: {int(warmer.hijack_ratio * 100)}%")

                if warmup_mode == "full":
                    # Full day-scaled browse sessions (8-26 min on day 3)
                    target_subs = [item["sub_name"] for item in campaign.posting_plan]
                    warmup_results = warmer.run_daily_warmup(target_subs=target_subs)
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] Warmup done: "
                        f"{warmup_results.get('sessions', 0)} sessions, "
                        f"{warmup_results.get('total_sec', 0)//60}min, "
                        f"votes={warmup_results.get('upvotes', 0)}up/"
                        f"{warmup_results.get('downvotes', 0)}down, "
                        f"comments={warmup_results.get('comments', 0)}")

                    # CQS check after campaign warmup (if enabled)
                    if self.adv_cqs_var.get():
                        try:
                            cqs = warmer.check_cqs()
                            if cqs is not None:
                                self.app.after(0, self._log,
                                    f"[Campaign {camp_idx+1}] CQS = {cqs}")
                        except Exception as cqs_err:
                            self.app.after(0, self._log,
                                f"[Campaign {camp_idx+1}] CQS check error: {cqs_err}")

                    if not warmer.should_post_today():
                        self.app.after(0, self._log,
                            f"[Campaign {camp_idx+1}] Day {warmup_day}: "
                            f"browse-only phase, no posting yet")
                        self.app.after(0, self._update_campaign_status,
                            camp_idx, "warmup", "orange")
                        return

                elif warmup_mode == "none":
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] Post-only mode, skipping warmup")

                # Daily limit from GUI overrides warmup cap
                max_posts = daily_limit

                # Cap posting plan
                if max_posts < len(campaign.posting_plan):
                    campaign.posting_plan = campaign.posting_plan[:max_posts]
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] Capped to {max_posts} posts "
                        f"(daily limit)")

                # Post each item
                self.app.after(0, self._update_campaign_status,
                    camp_idx, "posting", "yellow")

                posts_today = get_posts_today(campaign.profile_id)
                success = 0
                failed = 0
                banned = 0

                for i, item in enumerate(campaign.posting_plan):
                    if self.stop_all or campaign.stop_requested:
                        self.app.after(0, self._log,
                            f"[Campaign {camp_idx+1}] Stopped by user")
                        break

                    if not item.get("selected", True):
                        continue

                    # Check daily limit
                    if posts_today >= daily_limit:
                        self.app.after(0, self._log,
                            f"[Campaign {camp_idx+1}] Daily limit reached ({posts_today})")
                        break

                    # -- INTERLEAVED: browse session before each post --
                    if warmup_mode == "interleaved":
                        browse_sec = random.randint(browse_lo, browse_hi)
                        self.app.after(0, self._log,
                            f"[Campaign {camp_idx+1}] Browsing ~{browse_sec}s before post {i+1}...")
                        warmer._run_browse_session(session_sec=browse_sec)

                    sub = item["sub_name"]
                    title = item["title"]
                    file_path = item["file_path"]

                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] [{i+1}/{len(campaign.posting_plan)}] "
                        f"r/{sub}: \"{title[:40]}...\"")

                    # Spoof the file if enabled
                    spoofed_path = None
                    upload_path = file_path
                    if self.spoof_var.get():
                        try:
                            spoofed_path, spoof_result = spoof_file(file_path)
                            if spoofed_path:
                                upload_path = spoofed_path
                                self.app.after(0, self._log,
                                    f"  Spoofed: {', '.join(spoof_result.get('applied', []))}")
                            else:
                                self.app.after(0, self._log,
                                    f"  Spoof failed: {spoof_result.get('error', 'unknown')} - using original")
                        except Exception as e:
                            self.app.after(0, self._log,
                                f"  Spoof error: {e} - using original")

                    # Pre-post browsing (light page-level scroll)
                    if warmup_mode != "none":
                        humanizer.pre_post_browse(sub)

                    # Post the file
                    try:
                        ok = post_file_to_subreddit(
                            page=page,
                            subreddit=sub,
                            title=title,
                            file_path=upload_path,
                            mark_nsfw=True,
                            humanizer=humanizer,
                        )

                        # Check result
                        post_status, detail = check_post_result(page)

                        if post_status == BanStatus.OK:
                            success += 1
                            posts_today += 1
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "success", detail)
                            self.app.after(0, self._log,
                                f"  SUCCESS -> {detail}")

                        elif post_status == BanStatus.SUB_BANNED:
                            banned += 1
                            from core.post_history import add_ban
                            add_ban(campaign.profile_id, sub, detail)
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "banned", error=detail)
                            self.app.after(0, self._log,
                                f"  BANNED from r/{sub}: {detail}")

                        elif post_status == BanStatus.RATE_LIMITED:
                            self.app.after(0, self._log,
                                f"  RATE LIMITED: {detail}. Waiting 5 minutes...")
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "rate_limited", error=detail)
                            if not self._wait_with_stop(campaign, 300, "rate-limit backoff"):
                                break

                        else:
                            failed += 1
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "failed", error=detail)
                            self.app.after(0, self._log,
                                f"  FAILED: {detail}")

                    except Exception as e:
                        failed += 1
                        add_post(campaign.profile_id, item["file_hash"],
                                sub, title, file_path, "failed", error=str(e))
                        self.app.after(0, self._log,
                            f"  ERROR: {e}")
                    finally:
                        # Clean up spoofed file after posting
                        if spoofed_path and os.path.exists(spoofed_path):
                            try:
                                os.remove(spoofed_path)
                            except OSError:
                                pass

                    # Humanized wait between posts
                    if i < len(campaign.posting_plan) - 1:
                        kept_waiting = humanizer.wait_between_posts(
                            stop_checker=lambda: self.stop_all or campaign.stop_requested
                        )
                        if not kept_waiting:
                            self.app.after(0, self._log,
                                f"[Campaign {camp_idx+1}] Stopped during inter-post wait")
                            break

                # Record warmup stats to DB (interleaved accumulates across posts)
                if warmup_mode == "interleaved":
                    record_activity(warmer.profile_id, "upvotes", warmer.stats.get("upvotes", 0))
                    record_activity(warmer.profile_id, "comments", warmer.stats.get("comments", 0))
                    record_activity(warmer.profile_id, "joins", warmer.stats.get("joins", 0))

                # Done with this campaign
                campaign.status = "done"
                self.app.after(0, self._update_campaign_status,
                    camp_idx, f"done ({success}ok)", "green")
                self.app.after(0, self._log,
                    f"\n[Campaign {camp_idx+1}] DONE: {success} success, "
                    f"{failed} failed, {banned} banned\n")

        except Exception as e:
            self.app.after(0, self._log,
                f"[Campaign {camp_idx+1}] FATAL ERROR: {e}")
            self.app.after(0, self._update_campaign_status,
                camp_idx, "error", "red")

        finally:
            campaign.warmer = None
            # Release proxy group
            if proxy_group:
                self._release_proxy_group(proxy_group, campaign.profile_id)
            # Stop the browser profile
            try:
                stop_url = (f"{api_base}/api/v1/browser/stop"
                           f"?user_id={campaign.profile_id}&api_key={api_key}")
                req.get(stop_url, timeout=10)
            except:
                pass

    def _posting_complete(self):
        """Called when all campaign threads finish."""
        self.is_running = False
        self.stop_all = False
        self.post_btn.configure(state="normal")
        self.analyze_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress_bar.set(1.0)
        self.progress_label.configure(text="All campaigns complete")
        self._log("\n" + "=" * 60)
        self._log("ALL CAMPAIGNS COMPLETE")
        self._log("=" * 60)
        # Schedule a delayed performance check (15 min) so Reddit scores settle
        self._log("Performance check scheduled in 15 minutes...")
        self._perf_delayed_timer = self.app.after(
            15 * 60 * 1000, self._start_perf_check)
        # Also start the periodic 30-min timer
        self._start_perf_timer()
        self._refresh_account_statuses()

    def _stop_all(self):
        """Legacy alias — delegates to _stop_everything."""
        self._stop_everything()

    def _export_results(self):
        """Export all post results to CSV."""
        output_path = filedialog.asksaveasfilename(
            title="Export Results", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")])
        if output_path:
            count = export_results_csv(output_path)
            messagebox.showinfo("Exported", f"Exported {count} results to:\n{output_path}")

    # ===================== PERFORMANCE TRACKING =====================

    def _get_proxy_string(self):
        """Load proxy string from accounts.json for API calls."""
        try:
            accts_path = os.path.join(
                os.path.dirname(__file__), "..", "uploaders", "redgifs", "accounts.json")
            if os.path.exists(accts_path):
                with open(accts_path, encoding="utf-8") as f:
                    data = json.load(f)
                for acct in data.get("accounts", []):
                    proxy = acct.get("proxy")
                    if proxy:
                        return proxy
        except Exception:
            pass
        return None

    def _start_perf_check(self):
        """Launch a background performance check."""
        if self._perf_check_btn:
            self._perf_check_btn.configure(state="disabled", text="Checking...")
        threading.Thread(target=self._perf_check_worker, daemon=True).start()

    def _perf_check_worker(self):
        """Background worker: fetch Reddit scores and update tables."""
        try:
            profile_id = self.profile_entry.get().strip() or None
            proxy = self._get_proxy_string()
            summary = run_check_cycle(profile_id=profile_id, proxy=proxy)
            checked = summary.get("checked", 0)
            removed = summary.get("removed", 0)
            avg = summary.get("avg_score", 0)

            if checked > 0:
                self.app.after(0, self._log,
                    f"Performance check: {checked} posts checked, "
                    f"avg score {avg}, {removed} removed")
            else:
                self.app.after(0, self._log,
                    "Performance check: no posts to check")

            self.app.after(0, self._refresh_perf_tables)
        except Exception as e:
            self.app.after(0, self._log, f"Performance check error: {e}")
        finally:
            if self._perf_check_btn:
                self.app.after(0, lambda: self._perf_check_btn.configure(
                    state="normal", text="Check Performance"))

    def _refresh_perf_tables(self):
        """Refresh both performance treeviews from the database."""
        profile_id = self.profile_entry.get().strip() or None

        # Sub performance table
        self._sub_tree.delete(*self._sub_tree.get_children())
        for row in get_sub_performance(profile_id):
            self._sub_tree.insert("", "end", values=(
                f"r/{row['subreddit']}",
                row["post_count"],
                row["avg_score"],
                row["max_score"] or 0,
                row["removed_count"] or 0,
            ))

        # Content performance table
        self._content_tree.delete(*self._content_tree.get_children())
        for row in get_content_performance(profile_id):
            filename = os.path.basename(row["content_file"] or "unknown")
            self._content_tree.insert("", "end", values=(
                filename,
                row["sub_count"],
                row["avg_score"],
                f"r/{row['best_sub']}" if row.get("best_sub") else "",
                row["best_score"] or 0,
            ))

    def _on_sub_tree_dblclick(self, event):
        """Open subreddit in browser on double-click."""
        sel = self._sub_tree.selection()
        if sel:
            values = self._sub_tree.item(sel[0], "values")
            if values:
                sub = values[0].replace("r/", "")
                webbrowser.open(f"https://www.reddit.com/r/{sub}")

    def _start_perf_timer(self):
        """Start a 30-minute repeating timer for periodic performance checks."""
        if self._perf_timer is not None:
            self.app.after_cancel(self._perf_timer)
        self._perf_timer = self.app.after(30 * 60 * 1000, self._perf_timer_tick)

    def _perf_timer_tick(self):
        """Periodic timer callback — runs check then reschedules."""
        self._log("Auto-checking post performance...")
        threading.Thread(target=self._perf_check_worker, daemon=True).start()
        self._perf_timer = self.app.after(30 * 60 * 1000, self._perf_timer_tick)

    # ===================== ACCOUNT CHANGE =====================

    def _on_account_change(self, selection):
        """Handle account change — delegates to table row selection."""
        self._select_account_row(selection)

    def _refresh_life_stats(self, silent=False):
        """Read warmup stats from DB and update the lifetime stats labels."""
        profile_id = self.profile_entry.get().strip()
        if not profile_id:
            return

        try:
            status = get_warmup_status(profile_id)
        except Exception as e:
            logger.error(f"Failed to load warmup stats: {e}")
            status = None

        if not status:
            self.day_label.configure(text="Day --")
            self.life_upvotes.configure(text="0 votes")
            self.life_comments.configure(text="0 comments")
            self.life_joins.configure(text="0 joins")
            self.life_posts.configure(text="0 posts")
            return

        day = get_warmup_day(profile_id)
        self.day_label.configure(text=f"Day {day}")
        self.life_upvotes.configure(text=f"{status.get('total_upvotes', 0)} votes")
        self.life_comments.configure(text=f"{status.get('total_comments', 0)} comments")
        self.life_joins.configure(text=f"{status.get('total_joins', 0)} joins")
        self.life_posts.configure(text=f"{status.get('total_posts', 0)} posts")

    # ===================== STANDALONE WARMUP =====================

    def _apply_warmup_overrides(self, warmer):
        """Apply GUI advanced settings overrides to an AccountWarmer instance."""
        for field, key in [("adv_max_comments", "comments"),
                           ("adv_max_votes", "votes"),
                           ("adv_max_joins", "joins")]:
            try:
                val = getattr(self, field).get().strip()
                if val:
                    warmer._daily_caps[key] = int(val)
            except (ValueError, AttributeError):
                pass
        try:
            nsfw_val = self.adv_min_nsfw_days.get().strip()
            if nsfw_val:
                warmer.min_nsfw_days = int(nsfw_val)
        except (ValueError, AttributeError):
            pass

    def _start_standalone_warmup(self):
        """Start a warmup-only session (no posting). Supports concurrent warmups."""
        profile_id = self.profile_entry.get().strip()
        if not profile_id:
            messagebox.showerror("Error", "Select a Reddit account first.")
            return

        if profile_id in self._active_warmups:
            messagebox.showwarning("Already Running",
                                   f"Warmup already running for {profile_id}.")
            return

        grok_key = self.grok_key_entry.get().strip()

        # Resolve persona from account selection
        persona = None
        profile_attributes = None
        account_age_days = None
        account_created_at = None
        persona_key = self.persona_var.get()
        if persona_key and persona_key in self.persona_profiles:
            profile_data = self.persona_profiles[persona_key]
            persona = profile_data.get("persona")
            profile_attributes = profile_data.get("attributes")
            account_created_at = profile_data.get("created_at")
            try:
                raw_age = (profile_data.get("reddit_account", {}) or {}).get("age_days")
                if raw_age is not None:
                    account_age_days = max(0, int(raw_age))
            except Exception:
                account_age_days = None

            if account_age_days is None and account_created_at:
                try:
                    dt = datetime.fromisoformat(
                        str(account_created_at).replace("Z", "+00:00"))
                    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                    account_age_days = max(0, (now - dt).days)
                except Exception:
                    account_age_days = None

        # Track this warmup — don't disable the button
        self._active_warmups[profile_id] = {"warmer": None, "stop": False}
        n = len(self._active_warmups)
        self.warmup_btn.configure(text=f"Warmup ({n} running)")
        self.stop_btn.configure(state="normal")
        self._refresh_account_statuses()

        # Get display name for log prefix
        display_name = profile_id
        if persona_key and persona_key in self.persona_profiles:
            display_name = self.persona_profiles[persona_key].get(
                "display_name", profile_id)

        self._log(f"=== Starting warmup: {display_name} ({profile_id}) ===")

        thread = threading.Thread(
            target=self._standalone_warmup_worker,
            args=(profile_id, persona, profile_attributes, grok_key,
                  account_age_days, account_created_at),
            daemon=True,
        )
        thread.start()

    def _standalone_warmup_worker(self, profile_id, persona, profile_attributes,
                                  grok_key, account_age_days=None,
                                  account_created_at=None):
        """Background thread: connect browser, run warmup, report results."""
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        stats = None
        browser_started = False
        entry = self._active_warmups.get(profile_id, {})

        # Proxy rotation before starting browser
        proxy_group = self._get_account_proxy_group(profile_id)
        if proxy_group:
            if not self._acquire_proxy_group(proxy_group, profile_id):
                current = self._active_proxy_groups.get(proxy_group, "?")
                self.app.after(0, self._log,
                    f"[{profile_id}] BLOCKED: {proxy_group} in use by {current}")
                self.app.after(0, self._on_warmup_complete, profile_id, None)
                return
            self._rotate_proxy(proxy_group)

        def _stopped():
            return entry.get("stop", False)

        # 1. Start AdsPower browser
        self.app.after(0, self._log, f"[{profile_id}] Starting AdsPower browser...")
        try:
            resp = _requests.get(
                f"{api_base}/api/v1/browser/start"
                f"?user_id={profile_id}&api_key={api_key}",
                timeout=60,
            )
            data = resp.json()
            if data.get("code") != 0:
                self.app.after(0, self._log, f"[{profile_id}] AdsPower error: {data}")
                return
            ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
            if not ws_endpoint:
                self.app.after(0, self._log,
                    f"[{profile_id}] AdsPower start returned no CDP endpoint")
                return
            browser_started = True
            self.app.after(0, self._log,
                f"[{profile_id}] Browser started, connecting Playwright...")

            # 2. Connect Playwright (retry — AdsPower CDP may not be ready yet)
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = None
                for attempt in range(5):
                    try:
                        browser = p.chromium.connect_over_cdp(ws_endpoint)
                        break
                    except Exception as cdp_err:
                        if attempt < 4:
                            wait = 2 * (attempt + 1)
                            self.app.after(0, self._log,
                                f"[{profile_id}] CDP not ready, retry in {wait}s...")
                            time.sleep(wait)
                        else:
                            raise cdp_err
                contexts = browser.contexts
                ctx = contexts[0] if contexts else browser.new_context()
                all_pages = list(ctx.pages) if ctx else []

                # Pick a Reddit tab if one exists, otherwise use first tab
                page = None
                reddit_page = None
                for pg in all_pages:
                    try:
                        url = pg.url or ""
                    except Exception:
                        url = ""
                    if "reddit.com" in url:
                        reddit_page = pg
                        break

                if reddit_page:
                    page = reddit_page
                elif all_pages:
                    page = all_pages[0]
                else:
                    page = ctx.new_page()

                if page is None:
                    raise RuntimeError("No browser page available for warmup")

                # Close stale extra tabs
                closed = 0
                for pg in all_pages:
                    if pg != page:
                        try:
                            pg.close()
                            closed += 1
                        except Exception:
                            pass

                tab_msg = f"[{profile_id}] Playwright connected"
                if closed:
                    tab_msg += f" (closed {closed} stale tab{'s' if closed != 1 else ''})"
                self.app.after(0, self._log, tab_msg)
                if _stopped():
                    self.app.after(0, self._log,
                        f"[{profile_id}] Warmup stopped before session start")
                    return

                # 3. Create warmer
                warmer = AccountWarmer(
                    profile_id, page,
                    persona=persona,
                    attributes=profile_attributes,
                    grok_api_key=grok_key,
                    account_age_days=account_age_days,
                    account_created_at=account_created_at,
                )
                warmer.hijack_ratio = self.hijack_slider.get() / 100.0
                self._apply_warmup_overrides(warmer)
                entry["warmer"] = warmer
                if _stopped():
                    warmer.stop_requested = True

                day = warmer.get_day()
                max_posts = warmer.get_max_posts_today()
                self.app.after(0, self._log,
                    f"[{profile_id}] Day {day}, max posts today: {max_posts}, "
                    f"{len(warmer.general_subs)} general subs")
                self.app.after(0, self.progress_label.configure,
                    text=f"Day {day} — warmup in progress...")

                # 4. Attach log handler (filtered to THIS thread only)
                warmer_logger = logging.getLogger("core.account_warmer")
                handler = _GUILogHandler(self.log_box, self.app)
                handler.setFormatter(logging.Formatter(
                    f"%(asctime)s [{profile_id}] %(message)s",
                    datefmt="%H:%M:%S"))
                my_thread = threading.current_thread().ident
                handler.addFilter(
                    type("_TF", (logging.Filter,),
                         {"filter": lambda self, r, tid=my_thread: r.thread == tid})()
                )
                warmer_logger.addHandler(handler)
                entry["handler"] = handler

                try:
                    # 5. Run warmup
                    start = time.time()
                    try:
                        s_lo = int(self.session_min_entry.get())
                    except (ValueError, AttributeError):
                        s_lo = 15
                    try:
                        s_hi = max(s_lo, int(self.session_max_entry.get()))
                    except (ValueError, AttributeError):
                        s_hi = 30
                    session_min = random.randint(s_lo, s_hi) if s_lo else None
                    try:
                        c_lo = int(self.max_comments_entry.get())
                    except (ValueError, AttributeError):
                        c_lo = 10
                    try:
                        c_hi = max(c_lo, int(self.max_comments_high_entry.get()))
                    except (ValueError, AttributeError):
                        c_hi = 20
                    max_comments = random.randint(c_lo, c_hi) if c_lo else None

                    stats = warmer.run_daily_warmup(
                        session_minutes=session_min if session_min else None,
                        max_comments=max_comments if max_comments else None,
                    )
                    elapsed = int(time.time() - start)
                finally:
                    try:
                        warmer_logger.removeHandler(handler)
                    except Exception:
                        pass
                    entry.pop("handler", None)

                # 6. Report
                if stats:
                    self._last_action_log = stats.get("action_log", [])
                    self.app.after(0, self._log,
                        f"\n=== WARMUP COMPLETE ({profile_id}) ===\n"
                        f"Sessions: {stats['sessions']}\n"
                        f"Time: {elapsed // 60}m {elapsed % 60}s\n"
                        f"Scrolls: {stats['scrolls']}\n"
                        f"Upvotes: {stats['upvotes']}, "
                        f"Downvotes: {stats['downvotes']}\n"
                        f"Comments: {stats['comments']}\n"
                        f"Joins: {stats['joins']}\n"
                        f"Posts clicked: {stats['posts_clicked']}\n"
                        f"Subs browsed: {stats['subs_browsed']}")

                    # CQS check at end of warmup (if enabled)
                    if self.adv_cqs_var.get():
                        self.app.after(0, self._log,
                            f"[{profile_id}] Running CQS check...")
                        try:
                            cqs = warmer.check_cqs()
                            if cqs is not None:
                                self.app.after(0, self._log,
                                    f"[{profile_id}] CQS = {cqs}")
                            else:
                                self.app.after(0, self._log,
                                    f"[{profile_id}] CQS check: no result")
                        except Exception as cqs_err:
                            self.app.after(0, self._log,
                                f"[{profile_id}] CQS check error: {cqs_err}")

        except Exception as e:
            self.app.after(0, self._log, f"[{profile_id}] Warmup error: {e}")
        finally:
            # Clean up handler if still attached
            h = entry.get("handler")
            if h:
                try:
                    logging.getLogger("core.account_warmer").removeHandler(h)
                except Exception:
                    pass

            # Stop the AdsPower profile
            if browser_started:
                try:
                    _requests.get(
                        f"{api_base}/api/v1/browser/stop"
                        f"?user_id={profile_id}&api_key={api_key}",
                        timeout=15,
                    )
                    self.app.after(0, self._log,
                        f"[{profile_id}] AdsPower profile stopped")
                except Exception as stop_err:
                    self.app.after(0, self._log,
                        f"[{profile_id}] Failed to stop browser: {stop_err}")

            # Release proxy group
            if proxy_group:
                self._release_proxy_group(proxy_group, profile_id)

            self.app.after(0, self._on_warmup_complete, profile_id, stats)

    def _on_warmup_complete(self, profile_id, stats):
        """Clean up one warmup session. Re-enable buttons if none left."""
        self._active_warmups.pop(profile_id, None)
        n = len(self._active_warmups)

        if n == 0:
            self.warmup_btn.configure(text="Warmup Only")
            self.stop_btn.configure(state="disabled")
        else:
            self.warmup_btn.configure(text=f"Warmup ({n} running)")

        if stats:
            elapsed = stats.get("total_sec", 0)
            self.progress_label.configure(
                text=f"Warmup done ({profile_id}) - {stats['sessions']} sessions, "
                     f"{elapsed // 60}m {elapsed % 60}s, "
                     f"{stats['upvotes']}up/{stats['downvotes']}down, "
                     f"{stats['comments']} comments")
        else:
            self.progress_label.configure(
                text=f"Warmup failed or stopped ({profile_id})")

        # Refresh lifetime stats and account statuses
        self._refresh_life_stats(silent=True)
        self._refresh_account_statuses()

    # ===================== STOP ALL =====================

    def _stop_everything(self):
        """Signal all campaigns and warmup to stop."""
        # Stop posting campaigns
        self.stop_all = True
        for c in self.campaigns:
            c.stop_requested = True
            if c.warmer:
                c.warmer.stop_requested = True

        # Stop all active warmups
        for pid, entry in self._active_warmups.items():
            entry["stop"] = True
            w = entry.get("warmer")
            if w:
                w.stop_requested = True

        self._log("\nSTOP REQUESTED - finishing current action...")

    # ===================== ACTIVITY POPOUT =====================

    def _show_activity_popout(self):
        """Open a popout window showing all actions from the last warmup session."""
        log = self._last_action_log
        if not log:
            messagebox.showinfo("No Activity",
                                "No activity recorded yet. Run a warmup session first.")
            return

        win = ctk.CTkToplevel(self.app)
        win.title("Warmup Activity Log")
        win.geometry("900x520")
        win.attributes("-topmost", True)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        # Summary bar
        comments = [a for a in log if a["type"] in ("comment", "reply")]
        verified = [a for a in comments if a["status"] == "verified"]
        failed = [a for a in comments if a["status"] == "failed"]
        votes = [a for a in log if a["type"] in ("upvote", "downvote")]
        joins = [a for a in log if a["type"] == "join"]
        clicks = [a for a in log if a["type"] == "click"]

        summary = (
            f"{len(log)} actions  |  "
            f"{len(votes)} votes  |  "
            f"{len(comments)} comments ({len(verified)} verified, {len(failed)} failed)  |  "
            f"{len(joins)} joins  |  "
            f"{len(clicks)} posts clicked"
        )
        ctk.CTkLabel(win, text=summary, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        # Scrollable activity list
        scroll = ctk.CTkScrollableFrame(win, fg_color=("#F9FAFB", "#111827"))
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        scroll.grid_columnconfigure(3, weight=1)

        # Header row
        headers = [("Time", 60), ("Type", 70), ("Sub", 130), ("Detail", 0), ("Status", 70)]
        for col, (hdr, w) in enumerate(headers):
            ctk.CTkLabel(scroll, text=hdr, font=("Segoe UI", 11, "bold"),
                         anchor="w").grid(row=0, column=col, sticky="ew", padx=4, pady=(0, 4))

        type_colors = {
            "upvote": "#16A34A", "downvote": "#DC2626",
            "comment": "#2563EB", "reply": "#7C3AED",
            "join": "#D97706", "click": "#6B7280",
        }
        status_colors = {
            "verified": "#16A34A", "failed": "#DC2626", "ok": "#6B7280",
        }

        for i, action in enumerate(log):
            row = i + 1
            bg = ("#FFFFFF", "#1A1A2E") if i % 2 == 0 else ("#F3F4F6", "#1E2033")

            ctk.CTkLabel(scroll, text=action.get("ts", ""),
                         font=("Consolas", 11), fg_color=bg, anchor="w").grid(
                row=row, column=0, sticky="ew", padx=4, pady=1)

            ctk.CTkLabel(
                scroll, text=action.get("type", ""),
                font=("Segoe UI", 11, "bold"), fg_color=bg,
                text_color=type_colors.get(action.get("type"), "#6B7280"),
                anchor="w").grid(row=row, column=1, sticky="ew", padx=4, pady=1)

            sub = action.get("sub", "")
            ctk.CTkLabel(scroll, text=f"r/{sub}" if sub else "",
                         font=("Segoe UI", 11), fg_color=bg, anchor="w").grid(
                row=row, column=2, sticky="ew", padx=4, pady=1)

            detail_text = action.get("text", "") or action.get("url", "")
            url = action.get("url", "")
            detail_frame = ctk.CTkFrame(scroll, fg_color=bg)
            detail_frame.grid(row=row, column=3, sticky="ew", padx=4, pady=1)

            if detail_text:
                ctk.CTkLabel(
                    detail_frame, text=detail_text[:80],
                    font=("Segoe UI", 11), anchor="w"
                ).pack(side="left", fill="x", expand=True)

            if url and url.startswith("http"):
                ctk.CTkButton(
                    detail_frame, text="Open", width=45,
                    font=("Segoe UI", 10), height=22,
                    fg_color="#334155", hover_color="#1F2937",
                    command=lambda u=url: webbrowser.open(u)
                ).pack(side="right", padx=(4, 0))

            status = action.get("status", "ok")
            ctk.CTkLabel(
                scroll, text=status, font=("Segoe UI", 11, "bold"), fg_color=bg,
                text_color=status_colors.get(status, "#6B7280"), anchor="w"
            ).grid(row=row, column=4, sticky="ew", padx=4, pady=1)
