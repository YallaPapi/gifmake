"""
Account Warmup tab for GifMake GUI.
Standalone warmup sessions — browse, vote, comment, join subs — without posting.
"""
import customtkinter as ctk
from tkinter import messagebox
import webbrowser
import threading
import os
import json
import sys
import logging
import time
import requests
from datetime import datetime
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.post_history import get_warmup_status, get_warmup_day
from processors.account_profile import (
    ProfileManager,
    AccountProfile,
    AccountAttributes,
    PersonaInterests,
    RedditAccount,
)

logger = logging.getLogger(__name__)


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
        "header":  {"foreground": "#2DD4BF", "font": ("Consolas", 11, "bold")},
        "muted":   {"foreground": "#94A3B8"},
    }
    for tag, opts in tags.items():
        textbox._textbox.tag_config(tag, **opts)

ADSPOWER_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "uploaders", "redgifs", "adspower_config.json"
)
ACCOUNT_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "account_profiles.json"
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
                 "The bot will scroll Reddit, upvote posts, leave comments, and join "
                 "subreddits — just like a real person. New accounts need 3-7 days of "
                 "warmup or Reddit's spam filter will remove your posts.",
            font=("Segoe UI", 13), text_color=("#1F2937", "#E5E7EB"), justify="left"
        )
        desc.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        _auto_wrap(desc)

        # Load API keys silently from config (no visible entry fields)
        saved_keys = self._load_api_keys()
        self._grok_key = saved_keys.get("grok_api_key", "") or os.environ.get("GROK_API_KEY", "")

        # Hidden entry widget (warmup worker reads from this)
        self.grok_key_entry = ctk.CTkEntry(self.parent)
        if self._grok_key:
            self.grok_key_entry.insert(0, self._grok_key)
        # Don't grid — stays invisible

        # === ROW 1: ACCOUNT SELECTOR ===
        acct_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG)
        acct_frame.grid(row=1, column=0, sticky="ew", pady=(0, 5), padx=5)
        acct_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(acct_frame, text="Reddit Account:", font=("Segoe UI", 12, "bold")).grid(
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
            fg_color=ACCENT, hover_color=ACCENT_HOVER, font=("Segoe UI", 11, "bold"),
            command=self._open_add_account_dialog
        )
        self.add_account_btn.grid(row=0, column=2, padx=(5, 0), pady=5)

        self.remove_account_btn = ctk.CTkButton(
            acct_frame, text="Remove", width=80, height=30,
            fg_color=WARN, hover_color=WARN_HOVER, font=("Segoe UI", 11, "bold"),
            command=self._remove_selected_account
        )
        self.remove_account_btn.grid(row=0, column=3, padx=(5, 0), pady=5)

        self.reload_accounts_btn = ctk.CTkButton(
            acct_frame, text="Reload", width=70, height=30,
            fg_color="#334155", hover_color="#1F2937", font=("Segoe UI", 11),
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
            text="This is the Reddit account that will be warmed up. "
                 "The bot browses Reddit using this account's browser profile.",
            font=("Segoe UI", 11), text_color=("#4B5563", "#94A3B8")
        )
        acct_help.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 5))
        _auto_wrap(acct_help)

        # === ROW 2: PERSONA CONFIG ===
        persona_frame = ctk.CTkFrame(self.parent, fg_color=CARD_FG)
        persona_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=(0, 5))
        persona_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(persona_frame, text="Persona:", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", padx=(10, 5), pady=(5, 0))

        self.persona_info_label = ctk.CTkLabel(
            persona_frame,
            text="",
            font=("Segoe UI", 12), text_color=("#374151", "#CBD5E1"), justify="left"
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
            font=("Segoe UI", 10), text_color=("#6B7280", "#94A3B8")
        )
        self.persona_path_label.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            config_row, text="Edit Persona File", width=120, height=28,
            font=("Segoe UI", 11), command=self._open_persona_config
        ).grid(row=0, column=1, padx=(10, 0))

        ctk.CTkButton(
            config_row, text="Reload", width=70, height=28,
            font=("Segoe UI", 11), fg_color="#475569", hover_color="#334155",
            command=self._reload_persona
        ).grid(row=0, column=2, padx=(5, 0))

        persona_help = ctk.CTkLabel(
            persona_frame,
            text="The persona defines what your account is \"into\" — hobbies, location, personality. "
                 "The bot uses this to pick subreddits to browse and to generate realistic comments.",
            font=("Segoe UI", 11), text_color=("#4B5563", "#94A3B8")
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

        ctk.CTkLabel(sess_inner, text="SESSION",
                     font=("Segoe UI", 11, "bold"),
                     text_color=("#0F766E", "#14B8A6")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        self.sess_time_label = ctk.CTkLabel(
            sess_inner, text="--", font=("Segoe UI", 11),
            text_color=("#6B7280", "#94A3B8"))
        self.sess_time_label.grid(row=0, column=1, sticky="e", padx=10, pady=(8, 4))

        pill_frame = ctk.CTkFrame(sess_inner, fg_color="transparent")
        pill_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        self._pill_refs = {}
        pill_defs = [
            ("upvotes", "Upvotes", "upvote"),
            ("downvotes", "Downvotes", "downvote"),
            ("comments", "Comments", "comment"),
            ("joins", "Joined", "join"),
            ("clicks", "Clicks", "click"),
            ("scrolls", "Scrolls", "scroll"),
            ("subs", "Subs seen", "session"),
            ("sessions", "Sessions", "time"),
        ]
        for key, label, color_key in pill_defs:
            pill, val_lbl = _make_pill(pill_frame, "--", label, color_key)
            pill.pack(side="left", padx=3, pady=2)
            self._pill_refs[key] = val_lbl

        # === ROW 3b: LIFETIME STATS (compact bar) ===
        life_outer, life_inner = _accent_card(
            self.parent, accent_color=("#94A3B8", "#475569"),
            row=4, column=0, sticky="ew", padx=5, pady=(0, 5))

        ctk.CTkLabel(life_inner, text="LIFETIME STATS",
                     font=("Segoe UI", 11, "bold"),
                     text_color=("#6B7280", "#94A3B8")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0))

        life_vals = ctk.CTkFrame(life_inner, fg_color="transparent")
        life_vals.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 8))

        self.day_label = ctk.CTkLabel(
            life_vals, text="Day --", font=("Segoe UI", 14, "bold"),
            text_color=("#334155", "#E2E8F0"))
        self.day_label.pack(side="left", padx=(0, 14))

        self.status_val_label = ctk.CTkLabel(
            life_vals, text="--", font=("Segoe UI", 12),
            text_color=("#6B7280", "#94A3B8"))
        self.status_val_label.pack(side="left", padx=(0, 18))

        self.upvotes_label = ctk.CTkLabel(
            life_vals, text="0 votes", font=("Segoe UI", 13, "bold"),
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

        # === ROW 5: BUTTONS ===
        btn_frame = ctk.CTkFrame(self.parent, fg_color="transparent")
        btn_frame.grid(row=5, column=0, sticky="ew", padx=5, pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.start_btn = ctk.CTkButton(
            btn_frame, text="Start Warmup", font=("Segoe UI", 13, "bold"),
            height=40, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_warmup)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=3)

        self.stop_btn = ctk.CTkButton(
            btn_frame, text="Stop", font=("Segoe UI", 13, "bold"),
            height=40, fg_color=WARN, hover_color=WARN_HOVER,
            command=self._stop_warmup, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=3)

        self.refresh_btn = ctk.CTkButton(
            btn_frame, text="Refresh Stats", font=("Segoe UI", 13, "bold"),
            height=40, fg_color="#334155", hover_color="#1F2937",
            command=self._refresh_stats)
        self.refresh_btn.grid(row=0, column=2, sticky="ew", padx=3)

        self.activity_btn = ctk.CTkButton(
            btn_frame, text="View Activity", font=("Segoe UI", 13, "bold"),
            height=40, fg_color="#1E40AF", hover_color="#1E3A8A",
            command=self._show_activity_popout)
        self.activity_btn.grid(row=0, column=3, sticky="ew", padx=3)

        # Store last session's action log for the popout
        self._last_action_log = []

        # === ROW 6: PROGRESS ===
        self.progress_label = ctk.CTkLabel(
            self.parent, text="", font=("Segoe UI", 11), text_color=("#4B5563", "#94A3B8"))
        self.progress_label.grid(row=6, column=0, sticky="w", padx=10, pady=(5, 2))

        # === ROW 8: LOG BOX ===
        ctk.CTkLabel(
            self.parent, text="Live Activity Log", font=("Segoe UI", 12, "bold")
        ).grid(row=7, column=0, sticky="w", padx=10, pady=(5, 0))
        self.log_box = ctk.CTkTextbox(
            self.parent, height=350, font=("Consolas", 11),
            fg_color=("#F9FAFB", "#0D1117"), border_width=1,
            border_color=("#D1D5DB", "#21262D"))
        self.log_box.grid(row=8, column=0, sticky="nsew", padx=5, pady=5)
        self.parent.grid_rowconfigure(8, weight=1)
        _setup_log_tags(self.log_box)

        # Trigger initial account selection (must be after all widgets are created)
        if acct_options and acct_options[0] in self._acct_map:
            self._on_account_change(acct_options[0])

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
            ctk.CTkLabel(dialog, text=label, font=("Segoe UI", 11, "bold")).grid(
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
            font=("Segoe UI", 10),
            text_color=("#64748B", "#94A3B8"),
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
            font=("Segoe UI", 12, "bold"),
            command=_save,
        ).grid(row=0, column=0, sticky="ew", padx=5)
        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            height=34,
            fg_color="#475569",
            hover_color="#334155",
            font=("Segoe UI", 12),
            command=dialog.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=5)

    def _on_account_change(self, selection):
        """Handle account dropdown change — update hidden profile_entry and persona info."""
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

    def _log(self, msg):
        tag = _detect_log_tag(msg)
        if tag:
            self.log_box.insert("end", f"{msg}\n", tag)
        else:
            self.log_box.insert("end", f"{msg}\n")
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
            self.day_label.configure(text="Day --")
            self.status_val_label.configure(text="not started")
            self.upvotes_label.configure(text="0 votes")
            self.comments_label.configure(text="0 comments")
            self.joins_label.configure(text="0 joins")
            self.posts_label.configure(text="0 posts")
            return

        day = get_warmup_day(profile_id)
        self.day_label.configure(text=f"Day {day}")
        self.status_val_label.configure(text=f"{status.get('status', '--')}")
        self.upvotes_label.configure(text=f"{status.get('total_upvotes', 0)} votes")
        self.comments_label.configure(text=f"{status.get('total_comments', 0)} comments")
        self.joins_label.configure(text=f"{status.get('total_joins', 0)} joins")
        self.posts_label.configure(text=f"{status.get('total_posts', 0)} posts")

    def _start_warmup(self):
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

        # Disable buttons and reset session display
        self.start_btn.configure(state="disabled", text="Running...")
        self.stop_btn.configure(state="normal")
        self.refresh_btn.configure(state="disabled")
        self.is_running = True
        self._stop_requested = False
        self.log_box.delete("1.0", "end")
        self._reset_session_stats()

        thread = threading.Thread(
            target=self._warmup_worker,
            args=(profile_id, persona, grok_key, account_age_days, account_created_at),
            daemon=True,
        )
        thread.start()

    def _stop_warmup(self):
        self._stop_requested = True
        if self.warmer:
            self.warmer.stop_requested = True
            self._log("Stop requested — will halt after current cycle...")
        else:
            self._log("Stop requested — aborting startup...")
        self.stop_btn.configure(state="disabled")

    def _warmup_worker(self, profile_id, persona, grok_key,
                       account_age_days=None, account_created_at=None):
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
            data = resp.json()
            if data.get("code") != 0:
                self.app.after(0, self._log, f"AdsPower error: {data}")
                return
            ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
            if not ws_endpoint:
                self.app.after(0, self._log, "AdsPower start returned no CDP endpoint")
                return
            browser_started = True
            self.app.after(0, self._log, "Browser started, connecting Playwright...")

            # 2. Connect Playwright
            from playwright.sync_api import sync_playwright
            from core.account_warmer import AccountWarmer

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(ws_endpoint)
                contexts = browser.contexts
                if contexts:
                    ctx = contexts[0]
                else:
                    ctx = browser.new_context()
                pages = ctx.pages if ctx else []
                page = pages[0] if pages else ctx.new_page()
                if page is None:
                    raise RuntimeError("No browser page is available for warmup")

                self.app.after(0, self._log, "Playwright connected")
                if self._stop_requested:
                    self.app.after(0, self._log, "Warmup stopped before session start")
                    return

                # 3. Create warmer
                warmer = AccountWarmer(
                    profile_id, page,
                    persona=persona,
                    grok_api_key=grok_key,
                    account_age_days=account_age_days,
                    account_created_at=account_created_at)
                self.warmer = warmer
                if self._stop_requested:
                    warmer.stop_requested = True

                day = warmer.get_day()
                max_posts = warmer.get_max_posts_today()
                self.app.after(0, self._log,
                    f"Day {day}, max posts today: {max_posts}, "
                    f"{len(warmer.general_subs)} general subs")
                self.app.after(0, self.progress_label.configure,
                    text=f"Day {day} — warmup in progress...")

                # 4. Attach log handler to capture warmer output
                warmer_logger = logging.getLogger("core.account_warmer")
                handler = _GUILogHandler(self.log_box, self.app)
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(message)s", datefmt="%H:%M:%S"))
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

        except Exception as e:
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
        self.start_btn.configure(state="normal", text="Start Warmup")
        self.stop_btn.configure(state="disabled")
        self.refresh_btn.configure(state="normal")

        if stats:
            self._update_session_stats(stats)
            self._last_action_log = stats.get("action_log", [])
            elapsed = stats.get("total_sec", 0)
            self.progress_label.configure(
                text=f"Done - {stats['sessions']} sessions, "
                     f"{elapsed // 60}m {elapsed % 60}s, "
                     f"{stats['upvotes']}up/{stats['downvotes']}down, "
                     f"{stats['comments']} comments")
        else:
            self.progress_label.configure(text="Warmup failed or stopped")

        # Refresh lifetime totals from DB
        profile_id = self.profile_entry.get().strip()
        if profile_id:
            self._refresh_stats()

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
        ctk.CTkLabel(win, text=summary, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        # Scrollable activity list
        scroll = ctk.CTkScrollableFrame(win, fg_color=("#F9FAFB", "#111827"))
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        scroll.grid_columnconfigure(3, weight=1)

        # Header row
        headers = [("Time", 60), ("Type", 70), ("Sub", 130), ("Detail", 0), ("Status", 70)]
        for col, (hdr, w) in enumerate(headers):
            lbl = ctk.CTkLabel(scroll, text=hdr, font=("Segoe UI", 11, "bold"),
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
                         font=("Consolas", 11), fg_color=bg, anchor="w").grid(
                row=row, column=0, sticky="ew", padx=4, pady=1)

            type_lbl = ctk.CTkLabel(
                scroll, text=action.get("type", ""),
                font=("Segoe UI", 11, "bold"), fg_color=bg,
                text_color=type_colors.get(action.get("type"), "#6B7280"),
                anchor="w")
            type_lbl.grid(row=row, column=1, sticky="ew", padx=4, pady=1)

            sub = action.get("sub", "")
            sub_lbl = ctk.CTkLabel(scroll, text=f"r/{sub}" if sub else "",
                                   font=("Segoe UI", 11), fg_color=bg, anchor="w")
            sub_lbl.grid(row=row, column=2, sticky="ew", padx=4, pady=1)

            # Detail — show text, make URL clickable
            detail_text = action.get("text", "") or action.get("url", "")
            url = action.get("url", "")
            detail_frame = ctk.CTkFrame(scroll, fg_color=bg)
            detail_frame.grid(row=row, column=3, sticky="ew", padx=4, pady=1)

            if detail_text:
                det_lbl = ctk.CTkLabel(
                    detail_frame, text=detail_text[:80],
                    font=("Segoe UI", 11), anchor="w")
                det_lbl.pack(side="left", fill="x", expand=True)

            if url and url.startswith("http"):
                link_btn = ctk.CTkButton(
                    detail_frame, text="Open", width=45,
                    font=("Segoe UI", 10), height=22,
                    fg_color="#334155", hover_color="#1F2937",
                    command=lambda u=url: webbrowser.open(u))
                link_btn.pack(side="right", padx=(4, 0))

            status = action.get("status", "ok")
            status_lbl = ctk.CTkLabel(
                scroll, text=status, font=("Segoe UI", 11, "bold"), fg_color=bg,
                text_color=status_colors.get(status, "#6B7280"), anchor="w")
            status_lbl.grid(row=row, column=4, sticky="ew", padx=4, pady=1)
