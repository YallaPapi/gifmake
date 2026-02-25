"""
Account Warmup tab for GifMake GUI.
Standalone warmup sessions â€” browse, vote, comment, join subs â€” without posting.
"""
import customtkinter as ctk
from tkinter import messagebox
import webbrowser
import threading
import os
import json
import sys
import logging
import logging.handlers
import time
import random
import requests
from datetime import datetime, timedelta
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.post_history import (
    get_warmup_status, get_warmup_day, get_warmed_today_ids,
    get_all_warmup_stats, get_today_sessions, reset_today_warmup,
    get_daily_cap, get_daily_totals,
    record_karma, get_latest_karma,
)
from processors.account_profile import (
    ProfileManager,
    AccountProfile,
    AccountAttributes,
    PersonaInterests,
    RedditAccount,
)

logger = logging.getLogger(__name__)

# Rotating file log — survives crashes, caps at 5MB x 3 files
_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(_LOG_DIR, "warmup.log"),
    maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.setLevel(logging.INFO)
logging.getLogger("core.account_warmer").setLevel(logging.INFO)
logging.getLogger("core.account_warmer").addHandler(_file_handler)
logger.addHandler(_file_handler)


def _auto_wrap(label):
    """Bind a label's wraplength to its actual allocated width so text never clips."""
    def _on_resize(event):
        new = event.width - 10
        if new > 50:
            label.configure(wraplength=new)
    label.bind("<Configure>", _on_resize)


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
                                 "UPVOT", "DOWNVOT", "COMMENT", "REPLIED", "JOINED")):
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
        "header":  {"foreground": "#2DD4BF", "font": ("Consolas", 13, "bold")},
        "muted":   {"foreground": "#94A3B8"},
    }
    for tag, opts in tags.items():
        textbox._textbox.tag_config(tag, **opts)


def _warmup_stats_ok(stats):
    """True only when warmup produced a usable session (not a startup/feed failure)."""
    if not isinstance(stats, dict):
        return False
    if stats.get("ok") is False:
        return False
    # Match core.account_warmer.warmup_stats_ok semantics: require real activity.
    for key in ("scrolls", "posts_clicked", "comments", "joins",
                "upvotes", "downvotes", "subs_browsed"):
        try:
            if int(stats.get(key, 0) or 0) > 0:
                return True
        except Exception:
            continue
    return False


def _warmup_failure_text(stats):
    if not isinstance(stats, dict):
        return "warmup failed"
    reason = (stats.get("failure_reason") or "warmup failed").replace("_", " ")
    detail = (stats.get("failure_detail") or "").strip()
    return f"{reason}: {detail}" if detail else reason


def _warmup_fail_stats(reason, detail=""):
    return {
        "ok": False,
        "failure_reason": str(reason or "warmup_failed"),
        "failure_detail": str(detail or "").strip(),
    }


def _adspower_cdp_endpoint(payload):
    """Validate AdsPower browser/start payload shape and extract CDP endpoint."""
    if not isinstance(payload, dict):
        return "", "adspower_bad_response", (
            f"browser/start returned non-dict JSON ({type(payload).__name__})")

    data_node = payload.get("data")
    if data_node is None:
        data_node = {}
    elif not isinstance(data_node, dict):
        return "", "adspower_bad_response", (
            "AdsPower browser/start payload field 'data' is not an object")

    ws_node = data_node.get("ws")
    if ws_node is None:
        ws_node = {}
    elif not isinstance(ws_node, dict):
        return "", "adspower_bad_response", (
            "AdsPower browser/start payload field 'data.ws' is not an object")

    endpoint = ws_node.get("puppeteer")
    if endpoint is None or endpoint == "":
        return "", "adspower_missing_cdp", (
            "AdsPower browser/start succeeded but no CDP endpoint was returned")
    if not isinstance(endpoint, str):
        return "", "adspower_bad_response", (
            "AdsPower browser/start payload field 'data.ws.puppeteer' is not a string")

    endpoint = endpoint.strip()
    if not endpoint:
        return "", "adspower_missing_cdp", (
            "AdsPower browser/start succeeded but no CDP endpoint was returned")
    return endpoint, None, ""

ADSPOWER_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "uploaders", "redgifs", "adspower_config.json"
)
ACCOUNT_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "account_profiles.json"
)

# Run All constants
PROXY_PREFIXES = ("P ", "G ", "F ", "4u ")
BAN_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "warmup_bans.json"
)
SCHEDULE_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "schedule_config.json"
)

CARD_FG = ("#F7F9FC", "#1A2030")
CARD_INNER = ("#FFFFFF", "#1E2638")
HERO_FG = ("#EAF3FF", "#0F1A2E")
ACCENT = "#0F766E"
ACCENT_HOVER = "#115E59"
WARN = "#DC2626"
WARN_HOVER = "#B91C1C"
SECONDARY = "#334155"
SECONDARY_HOVER = "#1F2937"

# Pill badge colors (light, dark)
PILL_COLORS = {
    "upvote":   ("#16A34A", "#22C55E"),
    "downvote": ("#DC2626", "#EF4444"),
    "comment":  ("#2563EB", "#3B82F6"),
    "join":     ("#D97706", "#F59E0B"),
    "click":    ("#7C3AED", "#A78BFA"),
    "scroll":   ("#6B7280", "#9CA3AF"),
    "session":  ("#0F766E", "#14B8A6"),
    "time":     ("#475569", "#94A3B8"),
}


def _make_pill(parent, value, label, color_key, width=82):
    """Create a colored stat pill badge."""
    colors = PILL_COLORS.get(color_key, ("#6B7280", "#9CA3AF"))
    pill = ctk.CTkFrame(parent, fg_color=colors, corner_radius=10,
                        width=width, height=52)
    pill.pack_propagate(False)
    val_lbl = ctk.CTkLabel(pill, text=str(value), font=("Segoe UI", 17, "bold"),
                           text_color="white")
    val_lbl.pack(pady=(5, 0))
    cap_lbl = ctk.CTkLabel(pill, text=label, font=("Segoe UI", 8),
                           text_color=("#d4d4d4", "#d4d4d4"))
    cap_lbl.pack(pady=(0, 3))
    return pill, val_lbl


def _accent_card(parent, accent_color=ACCENT, **grid_kw):
    """Create a card with a left accent bar for depth."""
    outer = ctk.CTkFrame(parent, fg_color=accent_color, corner_radius=8)
    outer.grid(**grid_kw)
    outer.grid_columnconfigure(0, weight=1)
    inner = ctk.CTkFrame(outer, fg_color=CARD_INNER, corner_radius=6)
    inner.grid(row=0, column=0, sticky="nsew", padx=(3, 0), pady=0)
    inner.grid_columnconfigure(0, weight=1)
    return outer, inner


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
            pass  # GUI may be closing

    def _append(self, msg):
        tag = _detect_log_tag(msg)
        if tag:
            self.textbox.insert("end", f"{msg}\n", tag)
        else:
            self.textbox.insert("end", f"{msg}\n")
        self.textbox.see("end")


class WarmupTab:
    """Standalone account warmup tab."""

    def __init__(self, parent_frame, app):
        self.parent = parent_frame
        self.app = app
        self.is_running = False
        self._stop_requested = False
        self.warmer = None  # Set during worker, used for stop
        self._log_handler = None

        self.adspower_config = {}
        self.profiles_data = {}  # adspower profile_id -> account_name
        self.persona_profiles = {}  # persona key -> persona dict

        # Run All state — multi-account warmup across all proxy groups
        self._run_all_active = False
        self._run_all_stop = False
        self._group_threads = {}        # {group: Thread}
        self._group_results = {}        # {group: [result_dicts]}
        self._group_warmers = {}        # {group: AccountWarmer or None}
        self._ban_log = {}
        self._rotation_urls = {}
        self._rotation_failed_groups = set()
        self._run_all_lock = threading.Lock()

        # Continuous cycling state
        self._cycle_count = 0
        self._next_cycle_time = None        # datetime of next cycle start
        self._daily_caps_cache = {}         # {profile_id: {"comments": int, "joins": int}}
        self._accounts_at_cap = set()       # profile_ids that hit daily cap
        self._cycle_timer_id = None         # after() ID for countdown timer
        self._last_cycle_date = None        # YYYY-MM-DD, for day-boundary reset

        self._load_adspower_config()
        self._load_persona_profiles()
        self._create_widgets()

    def _load_adspower_config(self):
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

    def _load_persona_profiles(self):
        try:
            if os.path.exists(ACCOUNT_PROFILES_PATH):
                with open(ACCOUNT_PROFILES_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                self.persona_profiles = data.get("profiles", {})
        except Exception as e:
            logger.error(f"Failed to load persona profiles: {e}")

    def _slugify_profile_id(self, username):
        base = re.sub(r"[^a-z0-9]+", "_", (username or "").lower()).strip("_")
        return base or "reddit_account"

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

    def _build_account_map(self):
        """Build dropdown options by merging persona and AdsPower mappings.
        Accounts with a configured persona sort first (they are active).
        """
        self._acct_map = {}
        options = []

        persona_by_ads = {}
        for key, profile in self.persona_profiles.items():
            ads_id = (profile.get("adspower_id") or "").strip()
            if ads_id:
                persona_by_ads[ads_id] = key

        # Sort: persona-linked accounts first, then alphabetical
        def _sort_key(item):
            ads_id = item[0]
            has_persona = 0 if ads_id in persona_by_ads else 1
            return (has_persona, item[1].lower())

        for ads_id, account_name in sorted(self.profiles_data.items(), key=_sort_key):
            persona_key = persona_by_ads.get(ads_id)
            username = account_name
            if persona_key and persona_key in self.persona_profiles:
                username = self.persona_profiles[persona_key].get(
                    "reddit_account", {}
                ).get("username", account_name)
            display = f"{username} ({ads_id})"
            self._acct_map[display] = (ads_id, persona_key)
            options.append(display)

        # Include persona profiles that don't have AdsPower mapping yet.
        for key, profile in sorted(self.persona_profiles.items()):
            ads_id = (profile.get("adspower_id") or "").strip()
            if not ads_id or ads_id in self.profiles_data:
                continue
            username = profile.get("reddit_account", {}).get("username", key)
            display = f"{username} ({ads_id})"
            self._acct_map[display] = (ads_id, key)
            options.append(display)

        if not options:
            options = ["No accounts configured"]
        return options

    def _refresh_account_options(self, preferred_ads_id=None):
        options = self._build_account_map()
        self.acct_dropdown.configure(values=options)

        selected = options[0]
        if preferred_ads_id:
            for item in options:
                if item in self._acct_map and self._acct_map[item][0] == preferred_ads_id:
                    selected = item
                    break
        self.acct_var.set(selected)
        self._on_account_change(selected)

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

    def _create_widgets(self):
        self.parent.grid_columnconfigure(0, weight=1)

        # === TAB DESCRIPTION ===
        hero_frame = ctk.CTkFrame(
            self.parent, fg_color=HERO_FG, border_width=1, border_color=("#C8D9F1", "#314055")
        )
        hero_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(0, 10))
        hero_frame.grid_columnconfigure(0, weight=1)
        desc = ctk.CTkLabel(
            hero_frame,
            text="Build account credibility before posting.\n"
                 "Warmup sessions browse Reddit, vote, comment, and join communities\n"
                 "to reduce spam-filter risk on fresh accounts.",
            font=("Segoe UI", 13), text_color=("#1F2937", "#E5E7EB"), justify="left"
        )
        desc.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        _auto_wrap(desc)

        ctk.CTkLabel(
            hero_frame,
            text="Continuous warmup: cycles through all accounts, waits 45-120 min between cycles, stops when all hit daily cap.",
            font=("Segoe UI", 13),
            text_color=("#334155", "#E2E8F0"),
            justify="left",
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        # Load API keys silently from config (no visible entry fields)
        saved_keys = self._load_api_keys()
        self._grok_key = saved_keys.get("grok_api_key", "") or os.environ.get("GROK_API_KEY", "")

        # Hidden entry widget (warmup worker reads from this)
        self.grok_key_entry = ctk.CTkEntry(self.parent)
        if self._grok_key:
            self.grok_key_entry.insert(0, self._grok_key)
        # Don't grid â€” stays invisible

        # === ROW 1: WARMUP OVERVIEW (Stats Panel) ===
        self._build_stats_panel()

        # === ROW 7: ACCOUNT SELECTOR ===
        acct_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG)
        acct_frame.grid(row=7, column=0, sticky="ew", pady=(0, 5), padx=5)
        acct_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(acct_frame, text="Step 4: Reddit Account", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=(10, 5), pady=5)

        acct_options = self._build_account_map()

        self.acct_var = ctk.StringVar(value=acct_options[0])
        self.acct_dropdown = ctk.CTkOptionMenu(
            acct_frame, variable=self.acct_var,
            values=acct_options,
            command=self._on_account_change)
        self.acct_dropdown.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        self.add_account_btn = ctk.CTkButton(
            acct_frame, text="+ Add Account", width=120, height=30,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=("Segoe UI", 13, "bold"),
            command=self._open_add_account_dialog
        )
        self.add_account_btn.grid(row=0, column=2, padx=(5, 0), pady=5)

        self.remove_account_btn = ctk.CTkButton(
            acct_frame, text="Unlink", width=80, height=30,
            fg_color=WARN, hover_color=WARN_HOVER, font=("Segoe UI", 13, "bold"),
            command=self._remove_selected_account
        )
        self.remove_account_btn.grid(row=0, column=3, padx=(5, 0), pady=5)

        self.reload_accounts_btn = ctk.CTkButton(
            acct_frame, text="Reload Config", width=100, height=30,
            fg_color="#334155", hover_color="#1F2937", font=("Segoe UI", 13),
            command=self._reload_persona
        )
        self.reload_accounts_btn.grid(row=0, column=4, padx=(5, 10), pady=5)

        # Hidden profile_entry for backward compat with _start_warmup
        self.profile_entry = ctk.CTkEntry(self.parent)

        # Pre-fill from first account
        if self._acct_map:
            first = acct_options[0]
            ads_id, _ = self._acct_map[first]
            self.profile_entry.insert(0, ads_id)

        acct_help = ctk.CTkLabel(acct_frame,
            text="Select an account for Single Account Warmup below. "
                 "Format: username (AdsPower browser ID). "
                 "Multi-Account Warmup ignores this â€” it auto-discovers from AdsPower.",
            font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB")
        )
        acct_help.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 5))
        _auto_wrap(acct_help)

        # === ROW 8: PERSONA CONFIG ===
        persona_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG)
        persona_frame.grid(row=8, column=0, sticky="ew", padx=5, pady=(0, 5))
        persona_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(persona_frame, text="Step 5: Persona", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=(10, 5), pady=(5, 0))

        self.persona_info_label = ctk.CTkLabel(
            persona_frame,
            text="",
            font=("Segoe UI", 13), text_color=("#374151", "#CBD5E1"), justify="left"
        )
        self.persona_info_label.grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=(5, 0))
        _auto_wrap(self.persona_info_label)

        # Persona config file path + edit button
        config_row = ctk.CTkFrame(persona_frame, fg_color="transparent")
        config_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 5))
        config_row.grid_columnconfigure(0, weight=1)

        abs_profiles_path = os.path.abspath(ACCOUNT_PROFILES_PATH)
        self.persona_path_label = ctk.CTkLabel(
            config_row,
            text=f"Config: {abs_profiles_path}",
            font=("Segoe UI", 13), text_color=("#6B7280", "#D1D5DB")
        )
        self.persona_path_label.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            config_row, text="Edit Persona File", width=120, height=28,
            font=("Segoe UI", 13), command=self._open_persona_config
        ).grid(row=0, column=1, padx=(10, 0))

        ctk.CTkButton(
            config_row, text="Reload Config", width=100, height=28,
            font=("Segoe UI", 13), fg_color="#475569", hover_color="#334155",
            command=self._reload_persona
        ).grid(row=0, column=2, padx=(5, 0))

        persona_help = ctk.CTkLabel(
            persona_frame,
            text="The persona defines what your account is \"into\" â€” hobbies, location, personality. "
                 "The bot uses this to pick subreddits to browse and to generate realistic comments.",
            font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB")
        )
        persona_help.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 5))
        _auto_wrap(persona_help)

        # Hidden persona var for backward compat
        self.persona_var = ctk.StringVar(value="")

        # === ROW 3: SESSION STATS (pill badges) ===
        sess_outer, sess_inner = _accent_card(
            self.parent, accent_color="#0F766E",
            row=3, column=0, sticky="ew", padx=5, pady=(5, 3))

        sess_inner.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sess_inner, text="STEP 2: CURRENT RUN",
                     font=("Segoe UI", 13, "bold"),
                     text_color=("#0F766E", "#14B8A6")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        self.sess_time_label = ctk.CTkLabel(
            sess_inner, text="--", font=("Segoe UI", 13),
            text_color=("#6B7280", "#D1D5DB"))
        self.sess_time_label.grid(row=0, column=1, sticky="e", padx=10, pady=(8, 4))

        pill_frame = ctk.CTkFrame(sess_inner, fg_color="transparent")
        pill_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        self._pill_refs = {}
        pill_defs = [
            ("upvotes", "Upvotes", "upvote"),
            ("downvotes", "Downvotes", "downvote"),
            ("comments", "Comments", "comment"),
            ("joins", "Subs Joined", "join"),
            ("clicks", "Posts Read", "click"),
            ("scrolls", "Feed Scrolls", "scroll"),
            ("subs", "Subs Visited", "session"),
            ("sessions", "Browse Loops", "time"),
        ]
        for key, label, color_key in pill_defs:
            pill, val_lbl = _make_pill(pill_frame, "--", label, color_key)
            pill.pack(side="left", padx=3, pady=2)
            self._pill_refs[key] = val_lbl

        # === ROW 9: LIFETIME STATS (compact bar) ===
        life_outer, life_inner = _accent_card(
            self.parent, accent_color=("#94A3B8", "#475569"),
            row=9, column=0, sticky="ew", padx=5, pady=(0, 5))

        life_hdr = ctk.CTkFrame(life_inner, fg_color="transparent")
        life_hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        ctk.CTkLabel(life_hdr, text="LIFETIME TOTALS",
                     font=("Segoe UI", 13, "bold"),
                     text_color=("#6B7280", "#D1D5DB")).pack(side="left")
        ctk.CTkLabel(life_hdr,
                     text="  (all warmup sessions combined for selected account)",
                     font=("Segoe UI", 13),
                     text_color=("#6B7280", "#D1D5DB")).pack(side="left")

        life_vals = ctk.CTkFrame(life_inner, fg_color="transparent")
        life_vals.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 8))

        self.day_label = ctk.CTkLabel(
            life_vals, text="Account Age: --", font=("Segoe UI", 14, "bold"),
            text_color=("#334155", "#E2E8F0"))
        self.day_label.pack(side="left", padx=(0, 14))

        self.status_val_label = ctk.CTkLabel(
            life_vals, text="--", font=("Segoe UI", 13),
            text_color=("#6B7280", "#D1D5DB"))
        self.status_val_label.pack(side="left", padx=(0, 18))

        self.upvotes_label = ctk.CTkLabel(
            life_vals, text="0 upvotes", font=("Segoe UI", 13, "bold"),
            text_color=("#16A34A", "#22C55E"))
        self.upvotes_label.pack(side="left", padx=(0, 14))

        self.comments_label = ctk.CTkLabel(
            life_vals, text="0 comments", font=("Segoe UI", 13, "bold"),
            text_color=("#2563EB", "#3B82F6"))
        self.comments_label.pack(side="left", padx=(0, 14))

        self.joins_label = ctk.CTkLabel(
            life_vals, text="0 joins", font=("Segoe UI", 13, "bold"),
            text_color=("#D97706", "#F59E0B"))
        self.joins_label.pack(side="left", padx=(0, 14))

        self.posts_label = ctk.CTkLabel(
            life_vals, text="0 posts", font=("Segoe UI", 13, "bold"),
            text_color=("#7C3AED", "#A78BFA"))
        self.posts_label.pack(side="left")

        # === ROW 10: SINGLE ACCOUNT WARMUP ===
        single_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG, corner_radius=8)
        single_frame.grid(row=10, column=0, sticky="ew", padx=5, pady=(5, 3))
        single_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            single_frame, text="STEP 6: SINGLE ACCOUNT WARMUP",
            font=("Segoe UI", 14, "bold"), text_color=("#0F766E", "#14B8A6"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 0))
        single_desc = ctk.CTkLabel(
            single_frame,
            text="Runs warmup for the one account selected in the dropdown above. "
                 "Opens that account's AdsPower browser, browses Reddit, votes, comments, "
                 "and joins subs. No proxy rotation (single account only).",
            font=("Segoe UI", 13), text_color=("#6B7280", "#D1D5DB"), anchor="w",
        )
        single_desc.grid(row=1, column=0, columnspan=4, sticky="ew", padx=8, pady=(2, 6))
        _auto_wrap(single_desc)

        # Poetry warmup toggle
        poetry_row = ctk.CTkFrame(single_frame, fg_color="transparent")
        poetry_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 6))
        poetry_row.grid_columnconfigure(2, weight=1)

        self.poetry_var = ctk.BooleanVar(value=False)
        self.poetry_check = ctk.CTkCheckBox(
            poetry_row, text="Poetry Warmup",
            variable=self.poetry_var,
            font=("Segoe UI", 13, "bold"),
            text_color=("#7C3AED", "#C4B5FD"),
            onvalue=True, offvalue=False,
        )
        self.poetry_check.grid(row=0, column=0, sticky="w", padx=(0, 10))

        ctk.CTkLabel(
            poetry_row, text="Karma target:",
            font=("Segoe UI", 13), text_color=("#374151", "#E5E7EB"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 4))

        self.karma_target_entry = ctk.CTkEntry(
            poetry_row, width=70, height=28, font=("Segoe UI", 13),
            placeholder_text="1500",
        )
        self.karma_target_entry.insert(0, "1500")
        self.karma_target_entry.grid(row=0, column=2, sticky="w")

        poetry_help = ctk.CTkLabel(
            poetry_row,
            text="Poems reply to top comments for fast karma.",
            font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB"),
        )
        poetry_help.grid(row=0, column=3, sticky="ew", padx=(10, 0))
        _auto_wrap(poetry_help)

        self.start_btn = ctk.CTkButton(
            single_frame, text="Start Selected Account", font=("Segoe UI", 13, "bold"),
            height=38, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_warmup)
        self.start_btn.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 6))

        self.stop_btn = ctk.CTkButton(
            single_frame, text="Stop (Graceful)", font=("Segoe UI", 13, "bold"),
            height=38, fg_color=WARN, hover_color=WARN_HOVER,
            command=self._stop_warmup, state="disabled")
        self.stop_btn.grid(row=3, column=1, sticky="ew", padx=4, pady=(0, 6))

        self.refresh_btn = ctk.CTkButton(
            single_frame, text="Reload DB Stats", font=("Segoe UI", 13, "bold"),
            height=38, fg_color="#334155", hover_color="#1F2937",
            command=self._refresh_stats)
        self.refresh_btn.grid(row=3, column=2, sticky="ew", padx=4, pady=(0, 6))

        self.activity_btn = ctk.CTkButton(
            single_frame, text="Last Session Log", font=("Segoe UI", 13, "bold"),
            height=38, fg_color="#1E40AF", hover_color="#1E3A8A",
            command=self._show_activity_popout)
        self.activity_btn.grid(row=3, column=3, sticky="ew", padx=4, pady=(0, 6))

        # === ROW 2: MULTI-ACCOUNT WARMUP ===
        all_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG, corner_radius=8,
                                 border_width=2, border_color=("#15803D", "#166534"))
        all_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=(3, 5))
        all_frame.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        ctk.CTkLabel(
            all_frame, text="STEP 1: MULTI-ACCOUNT WARMUP (ALL PROXY GROUPS)",
            font=("Segoe UI", 14, "bold"), text_color=("#15803D", "#4ADE80"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 0))
        all_desc = ctk.CTkLabel(
            all_frame,
            text="Scans AdsPower for ALL accounts named 'P ...', 'G ...', 'F ...', '4u ...' "
                 "(these are the 4 proxy groups â€” each shares one mobile proxy IP). "
                 "Runs 4 threads (one per group). Within each group, accounts run one at a time: "
                 "open browser \u2192 ban check \u2192 warmup \u2192 close browser \u2192 rotate proxy IP \u2192 next account. "
                 "Permabanned accounts (from data/warmup_bans.json) are auto-skipped.",
            font=("Segoe UI", 13), text_color=("#6B7280", "#D1D5DB"), anchor="w",
        )
        all_desc.grid(row=1, column=0, columnspan=4, sticky="ew", padx=8, pady=(2, 6))
        _auto_wrap(all_desc)

        # Proxy group toggles — uncheck a group to skip it (e.g. while VA creates accounts)
        proxy_toggle_row = ctk.CTkFrame(all_frame, fg_color="transparent")
        proxy_toggle_row.grid(row=2, column=0, columnspan=5, sticky="ew", padx=8, pady=(0, 4))

        ctk.CTkLabel(
            proxy_toggle_row, text="Proxy groups:",
            font=("Segoe UI", 13, "bold"), text_color=("#374151", "#E5E7EB"),
        ).pack(side="left", padx=(0, 8))

        self._proxy_group_vars = {}
        for grp in ["P", "G", "F", "4u"]:
            var = ctk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(
                proxy_toggle_row, text=grp, variable=var,
                font=("Segoe UI", 13, "bold"),
                text_color=("#15803D", "#4ADE80"),
                onvalue=True, offvalue=False, width=60,
            )
            cb.pack(side="left", padx=(0, 6))
            self._proxy_group_vars[grp] = var

        # Poetry warmup toggle (applies to ALL accounts in multi-warmup)
        poetry_row = ctk.CTkFrame(all_frame, fg_color="transparent")
        poetry_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 6))
        poetry_row.grid_columnconfigure(3, weight=1)

        self.poetry_all_var = ctk.BooleanVar(value=False)
        self.poetry_all_check = ctk.CTkCheckBox(
            poetry_row, text="Poetry Mode",
            variable=self.poetry_all_var,
            font=("Segoe UI", 13, "bold"),
            text_color=("#7C3AED", "#C4B5FD"),
            onvalue=True, offvalue=False,
        )
        self.poetry_all_check.grid(row=0, column=0, sticky="w", padx=(0, 10))

        ctk.CTkLabel(
            poetry_row, text="Karma target:",
            font=("Segoe UI", 13), text_color=("#374151", "#E5E7EB"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 4))

        self.karma_all_entry = ctk.CTkEntry(
            poetry_row, width=70, height=28, font=("Segoe UI", 13),
            placeholder_text="1500",
        )
        self.karma_all_entry.insert(0, "1500")
        self.karma_all_entry.grid(row=0, column=2, sticky="w")

        poetry_all_help = ctk.CTkLabel(
            poetry_row,
            text="Poems reply to top comments for fast karma.",
            font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB"),
        )
        poetry_all_help.grid(row=0, column=3, sticky="ew", padx=(10, 0))
        _auto_wrap(poetry_all_help)

        self.run_all_btn = ctk.CTkButton(
            all_frame, text="Start All Accounts (Auto-Discover from AdsPower)",
            font=("Segoe UI", 13, "bold"),
            height=42, fg_color="#15803D", hover_color="#166534",
            command=self._start_run_all)
        self.run_all_btn.grid(row=4, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 6))

        self.stop_all_btn = ctk.CTkButton(
            all_frame, text="Stop All (Graceful)", font=("Segoe UI", 13, "bold"),
            height=42, fg_color="#991B1B", hover_color="#7F1D1D",
            command=self._stop_run_all, state="disabled")
        self.stop_all_btn.grid(row=4, column=2, sticky="ew", padx=4, pady=(0, 6))

        self.reset_today_btn = ctk.CTkButton(
            all_frame, text="Reset Today", font=("Segoe UI", 13, "bold"),
            height=42, fg_color="#B45309", hover_color="#92400E",
            command=self._reset_today)
        self.reset_today_btn.grid(row=4, column=3, sticky="ew", padx=4, pady=(0, 6))

        self.karma_btn = ctk.CTkButton(
            all_frame, text="Check Karma", font=("Segoe UI", 13, "bold"),
            height=42, fg_color="#6D28D9", hover_color="#5B21B6",
            command=self._start_karma_check)
        self.karma_btn.grid(row=4, column=4, sticky="ew", padx=4, pady=(0, 6))

        # Proxy group progress
        ctk.CTkLabel(
            all_frame, text="Proxy Group Progress:",
            font=("Segoe UI", 14, "bold"), text_color=("#6B7280", "#D1D5DB"),
            anchor="w",
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=(2, 0))

        self._group_labels = {}
        for i, grp in enumerate(["P", "G", "F", "4u"]):
            lbl = ctk.CTkLabel(
                all_frame, text=f"{grp}: waiting",
                font=("Segoe UI", 13, "bold"), text_color=("#334155", "#E2E8F0"))
            lbl.grid(row=6, column=i, padx=8, pady=2)
            self._group_labels[grp] = lbl

        self._run_all_overall_label = ctk.CTkLabel(
            all_frame, text="Not started",
            font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB"))
        self._run_all_overall_label.grid(row=7, column=0, columnspan=4, pady=(0, 8))

        # Store last session's action log for the popout
        self._last_action_log = []

        # === ROW 4: PROGRESS ===
        self.progress_label = ctk.CTkLabel(
            self.parent, text="", font=("Segoe UI", 13), text_color=("#4B5563", "#D1D5DB"))
        self.progress_label.grid(row=4, column=0, sticky="w", padx=10, pady=(5, 2))

        # === ROW 5: LOG LABEL / ROW 6: LOG BOX ===
        ctk.CTkLabel(
            self.parent, text="STEP 3: LIVE ACTIVITY LOG", font=("Segoe UI", 14, "bold")
        ).grid(row=5, column=0, sticky="w", padx=10, pady=(5, 0))
        self.log_box = ctk.CTkTextbox(
            self.parent, height=350, font=("Consolas", 13),
            fg_color=("#F9FAFB", "#0D1117"), border_width=1,
            border_color=("#D1D5DB", "#21262D"))
        self.log_box.grid(row=6, column=0, sticky="nsew", padx=5, pady=5)
        self.parent.grid_rowconfigure(6, weight=1)
        _setup_log_tags(self.log_box)

        # Trigger initial account selection (must be after all widgets are created)
        if acct_options and acct_options[0] in self._acct_map:
            self._on_account_change(acct_options[0])

    # ── Warmup Overview Stats Panel ────────────────────────────────────

    # Phase color system (cold → warm progression)
    PHASE_DEFS = [
        {"key": "lurker",  "name": "Lurker",  "days": (1, 7),
         "fill": "#64748B", "bg": "#334155", "text": "#94A3B8", "badge": "#1E293B"},
        {"key": "light",   "name": "Light",   "days": (8, 14),
         "fill": "#38BDF8", "bg": "#1E3A5F", "text": "#7DD3FC", "badge": "#0C2D48"},
        {"key": "regular", "name": "Regular", "days": (15, 21),
         "fill": "#A78BFA", "bg": "#2D2150", "text": "#C4B5FD", "badge": "#1E1545"},
        {"key": "active",  "name": "Active",  "days": (22, 30),
         "fill": "#22C55E", "bg": "#14352A", "text": "#4ADE80", "badge": "#052E16"},
    ]

    STATUS_COLORS = {
        "done":    {"strip": "#22C55E", "bg": ("#F0FDF4", "#0A1F0A")},
        "pending": {"strip": "#3B82F6", "bg": CARD_FG},
        "stale":   {"strip": "#F59E0B", "bg": ("#FFFBEB", "#1F1A0A")},
        "banned":  {"strip": "#EF4444", "bg": ("#FEF2F2", "#1F0A0A")},
        "idle":    {"strip": "#6B7280", "bg": CARD_FG},
    }

    def _get_phase(self, day):
        """Return phase dict for the given warmup day."""
        for p in self.PHASE_DEFS:
            if day <= p["days"][1]:
                return p
        return self.PHASE_DEFS[-1]

    def _build_stats_panel(self):
        """Build the full warmup overview: collapsible header + KPI cards + account cards."""
        self._stats_outer = ctk.CTkFrame(
            self.parent, fg_color=CARD_FG, corner_radius=8,
            border_width=1, border_color=("#C8D9F1", "#314055"))
        self._stats_outer.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        self._stats_outer.grid_columnconfigure(0, weight=1)

        # ── Collapsible header bar ──
        toggle_bar = ctk.CTkFrame(self._stats_outer, fg_color="transparent",
                                   cursor="hand2")
        toggle_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        toggle_bar.bind("<Button-1>", lambda e: self._toggle_stats_panel())

        ctk.CTkLabel(toggle_bar, text="WARMUP ANALYTICS",
                     font=("Segoe UI", 13, "bold"),
                     text_color=("#0F766E", "#14B8A6")).pack(side="left")

        self._stats_summary_label = ctk.CTkLabel(
            toggle_bar, text="",
            font=("Segoe UI", 11),
            text_color=("#6B7280", "#94A3B8"))
        self._stats_summary_label.pack(side="left", padx=(12, 0))

        self._stats_chevron = ctk.CTkLabel(
            toggle_bar, text="\u25BC",
            font=("Segoe UI", 11),
            text_color=("#6B7280", "#94A3B8"))
        self._stats_chevron.pack(side="right")

        ctk.CTkButton(
            toggle_bar, text="Refresh", width=56, height=22,
            fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
            font=("Segoe UI", 10),
            command=self._refresh_stats_panel
        ).pack(side="right", padx=(0, 8))

        # ── Expandable content frame (hidden by default) ──
        self._stats_expanded = False
        self._stats_frame = ctk.CTkFrame(self._stats_outer, fg_color="transparent")
        self._stats_frame.grid_columnconfigure(0, weight=1)
        # NOT gridded yet -- starts collapsed

        # ── Hero KPI row: 4 large metric cards ──
        hero = ctk.CTkFrame(self._stats_frame, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for i in range(4):
            hero.grid_columnconfigure(i, weight=1, uniform="kpi")

        self._kpi_cards = {}
        kpi_defs = [
            ("progress", "TODAY'S PROGRESS", "--",  "+0 today",  ACCENT),
            ("avg_day",  "AVG WARMUP DAY",   "--",  "Phase: --", "#3B82F6"),
            ("actions",  "TOTAL ACTIONS",    "0",   "+0 today",  "#22C55E"),
            ("banned",   "BANNED",           "0",   "",          "#6B7280"),
        ]
        for i, (key, label, default_val, default_delta, accent) in enumerate(kpi_defs):
            card = ctk.CTkFrame(hero, fg_color=CARD_FG, corner_radius=8,
                                border_width=1, border_color=("#D1D5DB", "#2D3748"),
                                height=100)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 3, 0 if i == 3 else 3))
            card.pack_propagate(False)

            # Left accent strip
            strip = ctk.CTkFrame(card, width=4, corner_radius=0, fg_color=accent)
            strip.pack(side="left", fill="y")

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)

            lbl = ctk.CTkLabel(inner, text=label,
                               font=("Segoe UI", 11),
                               text_color=("#6B7280", "#94A3B8"), anchor="w")
            lbl.pack(anchor="w")

            val = ctk.CTkLabel(inner, text=default_val,
                               font=("Segoe UI", 28, "bold"),
                               text_color=("#111827", "#F9FAFB"), anchor="w")
            val.pack(anchor="w", pady=(2, 0))

            delta = ctk.CTkLabel(inner, text=default_delta,
                                 font=("Segoe UI", 12),
                                 text_color="#22C55E", anchor="w")
            delta.pack(anchor="w")

            self._kpi_cards[key] = {"card": card, "strip": strip,
                                     "val": val, "delta": delta}

        # ── Header row for account cards ──
        acct_header = ctk.CTkFrame(self._stats_frame, fg_color="transparent")
        acct_header.grid(row=1, column=0, sticky="ew", pady=(2, 2))

        ctk.CTkLabel(acct_header, text="ACCOUNT WARMUP PROGRESS",
                     font=("Segoe UI", 13, "bold"),
                     text_color=("#0F766E", "#14B8A6")).pack(side="left")

        self._nsfw_ready_label = ctk.CTkLabel(
            acct_header, text="",
            font=("Segoe UI", 11, "bold"),
            text_color="#22C55E")
        self._nsfw_ready_label.pack(side="right", padx=(0, 4))

        # ── Scrollable account card list ──
        self._acct_cards_frame = ctk.CTkScrollableFrame(
            self._stats_frame, height=300,
            fg_color=("#F1F5F9", "#0F172A"), corner_radius=6)
        self._acct_cards_frame.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 4))
        self._acct_cards_frame.grid_columnconfigure(0, weight=1)

        # Load data after GUI is ready (updates summary even when collapsed)
        self.app.after(300, self._refresh_stats_panel)

    @staticmethod
    def _clean_adspower_name(raw_name):
        """Extract Reddit username from AdsPower profile name.

        Handles formats like:
          'P honeytonedhaze'           -> 'honeytonedhaze'
          'G - reddit bot - Username'  -> 'Username'
          'F - reddit bot -Username'   -> 'Username'
          'honeytonedhaze'             -> 'honeytonedhaze'
        """
        name = (raw_name or "").strip()
        # Strip proxy group prefix
        for pfx in PROXY_PREFIXES:
            if name.startswith(pfx):
                name = name[len(pfx):].strip()
                break
        # Strip "- reddit bot -" or similar middle segments
        if " - " in name:
            name = name.rsplit(" - ", 1)[-1].strip()
        elif name.startswith("- "):
            name = name.lstrip("- ").strip()
        # If still starts with "reddit bot" junk, strip it
        if name.lower().startswith("reddit bot"):
            name = name[len("reddit bot"):].lstrip(" -").strip()
        return name or raw_name

    def _build_adspower_name_cache(self, needed_ids=None):
        """Query AdsPower API to build adspower_id -> reddit username cache.

        Args:
            needed_ids: Optional set of profile IDs still unmapped after
                        other tiers. Triggers individual lookups for each.
        """
        if hasattr(self, '_ads_name_cache') and self._ads_name_cache:
            # Individual lookups for any IDs missing from cache
            if needed_ids:
                self._individual_lookups(needed_ids)
            return self._ads_name_cache

        self._ads_name_cache = {}
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        page_num = 1

        while page_num <= 10:  # safety cap
            for attempt in range(3):
                try:
                    resp = requests.get(
                        f"{api_base}/api/v1/user/list",
                        params={"api_key": api_key, "page": page_num,
                                "page_size": 100, "group_id": 0},
                        timeout=10)
                    data = resp.json()
                    if data.get("code") == 0:
                        break
                    time.sleep(1)  # rate limit retry
                except Exception:
                    data = None
                    time.sleep(1)
            else:
                break  # 3 failed attempts

            if not data or data.get("code") != 0:
                break

            items = data.get("data", {}).get("list", [])
            if not items:
                break

            for item in items:
                uid = item.get("user_id", "")
                clean = self._clean_adspower_name(item.get("name", ""))
                if uid and clean:
                    self._ads_name_cache[uid] = clean

            if len(items) < 100:
                break  # last page
            page_num += 1

        # Individual lookups for IDs missed by the paginated list
        if needed_ids:
            self._individual_lookups(needed_ids)

        return self._ads_name_cache

    def _individual_lookups(self, needed_ids):
        """Look up individual profile IDs via AdsPower API."""
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        for pid in list(needed_ids)[:20]:  # cap at 20
            if pid in self._ads_name_cache:
                continue
            try:
                resp = requests.get(
                    f"{api_base}/api/v1/user/list",
                    params={"api_key": api_key, "user_id": pid},
                    timeout=5)
                data = resp.json()
                items = (data.get("data") or {}).get("list") or []
                if items:
                    name = self._clean_adspower_name(
                        items[0].get("name", ""))
                    if name:
                        self._ads_name_cache[pid] = name
            except Exception:
                pass
            time.sleep(0.3)

    def _toggle_stats_panel(self):
        """Expand or collapse the full analytics panel."""
        self._stats_expanded = not self._stats_expanded
        if self._stats_expanded:
            self._stats_chevron.configure(text="\u25B2")
            self._stats_frame.grid(row=1, column=0, sticky="ew",
                                    padx=4, pady=(0, 6))
            self._refresh_stats_panel()
        else:
            self._stats_chevron.configure(text="\u25BC")
            self._stats_frame.grid_forget()

    def _refresh_stats_panel(self):
        """Kick off a background thread to fetch data, then update UI."""
        threading.Thread(target=self._fetch_stats_data, daemon=True).start()

    def _fetch_stats_data(self):
        """Background thread: fetch all data (DB + API), then hand to main thread."""
        self._ads_name_cache = {}
        try:
            all_stats = get_all_warmup_stats()
            warmed_today = get_warmed_today_ids()
            today_sessions = get_today_sessions()
        except Exception as e:
            logger.error(f"Stats panel refresh failed: {e}")
            return

        ban_log = self._load_ban_log()

        # Build username map (includes AdsPower API calls)
        username_map = {}
        all_profile_ids = set(s["profile_id"] for s in all_stats)

        for key, profile in self.persona_profiles.items():
            ads_id = (profile.get("adspower_id") or "").strip()
            if ads_id:
                uname = profile.get("reddit_account", {}).get("username", key)
                username_map[ads_id] = uname

        for ads_id, name in self.profiles_data.items():
            if ads_id not in username_map:
                clean = name
                for pfx in PROXY_PREFIXES:
                    if name.startswith(pfx):
                        clean = name[len(pfx):]
                        break
                username_map[ads_id] = clean

        still_needed = all_profile_ids - set(username_map.keys())
        ads_cache = self._build_adspower_name_cache(
            needed_ids=still_needed if still_needed else None)
        for ads_id, name in ads_cache.items():
            if ads_id not in username_map:
                username_map[ads_id] = name

        today_map = {}
        for ts in today_sessions:
            today_map[ts["profile_id"]] = ts

        # Hand results to main thread for UI update
        self.app.after(0, self._apply_stats_data,
                       all_stats, warmed_today, today_sessions,
                       today_map, ban_log, username_map)

    def _apply_stats_data(self, all_stats, warmed_today, today_sessions,
                          today_map, ban_log, username_map):
        """Main thread: update all UI widgets with fetched data."""
        banned_ids = {k for k, v in ban_log.items()
                      if v.get("status") == "permaban"}
        active_stats = [s for s in all_stats
                        if s["profile_id"] not in banned_ids]

        n_active = len(active_stats)
        n_today = len(warmed_today - banned_ids)
        n_banned = len(banned_ids)

        days_list = []
        for s in active_stats:
            try:
                started = datetime.fromisoformat(s["started_at"])
                days_list.append((datetime.now() - started).days + 1)
            except Exception:
                pass
        avg_day = round(sum(days_list) / len(days_list)) if days_list else 0

        total_cmt_all = sum(s.get("total_comments", 0) for s in all_stats)
        total_join_all = sum(s.get("total_joins", 0) for s in all_stats)
        total_actions_all = total_cmt_all + total_join_all

        cmt_today = sum(t.get("comments", 0) for t in today_sessions)
        join_today = sum(t.get("joins", 0) for t in today_sessions)
        actions_today = cmt_today + join_today

        # Card 1: Today's Progress
        self._kpi_cards["progress"]["val"].configure(
            text=f"{n_today} / {n_active}")
        self._kpi_cards["progress"]["delta"].configure(
            text="accounts warmed",
            text_color="#22C55E" if n_today >= n_active and n_active > 0 else "#94A3B8")

        # Card 2: Avg Warmup Day
        phase = self._get_phase(avg_day)
        self._kpi_cards["avg_day"]["val"].configure(text=str(avg_day))
        self._kpi_cards["avg_day"]["delta"].configure(
            text=f"Phase: {phase['name']}", text_color=phase["fill"])
        self._kpi_cards["avg_day"]["strip"].configure(fg_color=phase["fill"])

        # Card 3: Total Actions
        self._kpi_cards["actions"]["val"].configure(text=str(total_actions_all))
        self._kpi_cards["actions"]["delta"].configure(
            text=f"+{actions_today} today" if actions_today else "no activity today",
            text_color="#22C55E" if actions_today else "#94A3B8")

        # Card 4: Banned
        self._kpi_cards["banned"]["val"].configure(text=str(n_banned))
        self._kpi_cards["banned"]["strip"].configure(
            fg_color="#EF4444" if n_banned > 0 else "#6B7280")
        if n_banned > 0:
            banned_names = [username_map.get(bid, bid[:8])
                            for bid in list(banned_ids)[:2]]
            self._kpi_cards["banned"]["delta"].configure(
                text=", ".join(banned_names), text_color="#EF4444")
        else:
            self._kpi_cards["banned"]["delta"].configure(
                text="all clear", text_color="#22C55E")

        # NSFW-ready count
        nsfw_ready = sum(1 for d in days_list if d >= 14)
        self._nsfw_ready_label.configure(
            text=f"{nsfw_ready}/{n_active} NSFW-ready" if n_active else "")

        # Update collapsed summary line
        summary_parts = [f"{n_today}/{n_active} warmed today"]
        if avg_day:
            summary_parts.append(f"Day {avg_day} avg")
        if n_banned:
            summary_parts.append(f"{n_banned} banned")
        if actions_today:
            summary_parts.append(f"+{actions_today} actions today")
        self._stats_summary_label.configure(
            text="  |  ".join(summary_parts))

        # Rebuild account cards (only if expanded)
        if not self._stats_expanded:
            return
        self._rebuild_account_cards(
            all_stats, warmed_today, today_map, ban_log, username_map)

    def _rebuild_account_cards(self, all_stats, warmed_today, today_map,
                                ban_log, username_map):
        """Rebuild all account cards in the scrollable frame."""
        for w in self._acct_cards_frame.winfo_children():
            w.destroy()

        banned_ids = {k for k, v in ban_log.items()
                      if v.get("status") == "permaban"}
        today_str = datetime.now().strftime("%Y-%m-%d")

        # Build row data
        rows = []
        for s in all_stats:
            pid = s["profile_id"]
            is_banned = pid in banned_ids
            is_done = pid in warmed_today

            try:
                started = datetime.fromisoformat(s["started_at"])
                day = (datetime.now() - started).days + 1
            except Exception:
                day = 0

            last_date = s.get("last_activity_date") or ""
            if last_date == today_str:
                last_run = "today"
            elif last_date:
                try:
                    delta = (datetime.now()
                             - datetime.strptime(last_date, "%Y-%m-%d")).days
                    last_run = "yesterday" if delta == 1 else f"{delta}d ago"
                except Exception:
                    last_run = last_date
            else:
                last_run = "never"

            # Status + sort priority
            if is_banned:
                status, sk = "banned", 4
            elif is_done:
                status, sk = "done", 2
            elif day > 0:
                status, sk = "pending", 1
            else:
                status, sk = "idle", 3

            ts = today_map.get(pid, {})
            rows.append({
                "pid": pid, "sk": sk, "status": status,
                "user": username_map.get(pid, pid[:12]),
                "day": day,
                "cmt_today": ts.get("comments", 0),
                "up_today": ts.get("upvotes", 0),
                "join_today": ts.get("joins", 0),
                "cmt_all": s.get("total_comments", 0),
                "up_all": s.get("total_upvotes", 0),
                "join_all": s.get("total_joins", 0),
                "last_run": last_run,
            })

        rows.sort(key=lambda r: (r["sk"], r["user"].lower()))
        visible = [r for r in rows if r["status"] != "banned"]
        banned_rows = [r for r in rows if r["status"] == "banned"]

        for i, r in enumerate(visible):
            self._build_account_card(self._acct_cards_frame, r, i)

        # Banned section at bottom
        if banned_rows:
            sep = ctk.CTkFrame(self._acct_cards_frame, height=1,
                               fg_color=("#D1D5DB", "#334155"))
            sep.grid(row=len(visible), column=0, sticky="ew",
                     padx=8, pady=(8, 4))
            ctk.CTkLabel(
                self._acct_cards_frame,
                text=f"{len(banned_rows)} BANNED",
                font=("Segoe UI", 11, "bold"), text_color="#EF4444",
            ).grid(row=len(visible) + 1, column=0, sticky="w",
                   padx=8, pady=(0, 2))
            for j, r in enumerate(banned_rows):
                self._build_account_card(
                    self._acct_cards_frame, r,
                    len(visible) + 2 + j, compact=True)

    def _build_account_card(self, parent, r, grid_row, compact=False):
        """Build one account card with status strip, phase bar, stats."""
        sc = self.STATUS_COLORS.get(r["status"], self.STATUS_COLORS["idle"])
        phase = self._get_phase(r["day"])

        card = ctk.CTkFrame(parent, fg_color=sc["bg"], corner_radius=8,
                            height=80 if not compact else 40)
        card.grid(row=grid_row, column=0, sticky="ew", padx=4, pady=2)
        card.grid_propagate(False)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(0, weight=1)

        # Left status strip (4px colored bar)
        strip = ctk.CTkFrame(card, width=4, corner_radius=0,
                              fg_color=sc["strip"])
        strip.grid(row=0, column=0, rowspan=3 if not compact else 1,
                   sticky="ns", padx=(0, 0), pady=0)

        if compact:
            # Banned accounts: just username + status
            ctk.CTkLabel(
                card, text=f"\u2717  {r['user']}",
                font=("Segoe UI", 11),
                text_color="#EF4444", anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=12, pady=4)
            return

        # ── Row 0: Identity + phase badge + milestone + last run ──
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=1, sticky="ew", padx=(10, 8), pady=(6, 0))

        # Status dot
        dot_map = {"done": ("\u2713", "#22C55E"), "pending": ("\u25CF", "#3B82F6"),
                   "stale": ("\u25CF", "#F59E0B"), "idle": ("\u25CB", "#6B7280")}
        dot_char, dot_col = dot_map.get(r["status"], ("\u25CB", "#6B7280"))
        ctk.CTkLabel(header, text=dot_char, font=("Segoe UI", 12, "bold"),
                     text_color=dot_col, width=16).pack(side="left")

        # Username
        ctk.CTkLabel(header, text=r["user"][:18],
                     font=("Segoe UI", 13, "bold"),
                     text_color=("#1F2937", "#E5E7EB"),
                     anchor="w").pack(side="left", padx=(2, 8))

        # Day counter
        ctk.CTkLabel(header, text=f"Day {r['day']}",
                     font=("Segoe UI", 11),
                     text_color=phase["text"]).pack(side="left", padx=(0, 4))

        # Phase pill badge
        pill = ctk.CTkFrame(header, fg_color=phase["badge"],
                            corner_radius=8, width=60, height=20)
        pill.pack_propagate(False)
        pill.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(pill, text=phase["name"],
                     font=("Segoe UI", 9, "bold"),
                     text_color=phase["fill"]).pack(expand=True)

        # Milestone badge (NSFW unlock at day 14)
        if r["day"] >= 14:
            m_text, m_color = "NSFW \u2713", "#22C55E"
        elif r["day"] >= 11:
            m_text = f"NSFW in {14 - r['day']}d"
            m_color = "#FBBF24"
        else:
            m_text, m_color = "", ""

        if m_text:
            mbadge = ctk.CTkFrame(header,
                                   fg_color=("#1C1917", "#1C1917") if m_color == "#FBBF24"
                                   else ("#052E16", "#052E16"),
                                   corner_radius=6, width=72, height=18)
            mbadge.pack_propagate(False)
            mbadge.pack(side="left")
            ctk.CTkLabel(mbadge, text=m_text, font=("Segoe UI", 8, "bold"),
                         text_color=m_color).pack(expand=True)

        # Last run (right-aligned)
        ctk.CTkLabel(header, text=r["last_run"],
                     font=("Segoe UI", 10),
                     text_color=("#6B7280", "#94A3B8")).pack(side="right")

        # ── Row 1: Phase progress bar (segmented) ──
        bar_frame = ctk.CTkFrame(card, fg_color="transparent", height=16)
        bar_frame.grid(row=1, column=1, sticky="ew", padx=(12, 10), pady=(2, 0))
        bar_frame.pack_propagate(False)

        for p in self.PHASE_DEFS:
            day_start, day_end = p["days"]
            span = day_end - day_start + 1
            if r["day"] > day_end:
                fill = 1.0
            elif r["day"] >= day_start:
                fill = (r["day"] - day_start) / span
            else:
                fill = 0.0

            seg = ctk.CTkFrame(bar_frame, fg_color=p["bg"],
                               corner_radius=3, height=12)
            seg.pack(side="left", expand=True, fill="x", padx=(0, 1))
            seg.pack_propagate(False)

            if fill > 0:
                inner_fill = ctk.CTkFrame(seg, fg_color=p["fill"],
                                           corner_radius=2)
                inner_fill.place(relx=0, rely=0.08, relwidth=max(0.04, fill),
                                 relheight=0.84)

        # ── Row 2: Today stats + All-time stats ──
        stats_row = ctk.CTkFrame(card, fg_color="transparent")
        stats_row.grid(row=2, column=1, sticky="ew", padx=(12, 10), pady=(2, 6))

        # Today
        today_color = "#4ADE80" if r["status"] == "done" else ("#94A3B8", "#64748B")
        ctk.CTkLabel(stats_row, text="TODAY",
                     font=("Segoe UI", 9, "bold"),
                     text_color=("#888899", "#888899")).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(stats_row, text=f"{r['cmt_today']}c  {r['up_today']}v  {r['join_today']}j",
                     font=("Segoe UI", 11, "bold"),
                     text_color=today_color).pack(side="left", padx=(0, 16))

        # Separator
        ctk.CTkFrame(stats_row, width=1, height=14,
                     fg_color=("#D1D5DB", "#334155")).pack(side="left", padx=(0, 16))

        # All-time
        ctk.CTkLabel(stats_row, text="ALL",
                     font=("Segoe UI", 9, "bold"),
                     text_color=("#888899", "#888899")).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(stats_row,
                     text=f"{r['cmt_all']}c  {r['up_all']}v  {r['join_all']}j",
                     font=("Segoe UI", 11),
                     text_color=("#374151", "#CBD5E1")).pack(side="left")

    def _open_persona_config(self):
        """Open the persona config file in the default text editor."""
        path = os.path.abspath(ACCOUNT_PROFILES_PATH)
        if not os.path.exists(path):
            messagebox.showwarning("File Not Found", f"Persona config not found:\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", path])
            else:
                import subprocess
                subprocess.run(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open file:\n{e}")

    def _reload_persona(self):
        """Reload persona profiles from disk and refresh the display."""
        self._load_adspower_config()
        self._load_persona_profiles()
        self._refresh_account_options()
        self._log("Persona config reloaded.")

    def _remove_selected_account(self):
        """Remove the currently selected account from AdsPower config and persona profiles."""
        selected = self.acct_var.get()
        if selected not in self._acct_map:
            messagebox.showwarning("No Account", "Select an account to remove first.")
            return

        ads_id, persona_key = self._acct_map[selected]
        username = selected.split(" (")[0] if " (" in selected else selected

        if not messagebox.askyesno(
            "Remove Account",
            f"Remove '{username}' ({ads_id}) from the config?\n\n"
            "This removes the account from the dropdown and config files. "
            "It does NOT delete the AdsPower browser profile.",
        ):
            return

        # Remove from adspower_config.json
        try:
            config = dict(self.adspower_config or {})
            profiles = [
                p for p in config.get("profiles", [])
                if p.get("profile_id") != ads_id
            ]
            config["profiles"] = profiles
            with open(ADSPOWER_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            self.adspower_config = config
            self.profiles_data = {
                p.get("profile_id", ""): p.get("account_name", "")
                for p in profiles if p.get("profile_id")
            }
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update AdsPower config:\n{e}")
            return

        # Remove from account_profiles.json if present
        if persona_key and persona_key in self.persona_profiles:
            try:
                manager = ProfileManager(ACCOUNT_PROFILES_PATH)
                if persona_key in manager.profiles:
                    del manager.profiles[persona_key]
                    manager.save()
            except Exception as e:
                self._log(f"Warning: could not remove persona profile: {e}")

        self._load_persona_profiles()
        self._refresh_account_options()
        self._log(f"Removed account: {username} ({ads_id})")

    def _open_add_account_dialog(self):
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Add Reddit Account")
        dialog.geometry("520x420")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            dialog,
            text="Add/Update Reddit Account",
            font=("Segoe UI", 16, "bold"),
            text_color=("#0F172A", "#E5E7EB"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8))

        fields = [
            ("Reddit Username*", "username", ""),
            ("AdsPower ID*", "adspower_id", ""),
            ("Display Name", "display_name", ""),
            ("Age Days", "age_days", "0"),
            ("Location", "location", ""),
            ("Hobbies (comma)", "hobbies", ""),
            ("Interests (comma)", "interests", ""),
        ]
        entries = {}
        for row, (label, key, default) in enumerate(fields, start=1):
            ctk.CTkLabel(dialog, text=label, font=("Segoe UI", 13, "bold")).grid(
                row=row, column=0, sticky="w", padx=16, pady=6
            )
            entry = ctk.CTkEntry(dialog)
            if default:
                entry.insert(0, default)
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=6)
            entries[key] = entry

        help_label = ctk.CTkLabel(
            dialog,
            text="Creates/updates config/account_profiles.json and syncs src/uploaders/redgifs/adspower_config.json.",
            font=("Segoe UI", 13),
            text_color=("#64748B", "#D1D5DB"),
            justify="left",
        )
        help_label.grid(row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 6))
        _auto_wrap(help_label)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.grid(row=10, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 12))
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        def _save():
            username = entries["username"].get().strip()
            adspower_id = entries["adspower_id"].get().strip()
            display_name = entries["display_name"].get().strip() or username
            age_raw = entries["age_days"].get().strip() or "0"
            location = entries["location"].get().strip()
            hobbies = [x.strip() for x in entries["hobbies"].get().split(",") if x.strip()]
            interests = [x.strip() for x in entries["interests"].get().split(",") if x.strip()]

            if not username or not adspower_id:
                messagebox.showerror("Missing Fields", "Username and AdsPower ID are required.")
                return
            try:
                age_days = max(0, int(age_raw))
            except ValueError:
                messagebox.showerror("Invalid Age", "Age Days must be a whole number.")
                return

            manager = ProfileManager(ACCOUNT_PROFILES_PATH)
            existing = None
            for profile in manager.get_all_profiles():
                if profile.adspower_id == adspower_id or profile.reddit_account.username.lower() == username.lower():
                    existing = profile
                    break

            profile_id = existing.profile_id if existing else self._slugify_profile_id(username)
            if not existing and profile_id in manager.profiles:
                suffix = 2
                while f"{profile_id}_{suffix}" in manager.profiles:
                    suffix += 1
                profile_id = f"{profile_id}_{suffix}"

            attributes = existing.attributes if existing else AccountAttributes(age=25)
            persona = existing.persona if existing else PersonaInterests()
            persona.location = location
            if hobbies:
                persona.hobbies = hobbies
            if interests:
                persona.interests = interests

            reddit_account = existing.reddit_account if existing else RedditAccount(username=username)
            reddit_account.username = username
            reddit_account.age_days = age_days

            new_profile = AccountProfile(
                profile_id=profile_id,
                display_name=display_name,
                attributes=attributes,
                adspower_id=adspower_id,
                persona=persona,
                title_templates=existing.title_templates if existing else {"default": "{title}"},
                flair_mappings=existing.flair_mappings if existing else {},
                content_tags=existing.content_tags if existing else [],
                reddit_account=reddit_account,
                created_at=existing.created_at if existing else datetime.now().isoformat(),
            )
            manager.add_profile(new_profile)
            self._persist_adspower_mapping(adspower_id, username)
            self._load_persona_profiles()
            self._refresh_account_options(preferred_ads_id=adspower_id)
            self._log(f"Account saved: {username} ({adspower_id})")
            dialog.destroy()

        ctk.CTkButton(
            btn_frame,
            text="Save Account",
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=("Segoe UI", 14, "bold"),
            command=_save,
        ).grid(row=0, column=0, sticky="ew", padx=5)
        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            height=34,
            fg_color="#475569",
            hover_color="#334155",
            font=("Segoe UI", 13),
            command=dialog.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=5)

    def _on_account_change(self, selection):
        """Handle account dropdown change â€” update hidden profile_entry and persona info."""
        if selection not in self._acct_map:
            return

        ads_id, persona_key = self._acct_map[selection]

        # Update hidden profile entry (used by _start_warmup and _refresh_stats)
        self.profile_entry.delete(0, "end")
        self.profile_entry.insert(0, ads_id)

        # Store the persona key for _start_warmup
        self.persona_var.set(persona_key)

        # Build human-readable persona summary
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

        # Auto-refresh stats for the new account
        self._refresh_stats(silent=True)

    _LOG_MAX_LINES = 3000

    def _log(self, msg):
        # Write to file log first (survives crashes)
        logger.info(msg)
        tag = _detect_log_tag(msg)
        if tag:
            self.log_box.insert("end", f"{msg}\n", tag)
        else:
            self.log_box.insert("end", f"{msg}\n")
        # Trim to prevent RAM bloat during multi-day runs
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > self._LOG_MAX_LINES:
            self.log_box.delete("1.0", f"{line_count - self._LOG_MAX_LINES}.0")
        self.log_box.see("end")

    def _refresh_stats(self, silent=False):
        """Read warmup stats from DB and update the status panel."""
        profile_id = self.profile_entry.get().strip()
        if not profile_id:
            if not silent:
                messagebox.showwarning("No Profile", "Select an account first.")
            return

        try:
            status = get_warmup_status(profile_id)
        except Exception as e:
            logger.error(f"Failed to load warmup stats: {e}")
            status = None

        if not status:
            self.day_label.configure(text="Account Age: --")
            self.status_val_label.configure(text="not started")
            self.upvotes_label.configure(text="0 upvotes")
            self.comments_label.configure(text="0 comments")
            self.joins_label.configure(text="0 joins")
            self.posts_label.configure(text="0 posts")
            return

        day = get_warmup_day(profile_id)
        self.day_label.configure(text=f"Account Age: {day}d")
        self.status_val_label.configure(text=f"{status.get('status', '--')}")
        self.upvotes_label.configure(text=f"{status.get('total_upvotes', 0)} upvotes")
        self.comments_label.configure(text=f"{status.get('total_comments', 0)} comments")
        self.joins_label.configure(text=f"{status.get('total_joins', 0)} joins")
        self.posts_label.configure(text=f"{status.get('total_posts', 0)} posts")

    def _start_warmup(self):
        if self._run_all_active:
            messagebox.showwarning("Busy", "Run All is active. Stop it first.")
            return
        profile_id = self.profile_entry.get().strip()
        if not profile_id:
            messagebox.showerror("Error", "Select a Reddit account first.")
            return

        grok_key = self.grok_key_entry.get().strip()

        # Resolve persona from account selection
        persona = None
        account_age_days = None
        account_created_at = None
        persona_key = self.persona_var.get()
        if persona_key and persona_key in self.persona_profiles:
            profile_data = self.persona_profiles[persona_key]
            persona = profile_data.get("persona")
            account_created_at = profile_data.get("created_at")
            try:
                raw_age = (profile_data.get("reddit_account", {}) or {}).get("age_days")
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

        # Read poetry warmup settings
        poetry_warmup = self.poetry_var.get()
        try:
            karma_target = int(self.karma_target_entry.get().strip() or "1500")
        except ValueError:
            karma_target = 1500

        # Disable buttons and reset session display
        mode_label = "Poetry" if poetry_warmup else "Running"
        self.start_btn.configure(state="disabled", text=f"{mode_label}: {profile_id[:12]}...")
        self.stop_btn.configure(state="normal")
        self.refresh_btn.configure(state="disabled")
        self.is_running = True
        self._stop_requested = False
        self.log_box.delete("1.0", "end")
        self._reset_session_stats()

        thread = threading.Thread(
            target=self._warmup_worker,
            args=(profile_id, persona, grok_key, account_age_days, account_created_at,
                  poetry_warmup, karma_target),
            daemon=True,
        )
        thread.start()

    def _stop_warmup(self):
        self._stop_requested = True
        if self.warmer:
            self.warmer.stop_requested = True
            self._log("Stop requested â€” will halt after current cycle...")
        else:
            self._log("Stop requested â€” aborting startup...")
        self.stop_btn.configure(state="disabled")

    def _warmup_worker(self, profile_id, persona, grok_key,
                       account_age_days=None, account_created_at=None,
                       poetry_warmup=False, karma_target=1500):
        """Background thread: connect browser, run warmup, report results."""
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        stats = None
        browser_started = False

        # 1. Start AdsPower browser
        self.app.after(0, self._log, f"Starting AdsPower profile {profile_id}...")
        try:
            resp = requests.get(
                f"{api_base}/api/v1/browser/start"
                f"?user_id={profile_id}&api_key={api_key}",
                timeout=60,
            )
            try:
                data = resp.json()
            except Exception as e:
                stats = _warmup_fail_stats(
                    "adspower_bad_response",
                    f"browser/start returned invalid JSON ({e})",
                )
                self.app.after(0, self._log,
                               f"AdsPower error: invalid JSON response: {e}")
                return
            if not isinstance(data, dict):
                stats = _warmup_fail_stats(
                    "adspower_bad_response",
                    f"browser/start returned non-dict JSON ({type(data).__name__})",
                )
                self.app.after(0, self._log,
                               f"AdsPower error: unexpected response payload: {data!r}")
                return
            if data.get("code") != 0:
                stats = _warmup_fail_stats("adspower_start_failed", str(data))
                self.app.after(0, self._log, f"AdsPower error: {data}")
                return
            # Treat code=0 as browser started even if CDP endpoint is missing,
            # so the finally block attempts a cleanup stop.
            browser_started = True
            ws_endpoint, cdp_reason, cdp_detail = _adspower_cdp_endpoint(data)
            if cdp_reason:
                stats = _warmup_fail_stats(cdp_reason, cdp_detail)
                self.app.after(0, self._log, f"AdsPower error: {cdp_detail}")
                return
            if self._stop_requested:
                stats = _warmup_fail_stats(
                    "stopped",
                    "stop requested after browser start before Playwright connect",
                )
                self.app.after(0, self._log,
                               "Warmup stopped before Playwright connect")
                return
            self.app.after(0, self._log, "Browser started, connecting Playwright...")

            # 2. Connect Playwright
            from playwright.sync_api import sync_playwright
            from core.account_warmer import AccountWarmer, warmup_stats_ok

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(ws_endpoint)
                contexts = browser.contexts
                if contexts:
                    ctx = contexts[0]
                else:
                    ctx = browser.new_context()
                all_pages = list(ctx.pages) if ctx else []
                def _page_is_closed(pg):
                    try:
                        return bool(pg.is_closed())
                    except Exception:
                        return False
                page = None
                closed = 0
                for pg in all_pages:
                    try:
                        url = pg.url or ""
                    except Exception:
                        url = ""
                    if "reddit.com" in url and not _page_is_closed(pg):
                        page = pg
                        break
                if not page:
                    for pg in all_pages:
                        if not _page_is_closed(pg):
                            page = pg
                            break
                if page is None:
                    page = ctx.new_page()
                for pg in all_pages:
                    if pg is page:
                        continue
                    try:
                        pg.close()
                        closed += 1
                    except Exception:
                        pass
                if page is None or _page_is_closed(page):
                    raise RuntimeError("No live browser page is available for warmup")

                tab_msg = "Playwright connected"
                if closed:
                    tab_msg += f" (closed {closed} stale tab{'s' if closed != 1 else ''})"
                self.app.after(0, self._log, tab_msg)
                if self._stop_requested:
                    stats = {
                        "ok": False,
                        "failure_reason": "stopped",
                        "failure_detail": "stop requested before session start",
                    }
                    self.app.after(0, self._log, "Warmup stopped before session start")
                    return

                # 3. Create warmer
                warmer = AccountWarmer(
                    profile_id, page,
                    persona=persona,
                    grok_api_key=grok_key,
                    account_age_days=account_age_days,
                    account_created_at=account_created_at,
                    poetry_warmup=poetry_warmup,
                    karma_target=karma_target)
                self.warmer = warmer
                if self._stop_requested:
                    warmer.stop_requested = True

                day = warmer.get_day()
                max_posts = warmer.get_max_posts_today()
                self.app.after(0, self._log,
                    f"Day {day}, max posts today: {max_posts}, "
                    f"{len(warmer.general_subs)} general subs")
                self.app.after(0, self.progress_label.configure,
                    text=f"Day {day} â€” warmup in progress...")

                # 4. Attach log handler to capture warmer output
                warmer_logger = logging.getLogger("core.account_warmer")
                handler = _GUILogHandler(self.log_box, self.app)
                handler.setFormatter(logging.Formatter(
                    f"%(asctime)s [{profile_id}] %(message)s", datefmt="%H:%M:%S"))
                my_thread = threading.current_thread().ident
                handler.addFilter(
                    type("_TF", (logging.Filter,),
                         {"filter": lambda self, r, tid=my_thread: r.thread == tid})()
                )
                warmer_logger.addHandler(handler)
                self._log_handler = handler

                try:
                    # 5. Run warmup
                    start = time.time()
                    stats = warmer.run_daily_warmup()
                    elapsed = int(time.time() - start)
                finally:
                    # 6. Cleanup handler
                    try:
                        warmer_logger.removeHandler(handler)
                    except Exception:
                        pass
                    self._log_handler = None

                # 7. Report
                if warmup_stats_ok(stats):
                    self.app.after(0, self._log,
                        f"\n=== WARMUP COMPLETE ===\n"
                        f"Sessions: {stats['sessions']}\n"
                        f"Time: {elapsed // 60}m {elapsed % 60}s\n"
                        f"Scrolls: {stats['scrolls']}\n"
                        f"Upvotes: {stats['upvotes']}, "
                        f"Downvotes: {stats['downvotes']}\n"
                        f"Comments: {stats['comments']}\n"
                        f"Joins: {stats['joins']}\n"
                         f"Posts clicked: {stats['posts_clicked']}\n"
                         f"Subs browsed: {stats['subs_browsed']}")
                else:
                    if isinstance(stats, dict) and stats.get("failure_reason") == "stopped":
                        self.app.after(0, self._log,
                                       f"Warmup stopped: {_warmup_failure_text(stats)}")
                    else:
                        self.app.after(0, self._log,
                                       f"Warmup failed before session start: {_warmup_failure_text(stats)}")

        except Exception as e:
            if stats is None:
                if self._stop_requested:
                    stats = _warmup_fail_stats("stopped", "stop requested during warmup startup")
                elif not browser_started:
                    stats = _warmup_fail_stats("adspower_start_error", str(e))
                else:
                    stats = _warmup_fail_stats("warmup_worker_error", str(e))
            self.app.after(0, self._log, f"Warmup error: {e}")
        finally:
            # Clean up handler if it was attached
            if self._log_handler:
                try:
                    logging.getLogger("core.account_warmer").removeHandler(
                        self._log_handler)
                except Exception:
                    pass
                self._log_handler = None

            # Always attempt to close the AdsPower profile when started
            if browser_started:
                try:
                    requests.get(
                        f"{api_base}/api/v1/browser/stop"
                        f"?user_id={profile_id}&api_key={api_key}",
                        timeout=15,
                    )
                    self.app.after(0, self._log, "AdsPower profile stopped")
                except Exception as stop_err:
                    self.app.after(0, self._log, f"Failed to stop AdsPower profile: {stop_err}")

            self.app.after(0, self._on_complete, stats)

    def _reset_session_stats(self):
        """Clear the session stats panel (called at start of new run)."""
        self.sess_time_label.configure(text="running...")
        for key in self._pill_refs:
            self._pill_refs[key].configure(text="0")

    def _update_session_stats(self, stats):
        """Populate session pill badges from warmer results dict."""
        if not stats:
            return
        elapsed = stats.get("total_sec", 0)
        self.sess_time_label.configure(
            text=f"{elapsed // 60}m {elapsed % 60}s")
        pill_map = {
            "upvotes": stats.get("upvotes", 0),
            "downvotes": stats.get("downvotes", 0),
            "comments": stats.get("comments", 0),
            "joins": stats.get("joins", 0),
            "clicks": stats.get("posts_clicked", 0),
            "scrolls": stats.get("scrolls", 0),
            "subs": stats.get("subs_browsed", 0),
            "sessions": stats.get("sessions", 0),
        }
        for key, val in pill_map.items():
            if key in self._pill_refs:
                self._pill_refs[key].configure(text=str(val))

    def _on_complete(self, stats):
        """Re-enable buttons and refresh stats (runs on main thread)."""
        self.is_running = False
        self._stop_requested = False
        self.warmer = None
        self.start_btn.configure(state="normal", text="Start Selected Account")
        self.stop_btn.configure(state="disabled")
        self.refresh_btn.configure(state="normal")

        if _warmup_stats_ok(stats):
            self._update_session_stats(stats)
            self._last_action_log = stats.get("action_log", [])
            elapsed = stats.get("total_sec", 0)
            self.progress_label.configure(
                text=f"Done - {stats['sessions']} sessions, "
                     f"{elapsed // 60}m {elapsed % 60}s, "
                     f"{stats['upvotes']}up/{stats['downvotes']}down, "
                     f"{stats['comments']} comments")
        else:
            if stats and (stats.get("failure_reason") == "stopped"):
                self.progress_label.configure(text="Warmup stopped")
            else:
                self.progress_label.configure(
                    text=_warmup_failure_text(stats) if stats else "Warmup failed or stopped")

        # Refresh lifetime totals from DB
        profile_id = self.profile_entry.get().strip()
        if profile_id:
            self._refresh_stats()

        # Refresh stats overview panel
        self._refresh_stats_panel()

    def _show_activity_popout(self):
        """Open a popout window showing all actions from the last session."""
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
        ctk.CTkLabel(win, text=summary, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        # Scrollable activity list
        scroll = ctk.CTkScrollableFrame(win, fg_color=("#F9FAFB", "#111827"))
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        scroll.grid_columnconfigure(3, weight=1)

        # Header row
        headers = [("Time", 60), ("Type", 70), ("Sub", 130), ("Detail", 0), ("Status", 70)]
        for col, (hdr, w) in enumerate(headers):
            lbl = ctk.CTkLabel(scroll, text=hdr, font=("Segoe UI", 13, "bold"),
                               anchor="w")
            lbl.grid(row=0, column=col, sticky="ew", padx=4, pady=(0, 4))

        # Color map for action types
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
                         font=("Consolas", 13), fg_color=bg, anchor="w").grid(
                row=row, column=0, sticky="ew", padx=4, pady=1)

            type_lbl = ctk.CTkLabel(
                scroll, text=action.get("type", ""),
                font=("Segoe UI", 13, "bold"), fg_color=bg,
                text_color=type_colors.get(action.get("type"), "#6B7280"),
                anchor="w")
            type_lbl.grid(row=row, column=1, sticky="ew", padx=4, pady=1)

            sub = action.get("sub", "")
            sub_lbl = ctk.CTkLabel(scroll, text=f"r/{sub}" if sub else "",
                                   font=("Segoe UI", 13), fg_color=bg, anchor="w")
            sub_lbl.grid(row=row, column=2, sticky="ew", padx=4, pady=1)

            # Detail â€” show text, make URL clickable
            detail_text = action.get("text", "") or action.get("url", "")
            url = action.get("url", "")
            detail_frame = ctk.CTkFrame(scroll, fg_color=bg)
            detail_frame.grid(row=row, column=3, sticky="ew", padx=4, pady=1)

            if detail_text:
                det_lbl = ctk.CTkLabel(
                    detail_frame, text=detail_text[:80],
                    font=("Segoe UI", 13), anchor="w")
                det_lbl.pack(side="left", fill="x", expand=True)

            if url and url.startswith("http"):
                link_btn = ctk.CTkButton(
                    detail_frame, text="Open", width=45,
                    font=("Segoe UI", 13), height=22,
                    fg_color="#334155", hover_color="#1F2937",
                    command=lambda u=url: webbrowser.open(u))
                link_btn.pack(side="right", padx=(4, 0))

            status = action.get("status", "ok")
            status_lbl = ctk.CTkLabel(
                scroll, text=status, font=("Segoe UI", 13, "bold"), fg_color=bg,
                text_color=status_colors.get(status, "#6B7280"), anchor="w")
            status_lbl.grid(row=row, column=4, sticky="ew", padx=4, pady=1)

    # ================================================================
    # RUN ALL â€” multi-account warmup with proxy rotation & ban detection
    # ================================================================

    def _load_ban_log(self):
        """Load ban log from disk."""
        if os.path.exists(BAN_LOG_PATH):
            with open(BAN_LOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_ban_log(self):
        """Save ban log to disk (thread-safe)."""
        with self._run_all_lock:
            os.makedirs(os.path.dirname(BAN_LOG_PATH), exist_ok=True)
            tmp = BAN_LOG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._ban_log, f, indent=2)
            os.replace(tmp, BAN_LOG_PATH)

    def _load_rotation_urls(self):
        """Load proxy rotation URLs from schedule_config.json."""
        try:
            with open(SCHEDULE_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            groups = cfg.get("proxy_groups", {})
            return {
                str(k).strip(): (v.get("rotation_url", "") or "").strip()
                for k, v in groups.items()
            }
        except Exception as e:
            logger.error(f"Failed to load rotation URLs: {e}")
            return {}

    def _discover_all_accounts(self):
        """Query AdsPower API for all reddit bot profiles.

        Returns dict: {"P": [acc, ...], "G": [...], ...}
        where each acc = {name, username, adspower_id, proxy_group}
        """
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        by_group = {}
        page_num = 1
        total = 0
        while True:
            data = None
            for attempt in range(3):
                try:
                    resp = requests.get(
                        f"{api_base}/api/v1/user/list",
                        params={"api_key": api_key, "page": page_num, "page_size": 100},
                        timeout=30,
                    )
                    data = resp.json()
                except Exception as e:
                    logger.warning(f"AdsPower API request failed: {e}")
                    time.sleep(2)
                    continue
                if data.get("code") == 0:
                    break
                logger.warning(f"AdsPower API rate limit (attempt {attempt+1}): {data}")
                time.sleep(2)
            if not data or data.get("code") != 0:
                logger.error(f"AdsPower API error after retries: {data}")
                break

            items = data.get("data", {}).get("list", [])
            if not items:
                break

            # Filter prefixes by enabled proxy group checkboxes
            enabled_prefixes = tuple(
                p for p in PROXY_PREFIXES
                if self._proxy_group_vars.get(p.strip(), ctk.BooleanVar(value=True)).get()
            )

            for item in items:
                name = (item.get("name") or "").strip()
                for prefix in enabled_prefixes:
                    if name.startswith(prefix):
                        grp = prefix.strip()
                        username = name[len(prefix):].strip()
                        # Handle "P - reddit bot - Username" format
                        if " - " in username:
                            username = username.rsplit(" - ", 1)[-1].strip()
                        by_group.setdefault(grp, []).append({
                            "name": name,
                            "username": username.lower(),
                            "adspower_id": item["user_id"],
                            "proxy_group": grp,
                        })
                        total += 1
                        break

            page_num += 1
            time.sleep(1)

        logger.info(f"Discovered {total} accounts across {len(by_group)} groups")
        return by_group

    def _rotate_proxy(self, group):
        """Hit the proxy rotation URL for a fresh IP."""
        url = (self._rotation_urls.get(group) or "").strip()
        if not url:
            self.app.after(
                0, self._log,
                f"[{group}] ROTATION BLOCKED: no rotation URL configured. Stopping this group."
            )
            return False

        self.app.after(0, self._log, f"[{group}] Rotating proxy IP before next account...")
        fail_tokens = ("error", "failed", "invalid", "forbidden", "denied")
        for attempt in range(1, 4):
            if self._run_all_stop:
                self.app.after(0, self._log,
                               f"[{group}] Stop All requested during proxy rotation")
                return None
            try:
                resp = requests.get(url, timeout=20)
                body = (resp.text or "").strip()
                snippet = body[:120].replace("\n", " ")

                if resp.status_code == 200:
                    # Some providers return HTTP 200 with an error string in body.
                    if body and any(token in body.lower() for token in fail_tokens):
                        self.app.after(
                            0, self._log,
                            f"[{group}] Rotation response looked invalid (attempt {attempt}/3): {snippet}"
                        )
                    else:
                        self.app.after(
                            0, self._log,
                            f"[{group}] Rotation OK (attempt {attempt}/3)"
                        )
                        # Give the provider time to fully apply the new IP.
                        # fxdx.in mobile tunnels need ~30s to fully establish.
                        if not self._run_all_sleep(30, f"[{group}] proxy rotation settle wait"):
                            return None
                        return True
                else:
                    self.app.after(
                        0, self._log,
                        f"[{group}] Rotation HTTP {resp.status_code} (attempt {attempt}/3): {snippet}"
                    )
            except Exception as e:
                self.app.after(
                    0, self._log,
                    f"[{group}] Rotation request failed (attempt {attempt}/3): {e}"
                )
            if attempt < 3 and not self._run_all_sleep(
                    5, f"[{group}] proxy rotation retry backoff"):
                return None

        self.app.after(
            0, self._log,
            f"[{group}] ROTATION FAILED after 3 attempts. Stopping this group to avoid bans."
        )
        return False

    def _run_all_sleep(self, seconds, label=None):
        """Interruptible sleep for Run All waits (rotation/backoff/cooldowns)."""
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if self._run_all_stop:
                if label:
                    self.app.after(0, self._log,
                                   f"Stop All requested during {label} — skipping wait")
                return False
            time.sleep(min(0.5, max(0.0, end - time.time())))
        return True

    def _load_profile_data(self, username):
        """Load persona/attributes from account_profiles.json if available."""
        try:
            with open(ACCOUNT_PROFILES_PATH, encoding="utf-8") as f:
                profiles = json.load(f).get("profiles", {})
            profile = profiles.get(username)
            if not profile:
                return {}, {}, None, None

            persona = profile.get("persona", {})
            attributes = profile.get("attributes", {})
            raw_age = (profile.get("reddit_account", {}) or {}).get("age_days")
            created_at = profile.get("created_at")
            age_days = None
            try:
                if raw_age is not None:
                    age_days = max(0, int(raw_age))
            except Exception:
                pass
            if age_days is None and created_at:
                try:
                    dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                    age_days = max(0, (now - dt).days)
                except Exception:
                    pass
            return persona, attributes, age_days, created_at
        except Exception:
            return {}, {}, None, None

    # -- Orchestration --

    def _start_run_all(self):
        """Button handler: validate state, launch orchestrator thread."""
        if self._run_all_active or self.is_running:
            messagebox.showwarning("Busy", "A warmup is already running.")
            return

        grok_key = self._grok_key
        if not grok_key:
            messagebox.showerror("Error", "No Grok API key found in config/api_keys.json")
            return

        self._run_all_active = True
        self._run_all_stop = False
        with self._run_all_lock:
            self._group_results = {}
            self._group_warmers = {}
            self._rotation_failed_groups = set()
        self._group_threads = {}

        # UI state
        self.run_all_btn.configure(state="disabled", text="Running All...")
        self.stop_all_btn.configure(state="normal")
        self.start_btn.configure(state="disabled")
        self.log_box.delete("1.0", "end")
        self._reset_session_stats()

        # Reset group labels
        for grp, lbl in self._group_labels.items():
            lbl.configure(text=f"{grp}: discovering...")
        self._run_all_overall_label.configure(text="Discovering accounts...")

        thread = threading.Thread(target=self._continuous_orchestrator,
                                  args=(grok_key,), daemon=True)
        thread.start()

    def _continuous_orchestrator(self, grok_key):
        """Background thread: continuous cycling until all accounts hit daily caps or stopped."""
        enabled = [g for g, v in self._proxy_group_vars.items() if v.get()]
        skipped = [g for g, v in self._proxy_group_vars.items() if not v.get()]
        group_msg = f"Proxy groups: {', '.join(enabled)}"
        if skipped:
            group_msg += f"  (skipping: {', '.join(skipped)})"
        self.app.after(0, self._log, f"=== CONTINUOUS WARMUP STARTED === {group_msg}")

        # Clean up orphaned browsers from previous crashes
        closed = self._close_all_browsers()
        if closed:
            self.app.after(0, self._log,
                           f"Cleaned up {closed} orphaned browser(s) from previous run")
            time.sleep(3)

        # Discover accounts (once — the pool doesn't change mid-run)
        self.app.after(0, self._log, "Discovering accounts from AdsPower...")
        accounts_by_group = self._discover_all_accounts()
        if not accounts_by_group:
            self.app.after(0, self._log, "ERROR: No accounts found in AdsPower")
            self.app.after(0, self._on_run_all_complete)
            return

        for grp, accs in sorted(accounts_by_group.items()):
            self.app.after(0, self._log, f"  {grp}: {len(accs)} accounts")

        # Load configs (once)
        self._ban_log = self._load_ban_log()
        self._rotation_urls = self._load_rotation_urls()

        missing_rotation = [
            grp for grp in sorted(accounts_by_group.keys())
            if not (self._rotation_urls.get(grp) or "").strip()
        ]
        if missing_rotation:
            self.app.after(
                0, self._log,
                f"ERROR: Missing rotation URL for group(s): {', '.join(missing_rotation)}. "
                "Aborting to avoid ban risk.")
            self.app.after(0, self._on_run_all_complete)
            return

        # Collect active (non-banned) account IDs
        all_account_ids = set()
        for accs in accounts_by_group.values():
            for acc in accs:
                all_account_ids.add(acc["adspower_id"])
        permabanned_ids = {k for k, v in self._ban_log.items()
                           if v.get("status") == "permaban"}
        active_ids = all_account_ids - permabanned_ids

        if permabanned_ids:
            self.app.after(0, self._log,
                           f"Skipping {len(permabanned_ids)} permabanned accounts")
        self.app.after(0, self._log,
                       f"Rotation URLs: {', '.join(self._rotation_urls.keys())}")
        self.app.after(0, self._log,
                       "Mode: continuous cycling until all at daily cap\n")

        # Initialize cycling state
        with self._run_all_lock:
            self._daily_caps_cache = {}
            self._accounts_at_cap = set()
        self._cycle_count = 0
        self._last_cycle_date = datetime.now().strftime("%Y-%m-%d")

        while not self._run_all_stop:
            self._cycle_count += 1
            self.app.after(0, self._log, f"\n{'='*50}")
            self.app.after(0, self._log,
                           f"=== CYCLE {self._cycle_count} STARTING ===")
            self.app.after(0, self._log, f"{'='*50}")
            self.app.after(0, self._update_cycle_status)

            cycle_had_activity = self._run_one_cycle(
                accounts_by_group, grok_key, active_ids)

            if self._run_all_stop:
                break

            # Check if ALL active accounts hit their daily cap
            with self._run_all_lock:
                accounts_at_cap = set(self._accounts_at_cap)
            if accounts_at_cap >= active_ids:
                self.app.after(0, self._log,
                               "\n=== ALL ACCOUNTS AT DAILY CAP — DONE ===")
                break

            if not cycle_had_activity:
                self.app.after(0, self._log,
                               "\n=== NO ACTIVITY THIS CYCLE — STOPPING ===")
                break

            # Auto karma check after each cycle
            if not self._run_all_stop:
                self._run_karma_check_batch(accounts_by_group)

            # Inter-cycle pause: 45-120 minutes
            pause_min = random.randint(45, 120)
            pause_sec = pause_min * 60
            self._next_cycle_time = datetime.now() + timedelta(seconds=pause_sec)

            self.app.after(0, self._log,
                           f"\n--- Cycle {self._cycle_count} complete. "
                           f"Next in {pause_min}min "
                           f"(at {self._next_cycle_time.strftime('%H:%M:%S')}) ---")
            self.app.after(0, self._update_cycle_status)
            self.app.after(0, self._start_countdown_timer)

            # Interruptible sleep
            for _ in range(pause_sec):
                if self._run_all_stop:
                    break
                time.sleep(1)

            self._next_cycle_time = None
            self.app.after(0, self._stop_countdown_timer)

        self.app.after(0, self._on_run_all_complete)

    def _close_all_browsers(self):
        """Kill every open AdsPower browser. Prevents orphans from piling up."""
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")
        try:
            resp = requests.get(
                f"{api_base}/api/v1/browser/local-active",
                params={"api_key": api_key}, timeout=10)
            active = resp.json().get("data", {}).get("list", [])
        except Exception:
            return 0
        closed = 0
        for b in active:
            uid = b.get("user_id", "")
            try:
                requests.get(
                    f"{api_base}/api/v1/browser/stop",
                    params={"user_id": uid, "api_key": api_key}, timeout=10)
                closed += 1
            except Exception:
                pass
        return closed

    def _run_one_cycle(self, accounts_by_group, grok_key, active_ids):
        """Run one full pass through all proxy groups. Returns True if any account ran."""
        # Day boundary check — reset caps at midnight
        current_date = datetime.now().strftime("%Y-%m-%d")
        if self._last_cycle_date != current_date:
            with self._run_all_lock:
                self._daily_caps_cache = {}
                self._accounts_at_cap = set()
            self._last_cycle_date = current_date
            self.app.after(0, self._log, "=== NEW DAY — daily caps reset ===")

        with self._run_all_lock:
            self._group_results = {}
            self._group_warmers = {}
            self._rotation_failed_groups = set()
        self._group_threads = {}

        # Reset group labels
        for grp, lbl in self._group_labels.items():
            self.app.after(0, lambda l=lbl, g=grp: l.configure(
                text=f"{g}: starting..."))

        # Launch 1 thread per proxy group
        for grp, accs in accounts_by_group.items():
            if self._run_all_stop:
                break
            t = threading.Thread(target=self._run_group_cycle,
                                 args=(grp, accs, grok_key), daemon=True)
            self._group_threads[grp] = t
            t.start()

        # Wait for all group threads
        for grp, t in self._group_threads.items():
            t.join()

        # Check if any account actually ran
        total_ran = 0
        with self._run_all_lock:
            results_snapshot = [list(results) for results in self._group_results.values()]
        for results in results_snapshot:
            total_ran += sum(1 for r in results if r.get("status") == "success")
        return total_ran > 0

    def _run_group_cycle(self, group, accounts, grok_key):
        """Process accounts in one proxy group for one cycle, checking daily caps."""
        results = []
        with self._run_all_lock:
            self._group_results[group] = results

        shuffled = list(accounts)
        random.shuffle(shuffled)

        # Filter permabanned
        active = [a for a in shuffled
                  if self._ban_log.get(a["adspower_id"], {}).get("status") != "permaban"]

        # Check daily caps — skip accounts already at their cap
        runnable = []
        for acc in active:
            pid = acc["adspower_id"]

            # Get or create daily cap for this account (cached per day)
            with self._run_all_lock:
                cap = self._daily_caps_cache.get(pid)
            if cap is None:
                day = get_warmup_day(pid) or 1
                computed_cap = get_daily_cap(day)
                with self._run_all_lock:
                    cap = self._daily_caps_cache.setdefault(pid, computed_cap)
            totals = get_daily_totals(pid)

            remaining_comments = cap["comments"] - totals.get("comments", 0)
            remaining_joins = cap["joins"] - totals.get("joins", 0)

            if remaining_comments <= 0 and remaining_joins <= 0:
                with self._run_all_lock:
                    self._accounts_at_cap.add(pid)
                self.app.after(0, self._log,
                               f"[{group}] {acc['username']}: AT CAP "
                               f"({totals['comments']}/{cap['comments']}c, "
                               f"{totals['joins']}/{cap['joins']}j) — skip")
                continue

            # Attach remaining budget for _warmup_one_account to pass through
            acc["_remaining_budget"] = {
                "comments": max(0, remaining_comments),
                "joins": max(0, remaining_joins),
            }
            runnable.append(acc)

        at_cap = len(active) - len(runnable)
        self.app.after(0, self._log,
                       f"[{group}] C{self._cycle_count}: {len(runnable)} runnable, "
                       f"{at_cap} at cap")

        if not runnable:
            self.app.after(0, self._update_group_progress, group, 0, 0, 0)
            return

        self.app.after(0, self._update_group_progress, group, 0, 0, len(runnable))

        # Rotate proxy BEFORE the first account
        self.app.after(0, self._log, f"[{group}] Initial proxy rotation...")
        rotation_result = self._rotate_proxy(group)
        if rotation_result is None:
            self.app.after(0, self._log,
                           f"[{group}] Stop requested during initial rotation — halting group")
            return
        if not rotation_result:
            self.app.after(0, self._log,
                           f"[{group}] Initial rotation failed — skipping group this cycle")
            with self._run_all_lock:
                self._rotation_failed_groups.add(group)
            return

        for i, acc in enumerate(runnable):
            with self._run_all_lock:
                rotation_failed = group in self._rotation_failed_groups
            if rotation_failed:
                self.app.after(0, self._log,
                               f"[{group}] STOPPING GROUP: rotation failed.")
                break

            if self._run_all_stop:
                self.app.after(0, self._log, f"[{group}] Stop requested — halting")
                break

            budget = acc.get("_remaining_budget", {})
            self.app.after(0, self._log,
                           f"\n--- [{group}] C{self._cycle_count} {i+1}/{len(runnable)}: "
                           f"{acc['username']} (budget: {budget.get('comments', '?')}c "
                           f"{budget.get('joins', '?')}j) ---")
            self.app.after(0, self._update_group_progress, group, i + 1, i, len(runnable))

            result = self._warmup_one_account(acc, group, grok_key)
            with self._run_all_lock:
                results.append(result)

            status = result.get("status", "?")
            self.app.after(0, self._log, f"[{group}] {acc['username']}: {status}")

            # Cascade breaker: if session was instant-fail (tunnel dead),
            # pause the group and rotate before hammering the next account.
            result_stats = result.get("stats", {})
            result_sec = result_stats.get("total_sec", -1) if isinstance(result_stats, dict) else -1
            if status in ("success", "error", "browser_crashed", "proxy_dead", "no_feed") and result_sec >= 0 and result_sec < 30:
                self.app.after(0, self._log,
                               f"[{group}] Tunnel appears dead ({result_sec}s session). "
                               f"Pausing 60s + rotating before next account...")
                self._run_all_sleep(60, f"[{group}] tunnel cooldown wait")
                if not self._run_all_stop:
                    rotation_result = self._rotate_proxy(group)
                    if rotation_result is False:
                        with self._run_all_lock:
                            self._rotation_failed_groups.add(group)

            with self._run_all_lock:
                rotation_failed = group in self._rotation_failed_groups
            if rotation_failed:
                self.app.after(0, self._log,
                               f"[{group}] STOPPING GROUP after {acc['username']}: "
                               f"rotation failed.")
                break

        done = len(results)
        self.app.after(0, self._update_group_progress, group, done, done, len(runnable))
        self.app.after(0, self._log, f"[{group}] Cycle {self._cycle_count} done: {done} processed")

    def _warmup_one_account(self, account, group, grok_key):
        """Full per-account lifecycle: browser start â†’ ban check â†’ warmup â†’ stop â†’ rotate."""
        profile_key = account["username"]
        adspower_id = account["adspower_id"]
        api_base = self.adspower_config.get(
            "adspower_api_base", "http://localhost:50325")
        api_key = self.adspower_config.get("api_key", "")

        # Skip if permabanned
        prev = self._ban_log.get(adspower_id, {})
        if prev.get("status") == "permaban":
            return {"profile": profile_key, "status": "skip_banned",
                    "adspower_id": adspower_id, "proxy_group": group}

        # Load optional profile data
        persona_data, attributes, age_days, created_at = self._load_profile_data(profile_key)

        # Start AdsPower browser
        self.app.after(0, self._log, f"  Starting browser {adspower_id}...")
        browser_started = False
        _acct_start_ts = 0
        try:
            resp = requests.get(
                f"{api_base}/api/v1/browser/start"
                f"?user_id={adspower_id}&api_key={api_key}",
                timeout=60,
            )
            try:
                data = resp.json()
            except Exception as e:
                return {"profile": profile_key, "status": "failed",
                        "adspower_id": adspower_id,
                        "error": f"invalid AdsPower JSON: {e}"}
        except Exception as e:
            return {"profile": profile_key, "status": "failed",
                    "adspower_id": adspower_id, "error": str(e)}

        if not isinstance(data, dict):
            return {"profile": profile_key, "status": "failed",
                    "adspower_id": adspower_id,
                    "error": f"non-dict AdsPower response ({type(data).__name__})"}
        if data.get("code") != 0:
            return {"profile": profile_key, "status": "failed",
                    "adspower_id": adspower_id, "error": str(data)}
        # Treat code=0 as "browser may be live"; missing-CDP returns must stop
        # explicitly (they happen before the main try/finally below).
        browser_started = True
        ws_endpoint, cdp_reason, cdp_detail = _adspower_cdp_endpoint(data)
        if cdp_reason:
            try:
                requests.get(
                    f"{api_base}/api/v1/browser/stop"
                    f"?user_id={adspower_id}&api_key={api_key}",
                    timeout=15,
                )
                self.app.after(0, self._log,
                               "  Browser stopped (invalid/missing CDP endpoint)")
            except Exception as e:
                self.app.after(0, self._log,
                               f"  Failed to stop browser after invalid/missing CDP endpoint: {e}")
            return {"profile": profile_key, "status": "failed",
                    "adspower_id": adspower_id, "error": cdp_detail}
        _acct_start_ts = time.time()

        try:
            from playwright.sync_api import sync_playwright
            from core.account_warmer import AccountWarmer, warmup_stats_ok
            from core.ban_detector import check_account_health, BanStatus

            if self._run_all_stop:
                self.app.after(0, self._log,
                               "  Stop All requested before Playwright connect — skipping")
                return {"profile": profile_key, "status": "stopped",
                        "adspower_id": adspower_id, "proxy_group": group,
                        "error": "stop requested before Playwright connect"}
            with sync_playwright() as p:
                # CDP connection with retries
                browser = None
                for attempt in range(5):
                    try:
                        browser = p.chromium.connect_over_cdp(ws_endpoint)
                        break
                    except Exception as e:
                        if attempt < 4:
                            wait = 2 * (attempt + 1)
                            self.app.after(0, self._log,
                                           f"  CDP retry in {wait}s... ({e})")
                            if not self._run_all_sleep(wait, "Run All CDP retry wait"):
                                return {"profile": profile_key, "status": "stopped",
                                        "adspower_id": adspower_id, "proxy_group": group,
                                        "error": "stop requested during Playwright connect retry"}
                        else:
                            raise

                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                all_pages = list(ctx.pages) if ctx else []
                def _page_is_closed(pg):
                    try:
                        return bool(pg.is_closed())
                    except Exception:
                        return False

                # Pick existing Reddit tab or first tab
                page = None
                for pg in all_pages:
                    try:
                        url = pg.url or ""
                    except Exception:
                        url = ""
                    if "reddit.com" in url and not _page_is_closed(pg):
                        page = pg
                        break
                if not page:
                    for pg in all_pages:
                        if not _page_is_closed(pg):
                            page = pg
                            break
                if page is None:
                    page = ctx.new_page()

                # Close stale extra tabs
                for pg in all_pages:
                    if pg != page:
                        try:
                            pg.close()
                        except Exception:
                            pass

                if page is None or _page_is_closed(page):
                    raise RuntimeError("No live browser page available for Run All warmup")

                if self._run_all_stop:
                    self.app.after(0, self._log,
                                   "  Stop All requested before health check — skipping")
                    return {"profile": profile_key, "status": "stopped",
                            "adspower_id": adspower_id, "proxy_group": group,
                            "error": "stop requested before warmup start"}

                # === BAN CHECK ===
                self.app.after(0, self._log, "  Checking account health...")
                status, detail = check_account_health(page)

                if status == BanStatus.ACCOUNT_SUSPENDED:
                    self.app.after(0, self._log, f"  BANNED: {detail}")
                    with self._run_all_lock:
                        self._ban_log[adspower_id] = {
                            "status": "permaban", "username": profile_key,
                            "proxy_group": group, "detail": detail,
                            "detected": datetime.now().isoformat(),
                        }
                    self._save_ban_log()
                    return {"profile": profile_key, "status": "banned",
                            "adspower_id": adspower_id, "detail": detail}

                if status == BanStatus.SHADOW_BANNED:
                    self.app.after(0, self._log, f"  SHADOW BANNED: {detail}")
                    with self._run_all_lock:
                        self._ban_log[adspower_id] = {
                            "status": "shadowban", "username": profile_key,
                            "proxy_group": group, "detail": detail,
                            "detected": datetime.now().isoformat(),
                        }
                    self._save_ban_log()
                    return {"profile": profile_key, "status": "shadowbanned",
                            "adspower_id": adspower_id, "detail": detail}

                if status == BanStatus.UNKNOWN_ERROR:
                    if "not logged in" in detail:
                        self.app.after(0, self._log, "  NOT LOGGED IN â€” skipping")
                        return {"profile": profile_key, "status": "not_logged_in",
                                "adspower_id": adspower_id, "proxy_group": group}
                    crash_phrases = [
                        "browser has been closed", "target page",
                        "connection closed", "target closed",
                        "session closed", "page has been closed",
                    ]
                    if any(phrase in detail.lower() for phrase in crash_phrases):
                        self.app.after(0, self._log, f"  BROWSER CRASHED: {detail}")
                        return {"profile": profile_key, "status": "browser_crashed",
                                "adspower_id": adspower_id, "error": detail}
                    self.app.after(0, self._log,
                                   f"  Health check issue: {detail} â€” proceeding")

                self.app.after(0, self._log, f"  Account healthy: {detail}")

                # === WARMUP ===
                # Poetry mode: read from global GUI toggle
                poetry_on = self.poetry_all_var.get()
                try:
                    poetry_karma = int(self.karma_all_entry.get().strip() or "1500")
                except (ValueError, AttributeError):
                    poetry_karma = 1500
                if poetry_on:
                    self.app.after(0, self._log,
                                   f"  Poetry mode ON (karma target: {poetry_karma})")
                warmer = AccountWarmer(
                    adspower_id, page,
                    persona=persona_data or None,
                    attributes=attributes or None,
                    grok_api_key=grok_key,
                    account_age_days=age_days,
                    account_created_at=created_at,
                    username=profile_key,
                    poetry_warmup=poetry_on,
                    karma_target=poetry_karma,
                )
                with self._run_all_lock:
                    self._group_warmers[group] = warmer

                if self._run_all_stop:
                    warmer.stop_requested = True

                # Attach log handler
                warmer_logger = logging.getLogger("core.account_warmer")
                handler = _GUILogHandler(self.log_box, self.app)
                handler.setFormatter(logging.Formatter(
                    f"%(asctime)s [{group}:{profile_key}] %(message)s",
                    datefmt="%H:%M:%S"))
                my_thread = threading.current_thread().ident
                handler.addFilter(
                    type("_TF", (logging.Filter,),
                         {"filter": lambda self, r, tid=my_thread: r.thread == tid})()
                )
                warmer_logger.addHandler(handler)

                try:
                    stats = warmer.run_daily_warmup(
                        remaining_budget=account.get("_remaining_budget"))
                finally:
                    try:
                        warmer_logger.removeHandler(handler)
                    except Exception:
                        pass
                    with self._run_all_lock:
                        self._group_warmers[group] = None

                if not warmup_stats_ok(stats):
                    reason = (stats or {}).get("failure_reason", "warmup_failed")
                    detail = (stats or {}).get("failure_detail", "")
                    if reason == "proxy_dead":
                        status_name = "proxy_dead"
                    elif reason == "no_feed_loaded":
                        status_name = "no_feed"
                    elif reason == "browser_closed":
                        status_name = "browser_crashed"
                    elif reason == "stopped":
                        status_name = "stopped"
                    else:
                        status_name = "warmup_failed"
                    log_prefix = "  WARMUP STOPPED" if status_name == "stopped" else "  WARMUP FAILED"
                    self.app.after(0, self._log,
                                   f"{log_prefix}: {_warmup_failure_text(stats)}")
                    return {"profile": profile_key, "status": status_name,
                            "adspower_id": adspower_id, "proxy_group": group,
                            "error": detail or reason, "stats": stats}

                self.app.after(0, self._log,
                               f"  DONE â€” {stats['comments']}cmt, {stats['upvotes']}up, "
                               f"{stats['joins']}join, {stats['total_sec']//60}m")

                # Clear non-permaban entries on success
                with self._run_all_lock:
                    if adspower_id in self._ban_log and \
                       self._ban_log[adspower_id].get("status") != "permaban":
                        del self._ban_log[adspower_id]
                self._save_ban_log()

                return {"profile": profile_key, "status": "success",
                        "adspower_id": adspower_id, "proxy_group": group,
                        "stats": stats}

        except Exception as e:
            err_str = str(e).lower()
            crash_phrases = ["browser has been closed", "target page",
                             "connection closed", "target closed",
                             "session closed", "page has been closed"]
            if any(phrase in err_str for phrase in crash_phrases):
                self.app.after(0, self._log, f"  BROWSER CRASHED: {e}")
                return {"profile": profile_key, "status": "browser_crashed",
                        "adspower_id": adspower_id, "error": str(e)}
            self.app.after(0, self._log, f"  Warmup error: {e}")
            return {"profile": profile_key, "status": "error",
                    "adspower_id": adspower_id, "error": str(e)}
        finally:
            # Defensive cleanup: clear stale warmer ref if an exception skipped inner finally.
            with self._run_all_lock:
                if self._group_warmers.get(group) is not None:
                    self._group_warmers[group] = None

            # 1. Close browser FIRST
            if browser_started:
                try:
                    requests.get(
                        f"{api_base}/api/v1/browser/stop"
                        f"?user_id={adspower_id}&api_key={api_key}",
                        timeout=15,
                    )
                    self.app.after(0, self._log, "  Browser stopped")
                except Exception as e:
                    self.app.after(0, self._log, f"  Failed to stop browser: {e}")

            if self._run_all_stop:
                self.app.after(0, self._log,
                               "  Stop All active — skipping post-account rotation cleanup")
            else:
                # 2. Rotate proxy AFTER browser closed.
                #    SKIP rotation if the session was instant-fail (< 30s) —
                #    rotating a dead tunnel just makes it worse.
                elapsed = time.time() - _acct_start_ts if _acct_start_ts else 0
                if elapsed < 30:
                    self.app.after(0, self._log,
                                   f"  Skipping rotation — session was only {int(elapsed)}s "
                                   f"(tunnel likely dead, rotating won't help)")
                    self._run_all_sleep(5, "post-account cooldown wait")
                else:
                    if self._run_all_sleep(10, "post-account rotation wait"):
                        rotation_result = self._rotate_proxy(group)
                        if rotation_result is False:
                            with self._run_all_lock:
                                self._rotation_failed_groups.add(group)

    def _stop_run_all(self):
        """Stop all running warmups gracefully."""
        self._run_all_stop = True
        self._stop_countdown_timer()
        self.stop_all_btn.configure(state="disabled")
        self.app.after(0, self._log,
                       "Stop All requested — finishing current account, "
                       "will not start next cycle...")
        with self._run_all_lock:
            warmers = list(self._group_warmers.items())
        for grp, warmer in warmers:
            if warmer:
                warmer.stop_requested = True

    def _reset_today(self):
        """Clear today's warmup flags and daily cap cache."""
        count = reset_today_warmup()
        with self._run_all_lock:
            self._daily_caps_cache = {}
            self._accounts_at_cap = set()
        self._log(f"Reset {count} account(s) + daily caps — all can re-run today.")
        self._refresh_stats_panel()

    # ── Karma checking ──

    def _run_karma_check_batch(self, accounts_by_group):
        """Auto-run karma check via Reddit JSON API after a warmup cycle.

        Called automatically at end of each cycle — no GUI interaction needed.
        Uses proxy from schedule_config to avoid IP exposure.
        """
        import json as _json
        try:
            # Load proxy config
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "config", "schedule_config.json")
            proxy_url = None
            try:
                with open(config_path, encoding="utf-8") as f:
                    sched = _json.load(f)
                proxy_groups = sched.get("proxy_groups", {})
                for grp in ("P", "G", "F", "4u"):
                    http_str = proxy_groups.get(grp, {}).get("http", "")
                    if http_str:
                        parts = http_str.replace("http://", "").split(":")
                        if len(parts) == 4:
                            proxy_url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
                        break
            except Exception:
                pass

            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

            # Build flat list of non-banned accounts
            banned_ids = {k for k, v in self._ban_log.items()
                          if v.get("status") == "permaban"}
            all_accounts = []
            for grp, accs in accounts_by_group.items():
                for acc in accs:
                    if acc["adspower_id"] not in banned_ids:
                        all_accounts.append(acc)

            if not all_accounts:
                return

            self.app.after(0, self._log,
                           f"\n--- Auto karma check: {len(all_accounts)} accounts ---")

            checked = 0
            errors = 0
            for i, acc in enumerate(all_accounts):
                username = acc["username"]
                try:
                    resp = requests.get(
                        f"https://www.reddit.com/user/{username}/about.json",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        proxies=proxies, timeout=15)

                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        ck = data.get("comment_karma", 0)
                        lk = data.get("link_karma", 0)
                        tk = data.get("total_karma", ck + lk)
                        created = data.get("created_utc", 0)
                        age_days = int((time.time() - created) / 86400) if created else 0
                        record_karma(acc["adspower_id"], username, ck, lk,
                                     tk, age_days, int(created))
                        checked += 1
                    else:
                        errors += 1
                except Exception:
                    errors += 1

                if i < len(all_accounts) - 1:
                    time.sleep(2)

            self.app.after(0, self._log,
                           f"--- Karma check done: {checked} recorded, {errors} errors ---")

        except Exception as e:
            self.app.after(0, self._log, f"Auto karma check failed: {e}")

    def _start_karma_check(self):
        """Launch karma check in background thread."""
        self.karma_btn.configure(state="disabled", text="Checking...")
        self._log("=== KARMA CHECK STARTED ===")
        threading.Thread(target=self._karma_check_worker, daemon=True).start()

    def _karma_check_worker(self):
        """Check Reddit karma for all active accounts via public JSON API."""
        import json as _json
        try:
            # Load proxy config — use first available proxy for API requests
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "config", "schedule_config.json")
            with open(config_path, encoding="utf-8") as f:
                sched = _json.load(f)
            proxy_groups = sched.get("proxy_groups", {})
            proxy_url = None
            for grp in ("P", "G", "F", "4u"):
                http_str = proxy_groups.get(grp, {}).get("http", "")
                if http_str:
                    # Format: "http://host:port:user:pass"
                    parts = http_str.replace("http://", "").split(":")
                    if len(parts) == 4:
                        proxy_url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
                    break

            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

            # Discover active accounts
            accounts = self._discover_all_accounts()
            if not accounts:
                self.app.after(0, self._log, "No accounts found")
                return

            # Load ban log to skip banned accounts
            ban_log = self._load_ban_log()
            banned_ids = {k for k, v in ban_log.items() if v.get("status") == "permaban"}

            all_accounts = []
            for grp, accs in sorted(accounts.items()):
                for acc in accs:
                    if acc["adspower_id"] not in banned_ids:
                        all_accounts.append(acc)

            self.app.after(0, self._log,
                           f"Checking karma for {len(all_accounts)} active accounts...")

            results = []
            for i, acc in enumerate(all_accounts):
                username = acc["username"]
                try:
                    resp = requests.get(
                        f"https://www.reddit.com/user/{username}/about.json",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        proxies=proxies, timeout=15)

                    if resp.status_code == 404:
                        results.append({
                            "username": username, "group": acc["proxy_group"],
                            "adspower_id": acc["adspower_id"],
                            "status": "NOT FOUND", "total_karma": 0})
                        self.app.after(0, self._log,
                                       f"  {username}: NOT FOUND (suspended/deleted?)")
                    elif resp.status_code == 200:
                        data = resp.json().get("data", {})
                        ck = data.get("comment_karma", 0)
                        lk = data.get("link_karma", 0)
                        tk = data.get("total_karma", ck + lk)
                        created = data.get("created_utc", 0)
                        age_days = int((time.time() - created) / 86400) if created else 0

                        record_karma(acc["adspower_id"], username, ck, lk,
                                     tk, age_days, int(created))
                        results.append({
                            "username": username, "group": acc["proxy_group"],
                            "adspower_id": acc["adspower_id"],
                            "status": "ok", "comment_karma": ck,
                            "link_karma": lk, "total_karma": tk,
                            "age_days": age_days})
                    else:
                        results.append({
                            "username": username, "group": acc["proxy_group"],
                            "adspower_id": acc["adspower_id"],
                            "status": f"HTTP {resp.status_code}", "total_karma": 0})
                        self.app.after(0, self._log,
                                       f"  {username}: HTTP {resp.status_code}")
                except Exception as e:
                    results.append({
                        "username": username, "group": acc["proxy_group"],
                        "adspower_id": acc["adspower_id"],
                        "status": f"error: {e}", "total_karma": 0})

                # Rate limit: 1 req per 2 seconds
                if i < len(all_accounts) - 1:
                    time.sleep(2)

            # Print summary sorted by karma
            results.sort(key=lambda r: r.get("total_karma", 0), reverse=True)
            self.app.after(0, self._log, "\n=== KARMA REPORT ===")
            self.app.after(0, self._log,
                           f"{'Username':<28} {'Group':<5} {'Comment':>8} {'Link':>8} {'Total':>8} {'Age':>6}")
            self.app.after(0, self._log, "-" * 72)
            for r in results:
                if r["status"] == "ok":
                    self.app.after(0, self._log,
                                   f"{r['username']:<28} {r['group']:<5} "
                                   f"{r['comment_karma']:>8} {r['link_karma']:>8} "
                                   f"{r['total_karma']:>8} {r['age_days']:>5}d")
                else:
                    self.app.after(0, self._log,
                                   f"{r['username']:<28} {r['group']:<5} {r['status']}")

            ok_count = sum(1 for r in results if r["status"] == "ok")
            total_karma = sum(r.get("total_karma", 0) for r in results)
            self.app.after(0, self._log,
                           f"\n{ok_count}/{len(results)} accounts checked | "
                           f"Total karma across all: {total_karma}")
            self.app.after(0, self._log, "=== KARMA CHECK DONE ===")

        except Exception as e:
            self.app.after(0, self._log, f"Karma check failed: {e}")
        finally:
            self.app.after(0, lambda: self.karma_btn.configure(
                state="normal", text="Check Karma"))

    # ── Countdown timer for inter-cycle pauses ──

    def _update_cycle_status(self):
        """Update the overall label with cycle info."""
        with self._run_all_lock:
            capped = len(self._accounts_at_cap)
        if self._next_cycle_time:
            remaining = (self._next_cycle_time - datetime.now()).total_seconds()
            mins = max(0, int(remaining // 60))
            secs = max(0, int(remaining % 60))
            self._run_all_overall_label.configure(
                text=f"Cycle {self._cycle_count} done | {capped} at cap | "
                     f"Next cycle in {mins}m {secs}s")
        else:
            self._run_all_overall_label.configure(
                text=f"Cycle {self._cycle_count} running | {capped} at cap")

    def _start_countdown_timer(self):
        """Start a periodic UI refresh showing time until next cycle."""
        self._stop_countdown_timer()
        self._countdown_tick()

    def _countdown_tick(self):
        """Update the countdown display every 30 seconds."""
        if not self._run_all_active or self._run_all_stop or not self._next_cycle_time:
            return
        self._update_cycle_status()
        self._cycle_timer_id = self.app.after(30000, self._countdown_tick)

    def _stop_countdown_timer(self):
        """Cancel the countdown timer."""
        if self._cycle_timer_id:
            try:
                self.app.after_cancel(self._cycle_timer_id)
            except Exception:
                pass
            self._cycle_timer_id = None

    def _on_run_all_complete(self):
        """Re-enable buttons and show summary (runs on main thread)."""
        self._run_all_active = False
        self._run_all_stop = False
        self._stop_countdown_timer()
        self._next_cycle_time = None
        self.run_all_btn.configure(state="normal",
                                   text="Start All Accounts (Auto-Discover from AdsPower)")
        self.stop_all_btn.configure(state="disabled")
        self.start_btn.configure(state="normal")

        # Summarize results from last cycle
        with self._run_all_lock:
            all_results = []
            for results in self._group_results.values():
                all_results.extend(list(results))
            capped_count = len(self._accounts_at_cap)
            rotation_failed_groups = sorted(self._rotation_failed_groups)

        by_status = {}
        for r in all_results:
            s = r.get("status", "unknown")
            by_status.setdefault(s, []).append(r.get("profile", "?"))

        self._log(f"\n=== CONTINUOUS WARMUP COMPLETE — {self._cycle_count} cycles ===")
        self._log(f"  Accounts at daily cap: {capped_count}")
        for status, names in sorted(by_status.items()):
            self._log(f"  {status}: {len(names)}")
            if status in ("banned", "shadowbanned", "not_logged_in",
                          "browser_crashed", "error",
                          "proxy_dead", "no_feed", "warmup_failed", "stopped"):
                for n in names:
                    self._log(f"    - {n}")

        if rotation_failed_groups:
            self._log(f"  rotation_failed_groups: {len(rotation_failed_groups)}")
            for grp in rotation_failed_groups:
                self._log(f"    - {grp}")

        total = len(all_results)
        success = len(by_status.get("success", []))
        banned = len(by_status.get("banned", []))
        capped = capped_count
        self._run_all_overall_label.configure(
            text=(
                f"Done — {self._cycle_count} cycles | "
                f"{success}/{total} last cycle, "
                f"{capped} at daily cap, {banned} banned"
            ))

        # Save final ban log
        self._save_ban_log()

        # Clean up daily caps (keep for stats panel, cleared on next start)
        self._daily_caps_cache = {}

        # Refresh stats panel with latest data
        self._refresh_stats_panel()

    def _update_group_progress(self, group, current, done, total):
        """Update a group's status label (called via app.after from worker threads)."""
        if group in self._group_labels:
            self._group_labels[group].configure(text=f"{group}: {done}/{total}")
        # Update overall label
        done_all = 0
        banned_all = 0
        with self._run_all_lock:
            group_results_snapshot = [list(results) for results in self._group_results.values()]
        for results in group_results_snapshot:
            done_all += len(results)
            banned_all += sum(1 for r in results if r.get("status") == "banned")
        self._run_all_overall_label.configure(
            text=f"Running â€” {done_all} done, {banned_all} banned")
