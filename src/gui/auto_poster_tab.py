"""
Auto Poster tab for GifMake GUI.
Folder = Profile = Creator campaign model.
Analyzes content with Claude Vision, matches to subs, generates titles,
posts via AdsPower browser profiles with full humanization.
"""
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import os
import json
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.vision_matcher import (
    analyze_image, load_profiles, match_content, random_select_subs,
    scan_content_folder, content_file_hash, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
)
from core.title_generator import generate_titles_batch
from core.post_history import (
    add_post, get_posted_subs, get_banned_subs, get_posts_today, export_results_csv
)
from core.humanizer import Humanizer
from core.ban_detector import check_post_result, check_account_health, BanStatus
from core.spoofer import spoof_file, cleanup_spoof_dir
from core.account_warmer import AccountWarmer

logger = logging.getLogger(__name__)

ADSPOWER_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "uploaders", "redgifs", "adspower_config.json"
)


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
        self.is_running = False
        self.stop_all = False

        self._load_adspower_config()
        self._load_sub_data()
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

    def _create_widgets(self):
        """Build the full campaign UI."""
        self.parent.grid_columnconfigure(0, weight=1)

        # === API KEYS ===
        keys_frame = ctk.CTkFrame(self.parent)
        keys_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10), padx=5)
        keys_frame.grid_columnconfigure(1, weight=1)
        keys_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(keys_frame, text="Claude Key:", font=("", 12)).grid(
            row=0, column=0, sticky="w", padx=(10, 5), pady=5)
        self.claude_key_entry = ctk.CTkEntry(keys_frame, show="*",
                                              placeholder_text="sk-ant-...")
        self.claude_key_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(keys_frame, text="Grok Key:", font=("", 12)).grid(
            row=0, column=2, sticky="w", padx=(15, 5), pady=5)
        self.grok_key_entry = ctk.CTkEntry(keys_frame, show="*",
                                            placeholder_text="xai-...")
        self.grok_key_entry.grid(row=0, column=3, sticky="ew", padx=(5, 10), pady=5)

        # Load from env
        for entry, var in [(self.claude_key_entry, "ANTHROPIC_API_KEY"),
                           (self.grok_key_entry, "GROK_API_KEY")]:
            val = os.environ.get(var, "")
            if val:
                entry.insert(0, val)

        # === STATS BAR ===
        green_count = sum(1 for v in self.sub_tiers.values() if v.get("tier") == "GREEN")
        profiles_count = len(self.sub_profiles)
        ads_count = len(self.profiles_data)
        self.stats_label = ctk.CTkLabel(
            self.parent,
            text=f"{profiles_count:,} sub profiles | {green_count:,} GREEN | {ads_count} AdsPower profiles",
            font=("", 11), text_color="gray"
        )
        self.stats_label.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 5))

        # === CAMPAIGNS SECTION ===
        camp_header = ctk.CTkFrame(self.parent, fg_color="transparent")
        camp_header.grid(row=2, column=0, sticky="ew", padx=5)
        camp_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(camp_header, text="Campaigns", font=("", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=5)
        ctk.CTkButton(camp_header, text="+ Add Campaign", width=130,
                      command=self._add_campaign).grid(row=0, column=1, padx=5)

        # Campaign list (scrollable)
        self.campaigns_frame = ctk.CTkScrollableFrame(self.parent, height=130)
        self.campaigns_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        self.campaigns_frame.grid_columnconfigure(1, weight=1)

        self.campaign_widgets = []  # list of dicts with widget refs

        # === SETTINGS ROW ===
        settings_frame = ctk.CTkFrame(self.parent, fg_color="transparent")
        settings_frame.grid(row=4, column=0, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(settings_frame, text="Subs per file:", font=("", 12)).pack(
            side="left", padx=(5, 5))
        self.subs_per_file = ctk.CTkEntry(settings_frame, width=50)
        self.subs_per_file.pack(side="left", padx=(0, 15))
        self.subs_per_file.insert(0, "8")

        ctk.CTkLabel(settings_frame, text="Daily limit:", font=("", 12)).pack(
            side="left", padx=(0, 5))
        self.daily_limit_entry = ctk.CTkEntry(settings_frame, width=50)
        self.daily_limit_entry.pack(side="left", padx=(0, 15))
        self.daily_limit_entry.insert(0, "8")

        self.spoof_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(settings_frame, text="Spoof files",
                        variable=self.spoof_var).pack(side="left", padx=10)

        self.auto_mode_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(settings_frame, text="Auto Mode (skip review)",
                        variable=self.auto_mode_var).pack(side="left", padx=10)

        # === ACTION BUTTONS ===
        btn_frame = ctk.CTkFrame(self.parent, fg_color="transparent")
        btn_frame.grid(row=5, column=0, sticky="ew", padx=5, pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.analyze_btn = ctk.CTkButton(
            btn_frame, text="1. Analyze All", font=("", 13, "bold"),
            height=38, command=self._start_analysis)
        self.analyze_btn.grid(row=0, column=0, sticky="ew", padx=3)

        self.post_btn = ctk.CTkButton(
            btn_frame, text="2. Start Posting", font=("", 13, "bold"),
            height=38, fg_color="#28a745", hover_color="#218838",
            command=self._start_posting, state="disabled")
        self.post_btn.grid(row=0, column=1, sticky="ew", padx=3)

        self.stop_btn = ctk.CTkButton(
            btn_frame, text="Stop", font=("", 13, "bold"),
            height=38, fg_color="#dc3545", hover_color="#c82333",
            command=self._stop_all, state="disabled")
        self.stop_btn.grid(row=0, column=2, sticky="ew", padx=3)

        # === PROGRESS ===
        self.progress_bar = ctk.CTkProgressBar(self.parent)
        self.progress_bar.grid(row=6, column=0, sticky="ew", padx=10, pady=(5, 2))
        self.progress_bar.set(0)

        self.progress_label = ctk.CTkLabel(
            self.parent, text="", font=("", 11), text_color="gray")
        self.progress_label.grid(row=7, column=0, sticky="w", padx=10)

        # === RESULTS / LOG ===
        self.log_box = ctk.CTkTextbox(self.parent, height=300, font=("Consolas", 11))
        self.log_box.grid(row=8, column=0, sticky="nsew", padx=5, pady=5)
        self.parent.grid_rowconfigure(8, weight=1)

        # === EXPORT BUTTON ===
        ctk.CTkButton(
            self.parent, text="Export Results CSV", width=150,
            command=self._export_results
        ).grid(row=9, column=0, sticky="e", padx=10, pady=5)

    def _add_campaign(self):
        """Add a new campaign row (folder + profile selector)."""
        folder = filedialog.askdirectory(title="Select Content Folder")
        if not folder:
            return

        files = scan_content_folder(folder)
        if not files:
            messagebox.showwarning("No Content", "No images or videos found in that folder.")
            return

        row_idx = len(self.campaign_widgets)
        frame = self.campaigns_frame

        # Profile dropdown
        profile_options = [f"{pid} ({name})" for pid, name in self.profiles_data.items()]
        if not profile_options:
            profile_options = ["No profiles found"]

        profile_var = ctk.StringVar(value=profile_options[row_idx % len(profile_options)]
                                    if profile_options else "")

        row_frame = ctk.CTkFrame(frame)
        row_frame.grid(row=row_idx, column=0, sticky="ew", pady=2, columnspan=4)
        row_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row_frame, text=f"#{row_idx+1}", width=30, font=("", 12, "bold")).grid(
            row=0, column=0, padx=(5, 5))

        folder_label = ctk.CTkLabel(
            row_frame, text=f"{os.path.basename(folder)} ({len(files)} files)",
            font=("", 12), anchor="w")
        folder_label.grid(row=0, column=1, sticky="ew", padx=5)

        profile_dropdown = ctk.CTkOptionMenu(row_frame, variable=profile_var,
                                              values=profile_options, width=200)
        profile_dropdown.grid(row=0, column=2, padx=5)

        status_label = ctk.CTkLabel(row_frame, text="idle", font=("", 11),
                                     text_color="gray", width=80)
        status_label.grid(row=0, column=3, padx=(5, 10))

        remove_btn = ctk.CTkButton(row_frame, text="X", width=30, fg_color="#666",
                                    command=lambda i=row_idx: self._remove_campaign(i))
        remove_btn.grid(row=0, column=4, padx=5)

        # Parse profile_id from dropdown value
        selected = profile_var.get()
        profile_id = selected.split(" (")[0] if " (" in selected else selected
        profile_name = self.profiles_data.get(profile_id, "unknown")

        campaign = Campaign(folder, profile_id, profile_name)
        campaign.files = files

        widget_info = {
            "frame": row_frame,
            "folder_label": folder_label,
            "profile_var": profile_var,
            "status_label": status_label,
            "campaign": campaign,
        }
        self.campaign_widgets.append(widget_info)
        self.campaigns.append(campaign)

        self._log(f"Added campaign: {os.path.basename(folder)} → {profile_name} ({len(files)} files)")

    def _remove_campaign(self, index):
        """Remove a campaign row."""
        if index < len(self.campaign_widgets):
            w = self.campaign_widgets[index]
            w["frame"].destroy()
            self.campaign_widgets.pop(index)
            self.campaigns.pop(index)

    def _log(self, msg):
        """Append to the log box."""
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")

    def _update_campaign_status(self, index, status, color="gray"):
        """Update a campaign's status label."""
        if index < len(self.campaign_widgets):
            self.campaign_widgets[index]["status_label"].configure(
                text=status, text_color=color)

    # ===================== ANALYSIS =====================

    def _start_analysis(self):
        """Analyze all campaigns."""
        if not self.campaigns:
            messagebox.showerror("Error", "Add at least one campaign folder first.")
            return

        claude_key = self.claude_key_entry.get().strip()
        grok_key = self.grok_key_entry.get().strip()
        if not claude_key:
            messagebox.showerror("Error", "Enter your Claude API key.")
            return
        if not grok_key:
            messagebox.showerror("Error", "Enter your Grok API key.")
            return

        self.analyze_btn.configure(state="disabled", text="Analyzing...")
        self.is_running = True

        thread = threading.Thread(target=self._analysis_worker,
                                  args=(claude_key, grok_key), daemon=True)
        thread.start()

    def _analysis_worker(self, claude_key, grok_key):
        """Background: analyze all campaigns then generate titles."""
        total_files = sum(len(c.files) for c in self.campaigns)
        processed = 0

        try:
            subs_per = int(self.subs_per_file.get())
        except ValueError:
            subs_per = 8

        for camp_idx, campaign in enumerate(self.campaigns):
            self.app.after(0, self._update_campaign_status, camp_idx, "analyzing", "yellow")
            campaign.status = "analyzing"
            campaign.posting_plan = []

            # Re-read profile_id from dropdown (user might have changed it)
            selected = self.campaign_widgets[camp_idx]["profile_var"].get()
            campaign.profile_id = selected.split(" (")[0] if " (" in selected else selected
            campaign.profile_name = self.profiles_data.get(campaign.profile_id, "unknown")

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
                    excluded = banned | posted

                    # Match + random select
                    all_matches = match_content(vision, self.sub_profiles,
                                                self.sub_tiers, excluded_subs=excluded,
                                                sub_data=self.sub_data,
                                                max_subscribers=50000)
                    selected_subs = random_select_subs(all_matches, count=subs_per)

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

                    tags_str = ", ".join(vision.get("tags", [])[:5])
                    self.app.after(0, self._log,
                        f"  {fname}: {tags_str} → {len(selected_subs)} subs matched")

                except Exception as e:
                    self.app.after(0, self._log, f"  ERROR {fname}: {e}")

            # Generate titles for this campaign's plan
            if campaign.posting_plan:
                self.app.after(0, self._log,
                    f"\nGenerating titles for {len(campaign.posting_plan)} posts...")
                self.app.after(0, lambda p=processed, t=total_files:
                    self._update_progress(p, t, "Generating titles..."))

                pairings = [{
                    "sub_name": p["sub_name"],
                    "sub_theme": p["sub_theme"],
                    "content_tags": p["content_tags"],
                    "body_type": p["body_type"],
                    "action": p["action"],
                    "setting": p["setting"],
                } for p in campaign.posting_plan]

                titles = generate_titles_batch(pairings, grok_key)

                for i, title in enumerate(titles):
                    if title and i < len(campaign.posting_plan):
                        campaign.posting_plan[i]["title"] = title

                # Fill any None titles with a generic fallback
                for item in campaign.posting_plan:
                    if not item["title"]:
                        tags = item["content_tags"][:3]
                        item["title"] = " ".join(t.replace("_", " ").title() for t in tags)

            campaign.status = "ready"
            self.app.after(0, self._update_campaign_status, camp_idx, "ready", "green")
            self.app.after(0, self._log,
                f"Campaign {camp_idx+1} ready: {len(campaign.posting_plan)} posts planned\n")

        # Done analyzing all
        self.app.after(0, self._analysis_complete)

    def _analysis_complete(self):
        """Called when all analysis is done."""
        self.analyze_btn.configure(state="normal", text="1. Analyze All")
        self.progress_bar.set(1.0)

        total_posts = sum(len(c.posting_plan) for c in self.campaigns)
        self.progress_label.configure(text=f"Analysis complete: {total_posts} total posts planned")

        if total_posts > 0:
            self.post_btn.configure(state="normal")

            # Show posting plan in log
            if not self.auto_mode_var.get():
                self._show_posting_plan()

    def _show_posting_plan(self):
        """Display the posting plan for manual review."""
        self._log("\n" + "=" * 60)
        self._log("POSTING PLAN — Review below, then click 'Start Posting'")
        self._log("=" * 60)

        for camp_idx, campaign in enumerate(self.campaigns):
            self._log(f"\n--- Campaign {camp_idx+1}: {campaign.profile_name} ---")
            current_file = ""
            for item in campaign.posting_plan:
                if item["file_name"] != current_file:
                    current_file = item["file_name"]
                    self._log(f"\n  {current_file}:")
                self._log(f"    r/{item['sub_name']} (score:{item['score']:.0f}) — \"{item['title']}\"")

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

    def _campaign_posting_worker(self, camp_idx, campaign):
        """Post all items for one campaign (one AdsPower profile). Runs in its own thread."""
        import requests as req

        self.app.after(0, self._update_campaign_status, camp_idx, "connecting", "yellow")
        self.app.after(0, self._log,
            f"\n[Campaign {camp_idx+1}] Starting profile: {campaign.profile_name}")

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

                # Account warmup — progressive activities based on account age
                warmer = AccountWarmer(campaign.profile_id, page)
                warmup_day = warmer.get_day()
                max_posts = warmer.get_max_posts_today()
                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] Warmup day {warmup_day}, "
                    f"max posts today: {max_posts}")

                # Get target sub names for warmup activities
                target_subs = [item["sub_name"] for item in campaign.posting_plan]
                warmup_results = warmer.run_daily_warmup(target_subs=target_subs)
                self.app.after(0, self._log,
                    f"[Campaign {camp_idx+1}] Warmup done: "
                    f"browsed {warmup_results['browsed']}min, "
                    f"joined {warmup_results['joined']}, "
                    f"upvoted {warmup_results['upvoted']}, "
                    f"commented {warmup_results['commented']}")

                if not warmer.should_post_today():
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] Day {warmup_day}: "
                        f"browse-only phase, no posting yet")
                    self.app.after(0, self._update_campaign_status,
                        camp_idx, "warmup", "orange")
                    return

                # Cap posting plan to warmup limit
                if max_posts < len(campaign.posting_plan):
                    campaign.posting_plan = campaign.posting_plan[:max_posts]
                    self.app.after(0, self._log,
                        f"[Campaign {camp_idx+1}] Capped to {max_posts} posts "
                        f"(warmup day {warmup_day})")

                # Regular session warm (brief scroll before posting)
                humanizer.warm_session()

                # Post each item sequentially
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
                    if humanizer.should_stop_for_day(posts_today):
                        self.app.after(0, self._log,
                            f"[Campaign {camp_idx+1}] Daily limit reached ({posts_today})")
                        break

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
                                    f"  Spoof failed: {spoof_result.get('error', 'unknown')} — using original")
                        except Exception as e:
                            self.app.after(0, self._log,
                                f"  Spoof error: {e} — using original")

                    # Pre-post browsing
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
                                f"  SUCCESS → {detail}")

                        elif post_status == BanStatus.SUB_BANNED:
                            banned += 1
                            from core.post_history import add_ban
                            add_ban(campaign.profile_id, sub, detail)
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "banned", error=detail)
                            self.app.after(0, self._log,
                                f"  BANNED from r/{sub}: {detail}")

                        elif post_status == BanStatus.RATE_LIMITED:
                            # Back off significantly
                            self.app.after(0, self._log,
                                f"  RATE LIMITED: {detail}. Waiting 5 minutes...")
                            add_post(campaign.profile_id, item["file_hash"],
                                    sub, title, file_path, "rate_limited", error=detail)
                            time.sleep(300)

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
                        humanizer.wait_between_posts()

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

    def _stop_all(self):
        """Signal all campaigns to stop."""
        self.stop_all = True
        for c in self.campaigns:
            c.stop_requested = True
        self._log("\nSTOP REQUESTED — finishing current posts...")

    def _export_results(self):
        """Export all post results to CSV."""
        output_path = filedialog.asksaveasfilename(
            title="Export Results", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")])
        if output_path:
            count = export_results_csv(output_path)
            messagebox.showinfo("Exported", f"Exported {count} results to:\n{output_path}")
