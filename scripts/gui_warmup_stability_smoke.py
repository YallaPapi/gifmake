import logging
import os
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)


class DummyResponse:
    def __init__(self, payload=None, text="ok", status_code=200, json_exc=None):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._payload


class ImmediateApp:
    def after(self, _delay, func, *args, **kwargs):
        return func(*args, **kwargs)

    def after_cancel(self, _token):
        return None


class RecordingApp:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, func, *args, **kwargs):
        self.after_calls.append((delay, getattr(func, "__name__", str(func)), args, kwargs))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, _token):
        return None


class DummyWidget:
    def __init__(self):
        self.config_calls = []

    def configure(self, **kwargs):
        self.config_calls.append(kwargs)

    def delete(self, *_args, **_kwargs):
        return None

    def set(self, value):
        self.config_calls.append({"set": value})


class DummyNullHandler(logging.Handler):
    def emit(self, _record):
        return None


class LockTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._depth = 0
        self.enter_count = 0

    def __enter__(self):
        self._lock.acquire()
        self._depth += 1
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        self._lock.release()
        return False

    def _assert_locked(self):
        if self._depth <= 0:
            raise AssertionError("shared state accessed without _run_all_lock")


class GuardedDict(dict):
    def __init__(self, lock_tracker, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock_tracker = lock_tracker

    def __getitem__(self, key):
        self._lock_tracker._assert_locked()
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        self._lock_tracker._assert_locked()
        return super().__setitem__(key, value)

    def get(self, key, default=None):
        self._lock_tracker._assert_locked()
        return super().get(key, default)

    def setdefault(self, key, default=None):
        self._lock_tracker._assert_locked()
        return super().setdefault(key, default)

    def values(self):
        self._lock_tracker._assert_locked()
        return super().values()

    def items(self):
        self._lock_tracker._assert_locked()
        return super().items()

    def pop(self, key, default=None):
        self._lock_tracker._assert_locked()
        return super().pop(key, default)


class GuardedSet(set):
    def __init__(self, lock_tracker, *args):
        super().__init__(*args)
        self._lock_tracker = lock_tracker

    def add(self, element):
        self._lock_tracker._assert_locked()
        return super().add(element)

    def __len__(self):
        self._lock_tracker._assert_locked()
        return super().__len__()

    def __iter__(self):
        self._lock_tracker._assert_locked()
        return super().__iter__()

    def __contains__(self, element):
        self._lock_tracker._assert_locked()
        return super().__contains__(element)


def _assert(condition, msg):
    if not condition:
        raise AssertionError(msg)


def _fake_playwright_module(raise_on_call=False):
    sync_api = types.ModuleType("playwright.sync_api")

    class _Ctx:
        def __enter__(self):
            if raise_on_call:
                raise AssertionError("sync_playwright() should not be used in this test")
            raise AssertionError("sync_playwright() unexpectedly used")

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright():
        return _Ctx()

    sync_api.sync_playwright = sync_playwright
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    return {"playwright": pkg, "playwright.sync_api": sync_api}


class FakePage:
    def __init__(self, url="about:blank", closed=False):
        self.url = url
        self._closed = closed

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True
        return None


class FakeContext:
    def __init__(self, pages=None, new_page_factory=None):
        self.pages = list(pages or [])
        self._new_page_factory = new_page_factory or (lambda: FakePage())

    def new_page(self):
        page = self._new_page_factory()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, contexts=None, new_context_factory=None):
        self.contexts = list(contexts or [])
        self._new_context_factory = new_context_factory or (lambda: FakeContext())

    def new_context(self):
        ctx = self._new_context_factory()
        self.contexts.append(ctx)
        return ctx


def _fake_playwright_module_with_connect(connect_fn):
    sync_api = types.ModuleType("playwright.sync_api")

    class _Ctx:
        def __enter__(self):
            chromium = SimpleNamespace(connect_over_cdp=connect_fn)
            return SimpleNamespace(chromium=chromium)

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright():
        return _Ctx()

    sync_api.sync_playwright = sync_playwright
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    return {"playwright": pkg, "playwright.sync_api": sync_api}


def case_account_warmer_browser_closed_feed_load():
    from core import account_warmer as aw_mod

    class FakePage:
        def goto(self, *_args, **_kwargs):
            raise RuntimeError("Target page, context or browser has been closed")

    warmer = aw_mod.AccountWarmer.__new__(aw_mod.AccountWarmer)
    warmer.poetry_warmup = False
    warmer._poetry_subs = []
    warmer.page = FakePage()
    warmer.stop_requested = False
    warmer._wait_for_timeout = lambda _ms: True
    warmer._screenshot_error = lambda *_a, **_k: None
    warmer._run_failure = None
    warmer._max_comments = 0
    warmer.stats = {
        "upvotes": 0, "downvotes": 0, "comments": 0, "joins": 0,
        "posts_clicked": 0, "subs_browsed": 0, "sessions": 0,
        "total_sec": 0, "scrolls": 0,
    }

    reddit_mod = types.ModuleType("uploaders.reddit.reddit_poster_playwright")
    reddit_mod.dismiss_over18 = lambda page: None
    uploaders_mod = types.ModuleType("uploaders")
    uploaders_reddit_mod = types.ModuleType("uploaders.reddit")
    uploaders_mod.reddit = uploaders_reddit_mod
    uploaders_reddit_mod.reddit_poster_playwright = reddit_mod

    with patch.dict(sys.modules, {
        "uploaders": uploaders_mod,
        "uploaders.reddit": uploaders_reddit_mod,
        "uploaders.reddit.reddit_poster_playwright": reddit_mod,
    }):
        ok = aw_mod.AccountWarmer._run_browse_session(warmer, session_sec=1)

    _assert(ok is False, "browse session should fail when page/browser is closed during feed load")
    _assert(isinstance(warmer._run_failure, dict), "run failure metadata missing")
    _assert(warmer._run_failure.get("reason") == "browser_closed",
            f"expected browser_closed, got {warmer._run_failure}")


def _make_min_warmup_tab():
    from gui.warmup_tab import WarmupTab

    tab = WarmupTab.__new__(WarmupTab)
    tab.adspower_config = {"adspower_api_base": "http://fake", "api_key": "k"}
    tab.app = ImmediateApp()
    tab.progress_label = DummyWidget()
    tab.log_box = DummyWidget()
    tab._log_handler = None
    tab._stop_requested = False
    tab.warmer = None
    tab._logs = []
    tab._completed = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._on_complete = lambda stats: tab._completed.append(stats)
    return tab


def case_warmup_tab_single_bad_adspower_response_stats():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()
    with patch.object(wt_mod.requests, "get", return_value=DummyResponse(payload=["not", "a", "dict"])):
        wt_mod.WarmupTab._warmup_worker(tab, "pid-1", None, "")

    _assert(len(tab._completed) == 1, "completion callback should be called exactly once")
    stats = tab._completed[0]
    _assert(isinstance(stats, dict), "expected explicit failure stats for bad AdsPower response")
    _assert(stats.get("failure_reason") == "adspower_bad_response",
            f"unexpected failure_reason: {stats}")


def case_warmup_tab_single_stop_before_connect():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()

    def fake_get(*_args, **_kwargs):
        tab._stop_requested = True
        return DummyResponse(payload={
            "code": 0,
            "data": {"ws": {"puppeteer": "ws://dummy"}},
        })

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module(raise_on_call=True)):
            wt_mod.WarmupTab._warmup_worker(tab, "pid-2", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for stop-before-connect")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "stopped", f"expected stopped stats, got {stats}")
    _assert("Playwright connect" in (stats.get("failure_detail") or ""),
            f"expected startup stop detail, got {stats}")


def case_warmup_tab_single_start_timeout_classified():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()
    with patch.object(wt_mod.requests, "get", side_effect=TimeoutError("start timeout")):
        wt_mod.WarmupTab._warmup_worker(tab, "pid-timeout", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for start-timeout")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "adspower_start_error",
            f"expected adspower_start_error, got {stats}")


def _make_min_run_all_tab():
    from gui.warmup_tab import WarmupTab

    tab = WarmupTab.__new__(WarmupTab)
    tab.adspower_config = {"adspower_api_base": "http://fake", "api_key": "k"}
    tab.app = ImmediateApp()
    tab._run_all_lock = threading.Lock()
    tab._group_warmers = {}
    tab._rotation_failed_groups = set()
    tab._run_all_stop = True
    tab._ban_log = {}
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._load_profile_data = lambda _profile_key: ({}, {}, None, None)
    tab._save_ban_log = lambda: None
    tab._rotate_proxy = lambda _group: True
    tab.poetry_all_var = SimpleNamespace(get=lambda: False)
    tab.karma_all_entry = SimpleNamespace(get=lambda: "1500")
    return tab


def case_warmup_tab_run_all_stop_before_connect_cleans_browser():
    import gui.warmup_tab as wt_mod

    tab = _make_min_run_all_tab()
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module(raise_on_call=True)):
            result = wt_mod.WarmupTab._warmup_one_account(
                tab,
                {"username": "acct", "adspower_id": "pid-runall"},
                "P",
                "grok",
            )

    _assert(result.get("status") == "stopped", f"expected stopped result, got {result}")
    _assert(any("/browser/stop" in c for c in calls),
            "Run All startup stop should still stop AdsPower browser in finally")


def _make_min_auto_poster_for_standalone():
    from gui.auto_poster_tab import AutoPosterTab

    tab = AutoPosterTab.__new__(AutoPosterTab)
    tab.adspower_config = {"adspower_api_base": "http://fake", "api_key": "k"}
    tab.app = ImmediateApp()
    tab._logs = []
    tab._completions = []
    tab._released = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._on_warmup_complete = lambda profile_id, stats: tab._completions.append((profile_id, stats))
    tab._get_account_proxy_group = lambda _pid: "P"
    tab._acquire_proxy_group = lambda _pg, _pid: True
    tab._release_proxy_group = lambda pg, pid: tab._released.append((pg, pid))
    tab._active_warmups = {"pid-stand": {"warmer": None, "stop": False}}
    tab._rotate_proxy = (
        lambda _pg, *args, **kwargs:
        tab._active_warmups["pid-stand"].__setitem__("stop", True) or True
    )
    return tab


def case_auto_poster_standalone_stop_after_rotation_skips_start():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    start_calls = []

    def fake_get(url, timeout=None, **_kwargs):
        start_calls.append(url)
        raise AssertionError("AdsPower start should not be called when stop occurs after rotation")

    with patch.object(ap_mod._requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._standalone_warmup_worker(tab, "pid-stand", None, None, "")

    _assert(not start_calls, "standalone warmup should skip AdsPower start after stop during rotation")
    _assert(len(tab._completions) == 1, "standalone completion callback missing")
    _assert(tab._completions[0][1].get("failure_reason") == "stopped",
            f"expected stopped completion stats, got {tab._completions}")


def case_auto_poster_standalone_start_timeout_classified():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._get_account_proxy_group = lambda _pid: ""
    tab._active_warmups = {"pid-timeout-stand": {"warmer": None, "stop": False}}

    with patch.object(ap_mod._requests, "get", side_effect=TimeoutError("start timeout")):
        ap_mod.AutoPosterTab._standalone_warmup_worker(
            tab, "pid-timeout-stand", None, None, "")

    _assert(len(tab._completions) == 1, "standalone timeout completion callback missing")
    _assert(tab._completions[0][1].get("failure_reason") == "adspower_start_error",
            f"expected adspower_start_error, got {tab._completions}")


def _make_min_auto_poster_for_campaign():
    from gui.auto_poster_tab import AutoPosterTab

    tab = AutoPosterTab.__new__(AutoPosterTab)
    tab.adspower_config = {"adspower_api_base": "http://fake", "api_key": "k"}
    tab.app = ImmediateApp()
    tab.stop_all = False
    tab._posting_stop_requested = False
    tab._logs = []
    tab._status_updates = []
    tab._released = []
    tab.campaigns = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    def _update_status(idx, status, color):
        tab._status_updates.append((idx, status, color))
        if idx < len(tab.campaigns):
            tab.campaigns[idx].status = status
    tab._update_campaign_status = _update_status
    tab._refresh_account_statuses = lambda: None
    tab._get_account_proxy_group = lambda _pid: "P"
    tab._acquire_proxy_group = lambda _pg, _pid: True
    tab._rotate_proxy = lambda _pg, *args, **kwargs: True
    tab._release_proxy_group = lambda pg, pid: tab._released.append((pg, pid))
    return tab


def _make_min_auto_poster_for_posting_complete():
    from gui.auto_poster_tab import AutoPosterTab

    tab = AutoPosterTab.__new__(AutoPosterTab)
    tab.app = RecordingApp()
    tab.is_running = True
    tab.stop_all = False
    tab._posting_stop_requested = False
    tab.post_btn = DummyWidget()
    tab.analyze_btn = DummyWidget()
    tab.stop_btn = DummyWidget()
    tab.progress_bar = DummyWidget()
    tab.progress_label = DummyWidget()
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._perf_delayed_timer = None
    tab._refresh_account_statuses = lambda: tab._logs.append("statuses_refreshed")
    tab._start_perf_timer_calls = 0
    tab._start_perf_timer = lambda: setattr(tab, "_start_perf_timer_calls", tab._start_perf_timer_calls + 1)
    tab._start_perf_check_calls = 0
    tab._start_perf_check = lambda: setattr(tab, "_start_perf_check_calls", tab._start_perf_check_calls + 1)
    return tab


def case_auto_poster_campaign_stop_before_connect_cleans_and_releases():
    import requests as real_requests
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    campaign = SimpleNamespace(
        profile_id="pid-camp",
        profile_name="acct-camp",
        posting_plan=[],
        stop_requested=False,
        warmer=None,
        status="",
    )
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            campaign.stop_requested = True
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected requests.get URL: {url}")

    with patch.object(real_requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    _assert(any("/browser/start" in c for c in calls), "campaign test should hit browser/start")
    _assert(any("/browser/stop" in c for c in calls), "campaign startup-stop path should stop browser")
    _assert(("P", "pid-camp") in tab._released, "proxy group should be released on startup stop")
    _assert(any(status == "stopped" for _, status, _ in tab._status_updates),
            f"expected stopped status update, got {tab._status_updates}")


def case_auto_poster_proxy_group_lock_contention():
    from gui.auto_poster_tab import AutoPosterTab

    tab = AutoPosterTab.__new__(AutoPosterTab)
    tab._active_proxy_groups = {}
    tab._active_proxy_groups_lock = threading.Lock()

    winners = []
    results = []
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        pid = f"pid-{i}"
        ok = AutoPosterTab._acquire_proxy_group(tab, "P", pid)
        results.append((pid, ok))
        if ok:
            winners.append(pid)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _assert(sum(1 for _, ok in results if ok) == 1,
            f"expected exactly one proxy-group lock winner, got {results}")
    winner = winners[0]
    _assert(AutoPosterTab._get_proxy_group_owner(tab, "P") == winner,
            "proxy-group owner lookup returned wrong winner")
    AutoPosterTab._release_proxy_group(tab, "P", winner)
    _assert(AutoPosterTab._get_proxy_group_owner(tab, "P") is None,
            "proxy-group lock release failed")


def _fake_reddit_post_module():
    reddit_mod = types.ModuleType("uploaders.reddit.reddit_poster_playwright")
    reddit_mod.post_file_to_subreddit = lambda **_kwargs: True
    uploaders_mod = types.ModuleType("uploaders")
    uploaders_reddit_mod = types.ModuleType("uploaders.reddit")
    uploaders_mod.reddit = uploaders_reddit_mod
    uploaders_reddit_mod.reddit_poster_playwright = reddit_mod
    return {
        "uploaders": uploaders_mod,
        "uploaders.reddit": uploaders_reddit_mod,
        "uploaders.reddit.reddit_poster_playwright": reddit_mod,
    }


def case_warmup_tab_single_malformed_json_classified():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()
    with patch.object(wt_mod.requests, "get",
                      return_value=DummyResponse(json_exc=ValueError("bad json"))):
        wt_mod.WarmupTab._warmup_worker(tab, "pid-json", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for invalid JSON")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "adspower_bad_response",
            f"expected adspower_bad_response, got {stats}")


def case_warmup_tab_single_nested_payload_type_cleans_browser():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": []})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        wt_mod.WarmupTab._warmup_worker(tab, "pid-nested", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for nested payload anomaly")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "adspower_bad_response",
            f"expected adspower_bad_response, got {stats}")
    _assert(any("/browser/stop" in c for c in calls),
            "single warmup should stop AdsPower browser after code=0 malformed payload")


def case_warmup_tab_single_missing_cdp_stop_failure_no_crash():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()

    def fake_get(url, timeout=None, **_kwargs):
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {}}})
        if "/browser/stop" in url:
            raise TimeoutError("stop timeout")
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        wt_mod.WarmupTab._warmup_worker(tab, "pid-missing-cdp", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for missing-cdp stop failure")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "adspower_missing_cdp",
            f"expected adspower_missing_cdp, got {stats}")


def case_warmup_tab_single_no_live_page_after_connect_cleans_browser():
    import gui.warmup_tab as wt_mod

    tab = _make_min_warmup_tab()
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    ctx = FakeContext(
        pages=[FakePage(url="https://www.reddit.com/", closed=True)],
        new_page_factory=lambda: FakePage(url="about:blank", closed=True),
    )
    browser = FakeBrowser(contexts=[ctx])

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module_with_connect(lambda _ws: browser)):
            wt_mod.WarmupTab._warmup_worker(tab, "pid-nolive", None, "")

    _assert(len(tab._completed) == 1, "completion callback missing for no-live-page path")
    stats = tab._completed[0]
    _assert(stats.get("failure_reason") == "warmup_worker_error",
            f"expected warmup_worker_error, got {stats}")
    _assert(any("/browser/stop" in c for c in calls),
            "single warmup no-live-page path should stop AdsPower browser")


def case_warmup_tab_run_all_non_dict_adspower_response_no_crash():
    import gui.warmup_tab as wt_mod

    tab = _make_min_run_all_tab()
    tab._run_all_stop = False
    with patch.object(wt_mod.requests, "get",
                      return_value=DummyResponse(payload=["bad", "payload"])):
        result = wt_mod.WarmupTab._warmup_one_account(
            tab,
            {"username": "acct", "adspower_id": "pid-runall-bad"},
            "P",
            "grok",
        )

    _assert(result.get("status") == "failed", f"expected failed result, got {result}")
    _assert("non-dict AdsPower response" in (result.get("error") or ""),
            f"expected explicit non-dict error, got {result}")


def case_warmup_tab_run_all_nested_payload_type_cleans_browser():
    import gui.warmup_tab as wt_mod

    tab = _make_min_run_all_tab()
    tab._run_all_stop = False
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": []})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        result = wt_mod.WarmupTab._warmup_one_account(
            tab,
            {"username": "acct", "adspower_id": "pid-runall-nested"},
            "P",
            "grok",
        )

    _assert(result.get("status") == "failed", f"expected failed result, got {result}")
    _assert("payload field 'data'" in (result.get("error") or ""),
            f"expected nested payload error detail, got {result}")
    _assert(any("/browser/stop" in c for c in calls),
            "Run All malformed code=0 payload should stop AdsPower browser")


def case_warmup_tab_rotate_proxy_stop_during_settle_wait_returns_none():
    import gui.warmup_tab as wt_mod

    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab.app = ImmediateApp()
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._rotation_urls = {"P": "http://rotate"}
    tab._run_all_stop = False

    def fake_sleep(_seconds, _label=None):
        tab._run_all_stop = True
        return False

    tab._run_all_sleep = fake_sleep

    with patch.object(wt_mod.requests, "get",
                      return_value=DummyResponse(text="ok", status_code=200)):
        result = wt_mod.WarmupTab._rotate_proxy(tab, "P")

    _assert(result is None, f"expected None for stop-during-rotation, got {result}")


def case_warmup_tab_run_all_stop_during_cdp_retry_cleans_browser():
    import gui.warmup_tab as wt_mod

    tab = _make_min_run_all_tab()
    tab._run_all_stop = False
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    def stop_sleep(_seconds, _label=None):
        tab._run_all_stop = True
        return False

    tab._run_all_sleep = stop_sleep

    def connect_over_cdp(_ws):
        raise RuntimeError("cdp not ready")

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module_with_connect(connect_over_cdp)):
            result = wt_mod.WarmupTab._warmup_one_account(
                tab,
                {"username": "acct", "adspower_id": "pid-runall-cdp"},
                "P",
                "grok",
            )

    _assert(result.get("status") == "stopped", f"expected stopped result, got {result}")
    _assert(any("/browser/stop" in c for c in calls),
            "Run All stop during CDP retry should stop AdsPower browser")


def case_warmup_tab_run_all_playwright_import_error_cleans_browser_and_classifies():
    import gui.warmup_tab as wt_mod

    tab = _make_min_run_all_tab()
    tab._run_all_stop = False
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    bad_sync_api = types.ModuleType("playwright.sync_api")
    bad_playwright_pkg = types.ModuleType("playwright")
    bad_playwright_pkg.sync_api = bad_sync_api

    with patch.object(wt_mod.requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, {
                "playwright": bad_playwright_pkg,
                "playwright.sync_api": bad_sync_api,
        }):
            result = wt_mod.WarmupTab._warmup_one_account(
                tab,
                {"username": "acct", "adspower_id": "pid-runall-import"},
                "P",
                "grok",
            )

    _assert(result.get("status") == "error", f"expected error result, got {result}")
    _assert(any("/browser/stop" in c for c in calls),
            "Run All import failure should still stop AdsPower browser in finally")


def case_auto_poster_rotate_proxy_stop_during_settle_wait_returns_none():
    import gui.auto_poster_tab as ap_mod

    tab = ap_mod.AutoPosterTab.__new__(ap_mod.AutoPosterTab)
    tab.app = ImmediateApp()
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._queue_config = {"proxy_groups": {"P": {
        "rotation_url": "http://rotate",
        "wait_after_rotate_sec": 10,
    }}}
    tab._sleep_with_stop = lambda *args, **kwargs: False

    with patch.object(ap_mod._requests, "get", return_value=DummyResponse(text="ok")):
        result = ap_mod.AutoPosterTab._rotate_proxy(
            tab, "P", stop_checker=lambda: False, stop_label="test wait")

    _assert(result is None, f"expected None for stop-during-rotation settle, got {result}")


def case_auto_poster_standalone_malformed_json_classified():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._get_account_proxy_group = lambda _pid: ""
    tab._active_warmups = {"pid-json-stand": {"warmer": None, "stop": False}}

    with patch.object(ap_mod._requests, "get",
                      return_value=DummyResponse(json_exc=ValueError("bad json"))):
        ap_mod.AutoPosterTab._standalone_warmup_worker(
            tab, "pid-json-stand", None, None, "")

    _assert(len(tab._completions) == 1, "standalone invalid-JSON completion missing")
    _assert(tab._completions[0][1].get("failure_reason") == "adspower_bad_response",
            f"expected adspower_bad_response, got {tab._completions}")


def case_auto_poster_standalone_nested_payload_type_cleans_browser():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._get_account_proxy_group = lambda _pid: ""
    tab._active_warmups = {"pid-nested-stand": {"warmer": None, "stop": False}}
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": []})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(ap_mod._requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._standalone_warmup_worker(
            tab, "pid-nested-stand", None, None, "")

    _assert(len(tab._completions) == 1, "standalone nested-payload completion missing")
    stats = tab._completions[0][1]
    _assert(stats.get("failure_reason") == "adspower_bad_response",
            f"expected adspower_bad_response, got {stats}")
    _assert(any("/browser/stop" in c for c in calls),
            "standalone malformed code=0 payload should stop AdsPower browser")


def case_auto_poster_standalone_missing_cdp_stop_failure_no_crash():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._get_account_proxy_group = lambda _pid: ""
    tab._active_warmups = {"pid-missing-cdp-stand": {"warmer": None, "stop": False}}

    def fake_get(url, timeout=None, **_kwargs):
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {}}})
        if "/browser/stop" in url:
            raise TimeoutError("stop timeout")
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(ap_mod._requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._standalone_warmup_worker(
            tab, "pid-missing-cdp-stand", None, None, "")

    _assert(len(tab._completions) == 1, "standalone missing-cdp completion missing")
    stats = tab._completions[0][1]
    _assert(stats.get("failure_reason") == "adspower_missing_cdp",
            f"expected adspower_missing_cdp, got {stats}")


def case_auto_poster_standalone_stop_during_proxy_rotation_wait_reports_stopped():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._active_warmups = {"pid-rot-stop": {"warmer": None, "stop": False}}
    tab._rotate_proxy = lambda _pg, *args, **kwargs: None

    start_calls = []
    with patch.object(ap_mod._requests, "get",
                      side_effect=lambda *a, **k: start_calls.append(a[0]) or DummyResponse()):
        ap_mod.AutoPosterTab._standalone_warmup_worker(tab, "pid-rot-stop", None, None, "")

    _assert(not start_calls, "standalone worker should not start browser when rotation wait is stopped")
    _assert(len(tab._completions) == 1, "standalone stop-during-rotation completion missing")
    stats = tab._completions[0][1]
    _assert(stats.get("failure_reason") == "stopped",
            f"expected stopped stats, got {stats}")


def case_auto_poster_standalone_no_live_page_after_connect_cleans_browser():
    import gui.auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_standalone()
    tab._get_account_proxy_group = lambda _pid: ""
    tab._active_warmups = {"pid-stand-nolive": {"warmer": None, "stop": False}}
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected URL: {url}")

    ctx = FakeContext(
        pages=[FakePage(url="https://www.reddit.com/", closed=True)],
        new_page_factory=lambda: FakePage(url="about:blank", closed=True),
    )
    browser = FakeBrowser(contexts=[ctx])

    with patch.object(ap_mod._requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module_with_connect(lambda _ws: browser)):
            ap_mod.AutoPosterTab._standalone_warmup_worker(
                tab, "pid-stand-nolive", None, None, "")

    _assert(len(tab._completions) == 1, "standalone no-live-page completion missing")
    stats = tab._completions[0][1]
    _assert(stats.get("failure_reason") == "warmup_worker_error",
            f"expected warmup_worker_error, got {stats}")
    _assert(any("/browser/stop" in c for c in calls),
            "standalone no-live-page path should stop AdsPower browser")


def case_auto_poster_campaign_stop_during_proxy_rotation_wait_stopped():
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    tab._rotate_proxy = lambda _pg, *args, **kwargs: None
    campaign = SimpleNamespace(
        profile_id="pid-camp-rot-stop",
        profile_name="acct-camp",
        posting_plan=[],
        stop_requested=False,
        warmer=None,
        status="",
    )

    ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    _assert(("P", "pid-camp-rot-stop") in tab._released,
            "campaign should release proxy group when stopped during rotation")
    _assert(any(status == "stopped" for _, status, _ in tab._status_updates),
            f"expected stopped status update, got {tab._status_updates}")


def case_auto_poster_campaign_nested_payload_type_cleans_and_releases():
    import requests as real_requests
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    campaign = SimpleNamespace(
        profile_id="pid-camp-nested",
        profile_name="acct-camp",
        posting_plan=[],
        stop_requested=False,
        warmer=None,
        status="",
    )
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": []})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected requests.get URL: {url}")

    with patch.object(real_requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    _assert(any("/browser/stop" in c for c in calls),
            "campaign malformed code=0 payload should stop AdsPower browser")
    _assert(("P", "pid-camp-nested") in tab._released,
            "campaign malformed payload path should release proxy group")
    _assert(any(status == "error" for _, status, _ in tab._status_updates),
            f"expected error status update, got {tab._status_updates}")


def case_auto_poster_campaign_no_live_page_after_connect_cleans_and_releases():
    import requests as real_requests
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    campaign = SimpleNamespace(
        profile_id="pid-camp-nolive",
        profile_name="acct-camp",
        posting_plan=[],
        stop_requested=False,
        warmer=None,
        status="",
    )
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected requests.get URL: {url}")

    ctx = FakeContext(
        pages=[FakePage(url="https://www.reddit.com/", closed=True)],
        new_page_factory=lambda: FakePage(url="about:blank", closed=True),
    )
    browser = FakeBrowser(contexts=[ctx])

    with patch.object(real_requests, "get", side_effect=fake_get):
        with patch.dict(sys.modules, _fake_playwright_module_with_connect(lambda _ws: browser)):
            with patch.dict(sys.modules, _fake_reddit_post_module()):
                ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    _assert(any("/browser/stop" in c for c in calls),
            "campaign no-live-page path should stop AdsPower browser")
    _assert(("P", "pid-camp-nolive") in tab._released,
            "campaign no-live-page path should release proxy group")
    _assert(any(status == "error" for _, status, _ in tab._status_updates),
            f"expected error status update, got {tab._status_updates}")


def case_auto_poster_campaign_stop_before_browser_start_without_proxy_group():
    import requests as real_requests
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    tab._get_account_proxy_group = lambda _pid: ""
    campaign = SimpleNamespace(
        profile_id="pid-camp-nopg",
        profile_name="acct-camp",
        posting_plan=[],
        stop_requested=True,
        warmer=None,
        status="",
    )
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        raise AssertionError("requests.get should not be called when stop is set before browser start")

    with patch.object(real_requests, "get", side_effect=fake_get):
        ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    _assert(not calls, "campaign no-proxy stop-before-start should not call requests.get")
    _assert(any(status == "stopped" for _, status, _ in tab._status_updates),
            f"expected stopped status update, got {tab._status_updates}")


def case_auto_poster_wait_with_stop_interrupts_rate_limit_wait():
    import gui.auto_poster_tab as ap_mod

    tab = ap_mod.AutoPosterTab.__new__(ap_mod.AutoPosterTab)
    tab.app = ImmediateApp()
    tab.stop_all = False
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    campaign = SimpleNamespace(stop_requested=False)

    def fake_sleep(_sec):
        campaign.stop_requested = True
        return None

    with patch.object(ap_mod.time, "sleep", side_effect=fake_sleep):
        ok = ap_mod.AutoPosterTab._wait_with_stop(tab, campaign, 1, "rate-limit backoff")

    _assert(ok is False, "_wait_with_stop should abort when stop is requested during wait")


def case_auto_poster_account_row_connecting_state_is_active_posting():
    import gui.auto_poster_tab as ap_mod

    class PackWidget(DummyWidget):
        def __init__(self):
            super().__init__()
            self.pack_calls = 0
            self.pack_forget_calls = 0

        def pack(self, *_args, **_kwargs):
            self.pack_calls += 1

        def pack_forget(self):
            self.pack_forget_calls += 1

    tab = ap_mod.AutoPosterTab.__new__(ap_mod.AutoPosterTab)
    tab._active_warmups = {}
    tab.campaigns = [SimpleNamespace(profile_id="pid-connecting", status="connecting")]

    status_label = DummyWidget()
    warmup_btn = DummyWidget()
    stop_btn = PackWidget()
    tab._acct_table_rows = [{
        "ads_id": "pid-connecting",
        "status_label": status_label,
        "warmup_btn": warmup_btn,
        "stop_btn": stop_btn,
    }]

    ap_mod.AutoPosterTab._refresh_account_statuses(tab)

    _assert(status_label.config_calls and status_label.config_calls[-1].get("text") == "Posting",
            f"connecting campaign should show Posting account status: {status_label.config_calls}")
    _assert(stop_btn.pack_calls >= 1 and stop_btn.pack_forget_calls == 0,
            "connecting campaign should keep per-account Stop visible")
    _assert(warmup_btn.config_calls and warmup_btn.config_calls[-1].get("state") == "disabled",
            f"connecting campaign should disable per-account Warmup: {warmup_btn.config_calls}")


def case_warmup_tab_run_group_cycle_daily_cap_shared_state_uses_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab.app = ImmediateApp()
    tab._run_all_lock = lock
    tab._group_results = GuardedDict(lock)
    tab._daily_caps_cache = GuardedDict(lock)
    tab._accounts_at_cap = GuardedSet(lock)
    tab._rotation_failed_groups = GuardedSet(lock)
    tab._run_all_stop = False
    tab._ban_log = {}
    tab._cycle_count = 1
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._update_group_progress = lambda *args, **kwargs: None
    tab._rotate_proxy = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("should not rotate when no accounts are runnable"))
    tab._warmup_one_account = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("at-cap accounts should not reach _warmup_one_account"))

    day_calls = []

    def fake_get_warmup_day(pid):
        day_calls.append(pid)
        return 1

    accounts = [
        {"username": "acct-1", "adspower_id": "pid-cap"},
        {"username": "acct-2", "adspower_id": "pid-cap"},
    ]

    with patch.object(wt_mod, "get_warmup_day", side_effect=fake_get_warmup_day):
        with patch.object(wt_mod, "get_daily_cap",
                          side_effect=lambda _day: {"comments": 1, "joins": 1}):
            with patch.object(wt_mod, "get_daily_totals",
                              side_effect=lambda _pid: {"comments": 1, "joins": 1}):
                wt_mod.WarmupTab._run_group_cycle(tab, "P", accounts, "grok")

    _assert(day_calls == ["pid-cap"],
            f"daily cap cache should avoid duplicate get_warmup_day calls, got {day_calls}")
    with lock:
        _assert(tab._daily_caps_cache.get("pid-cap") == {"comments": 1, "joins": 1},
                f"daily cap cache missing expected entry: {dict(tab._daily_caps_cache)}")
        _assert("pid-cap" in tab._accounts_at_cap,
                f"expected pid-cap in at-cap set, got {set(tab._accounts_at_cap)}")


def case_warmup_tab_update_group_progress_uses_locked_snapshot():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab._run_all_lock = lock
    tab._group_labels = {"P": DummyWidget()}
    tab._run_all_overall_label = DummyWidget()
    tab._group_results = GuardedDict(lock, {
        "P": [{"status": "success"}, {"status": "banned"}],
        "G": [{"status": "banned"}],
    })

    wt_mod.WarmupTab._update_group_progress(tab, "P", 2, 2, 2)

    _assert(lock.enter_count >= 1, "_update_group_progress should acquire _run_all_lock")
    _assert(any("Running" in (c.get("text") or "") for c in tab._run_all_overall_label.config_calls),
            f"overall label was not updated as expected: {tab._run_all_overall_label.config_calls}")


def case_warmup_tab_on_run_all_complete_reads_shared_state_under_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab._run_all_lock = lock
    tab._group_results = GuardedDict(lock, {
        "P": [{"profile": "acct-1", "status": "success"}],
        "G": [{"profile": "acct-2", "status": "stopped"}],
    })
    tab._accounts_at_cap = GuardedSet(lock, {"pid-cap"})
    tab._rotation_failed_groups = GuardedSet(lock, {"G"})
    tab._daily_caps_cache = {}
    tab._cycle_count = 2
    tab._run_all_active = True
    tab._run_all_stop = True
    tab._cycle_timer_id = None
    tab._next_cycle_time = None
    tab.run_all_btn = DummyWidget()
    tab.stop_all_btn = DummyWidget()
    tab.start_btn = DummyWidget()
    tab._run_all_overall_label = DummyWidget()
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._stop_countdown_timer = lambda: None
    tab._save_ban_log = lambda: None
    tab._refresh_stats_panel = lambda: tab._logs.append("stats_refreshed")

    wt_mod.WarmupTab._on_run_all_complete(tab)

    _assert(lock.enter_count >= 1, "_on_run_all_complete should acquire _run_all_lock")
    _assert(any("rotation_failed_groups" in msg for msg in tab._logs),
            f"expected rotation-failed summary in logs, got {tab._logs}")
    _assert(any("Done" in (c.get("text") or "") for c in tab._run_all_overall_label.config_calls),
            f"expected final overall label update, got {tab._run_all_overall_label.config_calls}")


def case_warmup_tab_update_cycle_status_reads_accounts_at_cap_under_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab._run_all_lock = lock
    tab._accounts_at_cap = GuardedSet(lock, {"pid-a", "pid-b"})
    tab._next_cycle_time = None
    tab._cycle_count = 3
    tab._run_all_overall_label = DummyWidget()

    wt_mod.WarmupTab._update_cycle_status(tab)

    _assert(lock.enter_count >= 1, "_update_cycle_status should acquire _run_all_lock")
    _assert(any("2 at cap" in (c.get("text") or "") for c in tab._run_all_overall_label.config_calls),
            f"expected at-cap count in cycle status, got {tab._run_all_overall_label.config_calls}")


def case_warmup_tab_stop_run_all_reads_group_warmers_under_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    warmer = SimpleNamespace(stop_requested=False)
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab._run_all_lock = lock
    tab._group_warmers = GuardedDict(lock, {"P": warmer, "G": None})
    tab._run_all_stop = False
    tab._stop_countdown_timer = lambda: None
    tab.stop_all_btn = DummyWidget()
    tab.app = ImmediateApp()
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))

    wt_mod.WarmupTab._stop_run_all(tab)

    _assert(lock.enter_count >= 1, "_stop_run_all should snapshot warmers under _run_all_lock")
    _assert(tab._run_all_stop is True, "_stop_run_all should set stop flag")
    _assert(warmer.stop_requested is True, "active warmer should receive stop_requested=True")


def case_warmup_tab_reset_today_clears_shared_state_under_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab._run_all_lock = lock
    tab._daily_caps_cache = GuardedDict(lock, {"pid": {"comments": 1}})
    tab._accounts_at_cap = GuardedSet(lock, {"pid"})
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._refresh_stats_panel = lambda: tab._logs.append("stats_refreshed")

    with patch.object(wt_mod, "reset_today_warmup", return_value=2):
        wt_mod.WarmupTab._reset_today(tab)

    _assert(lock.enter_count >= 1, "_reset_today should clear shared state under _run_all_lock")
    with lock:
        _assert(dict(tab._daily_caps_cache) == {},
                f"daily cap cache should be reset, got {dict(tab._daily_caps_cache)}")
        _assert(len(tab._accounts_at_cap) == 0,
                f"accounts-at-cap set should be reset, got {set(tab._accounts_at_cap)}")


def case_warmup_tab_run_group_cycle_initial_rotation_failure_sets_flag_under_lock():
    import gui.warmup_tab as wt_mod

    lock = LockTracker()
    tab = wt_mod.WarmupTab.__new__(wt_mod.WarmupTab)
    tab.app = ImmediateApp()
    tab._run_all_lock = lock
    tab._group_results = GuardedDict(lock)
    tab._daily_caps_cache = GuardedDict(lock)
    tab._accounts_at_cap = GuardedSet(lock)
    tab._rotation_failed_groups = GuardedSet(lock)
    tab._run_all_stop = False
    tab._ban_log = {}
    tab._cycle_count = 1
    tab._logs = []
    tab._log = lambda msg: tab._logs.append(str(msg))
    tab._update_group_progress = lambda *args, **kwargs: None
    tab._rotate_proxy = lambda _group: False
    tab._warmup_one_account = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("_warmup_one_account should not run when initial rotation fails"))

    accounts = [{"username": "acct-1", "adspower_id": "pid-1"}]
    with patch.object(wt_mod, "get_warmup_day", return_value=1):
        with patch.object(wt_mod, "get_daily_cap", return_value={"comments": 2, "joins": 1}):
            with patch.object(wt_mod, "get_daily_totals", return_value={"comments": 0, "joins": 0}):
                wt_mod.WarmupTab._run_group_cycle(tab, "P", accounts, "grok")

    _assert(lock.enter_count >= 1, "_run_group_cycle should update rotation failure under _run_all_lock")
    with lock:
        _assert("P" in tab._rotation_failed_groups,
                f"expected group P in rotation_failed_groups, got {set(tab._rotation_failed_groups)}")


def _fake_reddit_post_module_with_post(post_fn):
    reddit_mod = types.ModuleType("uploaders.reddit.reddit_poster_playwright")
    reddit_mod.post_file_to_subreddit = post_fn
    uploaders_mod = types.ModuleType("uploaders")
    uploaders_reddit_mod = types.ModuleType("uploaders.reddit")
    uploaders_mod.reddit = uploaders_reddit_mod
    uploaders_reddit_mod.reddit_poster_playwright = reddit_mod
    return {
        "uploaders": uploaders_mod,
        "uploaders.reddit": uploaders_reddit_mod,
        "uploaders.reddit.reddit_poster_playwright": reddit_mod,
    }


def _run_auto_poster_campaign_worker_smoke(
        warmup_mode="interleaved",
        posting_plan=None,
        browse_hook=None,
        pre_post_hook=None,
        check_post_result_hook=None,
        add_post_hook=None,
        wait_between_hook=None,
        wait_with_stop_hook=None,
        warmer_get_day_exc=None):
    import requests as real_requests
    from gui import auto_poster_tab as ap_mod

    state = {
        "request_calls": [],
        "post_calls": [],
        "pre_post_calls": [],
        "wait_between_calls": 0,
        "add_posts": [],
        "record_activity": [],
        "warmers": [],
    }

    if posting_plan is None:
        posting_plan = [{
            "sub_name": "testsub",
            "title": "Test Title",
            "file_path": "C:\\tmp\\fake.gif",
            "file_hash": "hash-1",
            "selected": True,
        }]

    tab = _make_min_auto_poster_for_campaign()
    campaign = SimpleNamespace(
        profile_id="pid-camp-loop",
        profile_name="acct-camp",
        posting_plan=list(posting_plan),
        stop_requested=False,
        warmer=None,
        status="",
    )
    tab.campaigns = [campaign]
    tab.hijack_slider = SimpleNamespace(get=lambda: 0)
    tab._apply_warmup_overrides = lambda _warmer: None
    tab.grok_key_entry = SimpleNamespace(get=lambda: "")
    tab.warmup_mode_var = SimpleNamespace(get=lambda: warmup_mode)
    tab.browse_min_entry = SimpleNamespace(get=lambda: "10")
    tab.browse_max_entry = SimpleNamespace(get=lambda: "10")
    tab.daily_limit_entry = SimpleNamespace(get=lambda: "10")
    tab.spoof_var = SimpleNamespace(get=lambda: False)
    tab.adv_cqs_var = SimpleNamespace(get=lambda: False)
    if wait_with_stop_hook is not None:
        tab._wait_with_stop = lambda camp, seconds, label: wait_with_stop_hook(
            tab, camp, state, seconds, label)

    def fake_requests_get(url, timeout=None, **_kwargs):
        state["request_calls"].append(url)
        if "/browser/start" in url:
            return DummyResponse(payload={"code": 0, "data": {"ws": {"puppeteer": "ws://dummy"}}})
        if "/browser/stop" in url:
            return DummyResponse(payload={"code": 0})
        raise AssertionError(f"Unexpected requests.get URL: {url}")

    live_ctx = FakeContext(pages=[FakePage(url="https://www.reddit.com/", closed=False)])
    live_browser = FakeBrowser(contexts=[live_ctx])

    ban_status = SimpleNamespace(
        OK="ok",
        SUB_BANNED="sub_banned",
        RATE_LIMITED="rate_limited",
        ACCOUNT_SUSPENDED="account_suspended",
    )

    class FakeHumanizer:
        def __init__(self, _page, _cfg):
            pass

        def pre_post_browse(self, sub):
            state["pre_post_calls"].append(sub)
            if pre_post_hook:
                return pre_post_hook(tab, campaign, state, sub)
            return None

        def wait_between_posts(self, stop_checker=None):
            state["wait_between_calls"] += 1
            if wait_between_hook:
                return wait_between_hook(tab, campaign, state, stop_checker)
            if stop_checker and stop_checker():
                return False
            return True

    class FakeWarmer:
        def __init__(self, profile_id, _page, **_kwargs):
            self.profile_id = profile_id
            self.stop_requested = False
            self.hijack_ratio = 0.0
            self.general_subs = ["testsub"]
            self._run_failure = {}
            self.stats = {
                "scrolls": 1,
                "upvotes": 0,
                "downvotes": 0,
                "comments": 0,
                "joins": 0,
            }
            state["warmers"].append(self)

        def get_day(self):
            if warmer_get_day_exc is not None:
                raise warmer_get_day_exc
            return 3

        def get_max_posts_today(self):
            return 3

        def _run_browse_session(self, session_sec=None):
            if browse_hook:
                return browse_hook(self, session_sec, tab, campaign, state)
            self.stats["scrolls"] = max(1, int(self.stats.get("scrolls", 0) or 0))
            return True

    def fake_check_post_result(_page):
        if check_post_result_hook:
            return check_post_result_hook(tab, campaign, state)
        return ban_status.OK, "posted"

    def fake_add_post(*args, **kwargs):
        state["add_posts"].append((args, kwargs))
        if add_post_hook:
            return add_post_hook(tab, campaign, state, args, kwargs)

    def fake_record_activity(*args):
        state["record_activity"].append(args)

    def fake_post_file_to_subreddit(**kwargs):
        state["post_calls"].append(kwargs)
        return True

    fake_profile_manager = lambda *args, **kwargs: SimpleNamespace(get_all_profiles=lambda: [])

    with patch.object(real_requests, "get", side_effect=fake_requests_get):
        with patch.dict(sys.modules, _fake_playwright_module_with_connect(lambda _ws: live_browser)):
            with patch.dict(sys.modules, _fake_reddit_post_module_with_post(fake_post_file_to_subreddit)):
                with patch.multiple(
                        ap_mod,
                        Humanizer=FakeHumanizer,
                        AccountWarmer=FakeWarmer,
                        BanStatus=ban_status,
                        check_account_health=lambda _page: (ban_status.OK, "ok"),
                        check_post_result=fake_check_post_result,
                        get_posts_today=lambda _pid: 0,
                        add_post=fake_add_post,
                        record_activity=fake_record_activity,
                        ProfileManager=fake_profile_manager,
                        warmup_stats_ok=lambda stats: isinstance(stats, dict) and int(
                            stats.get("scrolls", 0) or 0) > 0):
                    ap_mod.AutoPosterTab._campaign_posting_worker(tab, 0, campaign)

    return tab, campaign, state


def case_auto_poster_campaign_stop_after_interleaved_browse_before_post_skips_post():
    def browse_hook(_warmer, _session_sec, _tab, campaign, _state):
        campaign.stop_requested = True
        return True

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="interleaved",
        browse_hook=browse_hook,
    )

    _assert(len(state["post_calls"]) == 0,
            "campaign should not submit a post after stop during interleaved browse")
    _assert(campaign.status == "stopped",
            f"expected campaign status stopped, got {campaign.status!r}")
    _assert(any("Stopped by user during interleaved warmup" in m for m in tab._logs),
            f"expected interleaved-stop log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_pre_post_browse_skips_post():
    def pre_post_hook(_tab, campaign, _state, _sub):
        campaign.stop_requested = True
        return None

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="interleaved",
        pre_post_hook=pre_post_hook,
    )

    _assert(len(state["post_calls"]) == 0,
            "campaign should not submit a post after stop during pre-post browse")
    _assert(campaign.status == "stopped",
            f"expected campaign status stopped, got {campaign.status!r}")
    _assert(any("before post submit" in m for m in tab._logs),
            f"expected stop-before-post log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_pre_post_browse_exception_is_stopped():
    def pre_post_hook(_tab, campaign, _state, _sub):
        campaign.stop_requested = True
        raise RuntimeError("page closed during pre-post browse")

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="interleaved",
        pre_post_hook=pre_post_hook,
    )

    _assert(len(state["post_calls"]) == 0,
            "campaign should not submit a post when stop-triggered pre-post browse raises")
    _assert(campaign.status == "stopped",
            f"expected campaign status stopped, got {campaign.status!r}")
    _assert(not any(status == "error" for _, status, _ in tab._status_updates),
            f"stop during pre-post browse should not surface as error: {tab._status_updates}")
    _assert(any("during pre-post browse" in m for m in tab._logs),
            f"expected stop-during-pre-post-browse log, got {tab._logs}")


def case_auto_poster_campaign_rate_limit_wait_stop_finalizes_stopped():
    def fake_check_post_result(_tab, _campaign, _state):
        return "rate_limited", "slow down"

    def fake_wait_with_stop(_tab, campaign, _state, _seconds, _label):
        campaign.stop_requested = True
        return False

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        check_post_result_hook=fake_check_post_result,
        wait_with_stop_hook=fake_wait_with_stop,
    )

    _assert(len(state["post_calls"]) == 1,
            f"expected one attempted post before rate-limit stop, got {len(state['post_calls'])}")
    _assert(campaign.status == "stopped",
            f"expected campaign status stopped after rate-limit stop, got {campaign.status!r}")
    _assert(not any(status.startswith("done") for _, status, _ in tab._status_updates),
            f"campaign should not finalize as done after rate-limit stop: {tab._status_updates}")
    _assert(any(len(args) > 5 and args[5] == "rate_limited" for args, _kwargs in state["add_posts"]),
            f"expected rate_limited add_post record, got {state['add_posts']}")


def case_auto_poster_campaign_inter_post_wait_stop_finalizes_stopped():
    two_posts = [
        {
            "sub_name": "testsub",
            "title": "Post 1",
            "file_path": "C:\\tmp\\1.gif",
            "file_hash": "hash-1",
            "selected": True,
        },
        {
            "sub_name": "testsub",
            "title": "Post 2",
            "file_path": "C:\\tmp\\2.gif",
            "file_hash": "hash-2",
            "selected": True,
        },
    ]

    def wait_between_hook(_tab, campaign, _state, _stop_checker):
        campaign.stop_requested = True
        return False

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        posting_plan=two_posts,
        wait_between_hook=wait_between_hook,
    )

    _assert(len(state["post_calls"]) == 1,
            f"stop during inter-post wait should prevent second post, got {len(state['post_calls'])}")
    _assert(campaign.status == "stopped",
            f"expected campaign status stopped, got {campaign.status!r}")
    _assert(any("Stopped during inter-post wait" in m for m in tab._logs),
            f"expected inter-post wait stop log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_post_result_exception_finalizes_stopped():
    def fake_check_post_result(_tab, campaign, _state):
        campaign.stop_requested = True
        raise RuntimeError("Target page, context or browser has been closed")

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        check_post_result_hook=fake_check_post_result,
    )

    _assert(len(state["post_calls"]) == 1,
            "campaign should have attempted one post before post-result exception")
    _assert(campaign.status == "stopped",
            f"expected stopped campaign status, got {campaign.status!r}")
    _assert(not any(status == "error" for _, status, _ in tab._status_updates),
            f"stop-triggered post-result exception should not surface as error: {tab._status_updates}")
    _assert(any("during post result check" in m for m in tab._logs),
            f"expected post-result stop log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_result_recording_exception_finalizes_stopped():
    two_posts = [
        {
            "sub_name": "testsub",
            "title": "Post 1",
            "file_path": "C:\\tmp\\1.gif",
            "file_hash": "hash-1",
            "selected": True,
        },
        {
            "sub_name": "testsub",
            "title": "Post 2",
            "file_path": "C:\\tmp\\2.gif",
            "file_hash": "hash-2",
            "selected": True,
        },
    ]

    def add_post_hook(_tab, campaign, _state, _args, _kwargs):
        campaign.stop_requested = True
        raise RuntimeError("sqlite busy during stop")

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        posting_plan=two_posts,
        add_post_hook=add_post_hook,
    )

    _assert(len(state["post_calls"]) == 1,
            f"stop during result recording should prevent second post, got {len(state['post_calls'])}")
    _assert(campaign.status == "stopped",
            f"expected stopped campaign status, got {campaign.status!r}")
    _assert(not any(status == "error" for _, status, _ in tab._status_updates),
            f"stop-triggered result-recording exception should not surface as error: {tab._status_updates}")
    _assert(any("during result recording" in m for m in tab._logs),
            f"expected result-recording stop log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_rate_limit_wait_exception_finalizes_stopped():
    def fake_check_post_result(_tab, _campaign, _state):
        return "rate_limited", "slow down"

    def fake_wait_with_stop(_tab, campaign, _state, _seconds, _label):
        campaign.stop_requested = True
        raise RuntimeError("rate-limit wait interrupted by stop")

    tab, campaign, _state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        check_post_result_hook=fake_check_post_result,
        wait_with_stop_hook=fake_wait_with_stop,
    )

    _assert(campaign.status == "stopped",
            f"expected stopped campaign status, got {campaign.status!r}")
    _assert(not any(status == "error" for _, status, _ in tab._status_updates),
            f"stop-triggered rate-limit wait exception should not surface as error: {tab._status_updates}")
    _assert(any("during rate-limit wait" in m for m in tab._logs),
            f"expected rate-limit wait stop log, got {tab._logs}")


def case_auto_poster_campaign_stop_during_inter_post_wait_exception_finalizes_stopped():
    two_posts = [
        {
            "sub_name": "testsub",
            "title": "Post 1",
            "file_path": "C:\\tmp\\1.gif",
            "file_hash": "hash-1",
            "selected": True,
        },
        {
            "sub_name": "testsub",
            "title": "Post 2",
            "file_path": "C:\\tmp\\2.gif",
            "file_hash": "hash-2",
            "selected": True,
        },
    ]

    def wait_between_hook(_tab, campaign, _state, _stop_checker):
        campaign.stop_requested = True
        raise RuntimeError("page closed during inter-post wait")

    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        posting_plan=two_posts,
        wait_between_hook=wait_between_hook,
    )

    _assert(len(state["post_calls"]) == 1,
            f"stop during inter-post wait exception should prevent second post, got {len(state['post_calls'])}")
    _assert(campaign.status == "stopped",
            f"expected stopped campaign status, got {campaign.status!r}")
    _assert(not any(status == "error" for _, status, _ in tab._status_updates),
            f"stop-triggered inter-post wait exception should not surface as error: {tab._status_updates}")
    _assert(any("during inter-post wait" in m for m in tab._logs),
            f"expected inter-post wait stop log, got {tab._logs}")


def case_auto_poster_campaign_inter_post_wait_exception_without_stop_is_error():
    two_posts = [
        {
            "sub_name": "testsub",
            "title": "Post 1",
            "file_path": "C:\\tmp\\1.gif",
            "file_hash": "hash-1",
            "selected": True,
        },
        {
            "sub_name": "testsub",
            "title": "Post 2",
            "file_path": "C:\\tmp\\2.gif",
            "file_hash": "hash-2",
            "selected": True,
        },
    ]

    def wait_between_hook(_tab, _campaign, _state, _stop_checker):
        raise RuntimeError("inter-post wait helper failed")

    tab, _campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        posting_plan=two_posts,
        wait_between_hook=wait_between_hook,
    )

    _assert(len(state["post_calls"]) == 1,
            "inter-post wait exception path should occur after first post")
    _assert(any(status == "error" for _, status, _ in tab._status_updates),
            f"non-stop inter-post wait exception should surface as error: {tab._status_updates}")


def case_auto_poster_campaign_post_result_exception_without_stop_records_failed_and_completes():
    def fake_check_post_result(_tab, _campaign, _state):
        raise RuntimeError("post-result parser error")

    tab, _campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        check_post_result_hook=fake_check_post_result,
    )

    _assert(len(state["post_calls"]) == 1,
            "post-result exception case should still attempt one post")
    _assert(any(len(args) > 5 and args[5] == "failed" for args, _kwargs in state["add_posts"]),
            f"post-result exception should record failed post, got {state['add_posts']}")
    _assert(any(status.startswith("done") for _, status, _ in tab._status_updates),
            f"single-post parser exception should still finalize campaign loop as done, got {tab._status_updates}")


def case_auto_poster_posting_complete_stop_uses_stopped_wording_and_skips_perf_schedule():
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_posting_complete()
    tab.stop_all = True
    tab._posting_stop_requested = True

    ap_mod.AutoPosterTab._posting_complete(tab)

    _assert(tab.is_running is False, "_posting_complete should clear running flag")
    _assert(tab.stop_all is False, "_posting_complete should clear stop_all flag")
    _assert(any((c.get("text") or "") == "Campaign run stopped" for c in tab.progress_label.config_calls),
            f"expected stopped progress label, got {tab.progress_label.config_calls}")
    _assert(any("ALL CAMPAIGNS STOPPED" in m for m in tab._logs),
            f"expected stopped completion log, got {tab._logs}")
    _assert(tab._start_perf_timer_calls == 0,
            f"stopped completion should not start perf timer, got {tab._start_perf_timer_calls}")
    _assert(not any(delay == 15 * 60 * 1000 for delay, *_rest in tab.app.after_calls),
            f"stopped completion should not schedule delayed perf check, got {tab.app.after_calls}")


def case_auto_poster_posting_complete_success_schedules_perf_checks():
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_posting_complete()

    ap_mod.AutoPosterTab._posting_complete(tab)

    _assert(any((c.get("text") or "") == "All campaigns complete" for c in tab.progress_label.config_calls),
            f"expected completion progress label, got {tab.progress_label.config_calls}")
    _assert(any("ALL CAMPAIGNS COMPLETE" in m for m in tab._logs),
            f"expected completion log, got {tab._logs}")
    _assert(tab._start_perf_timer_calls == 1,
            f"successful completion should start perf timer once, got {tab._start_perf_timer_calls}")
    _assert(any(delay == 15 * 60 * 1000 for delay, *_rest in tab.app.after_calls),
            f"expected delayed perf check scheduling, got {tab.app.after_calls}")


def case_auto_poster_stop_everything_sets_posting_stop_flag_and_propagates():
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    campaign_warmer = SimpleNamespace(stop_requested=False)
    warmup_warmer = SimpleNamespace(stop_requested=False)
    tab.campaigns = [SimpleNamespace(stop_requested=False, warmer=campaign_warmer)]
    tab._active_warmups = {
        "pid": {"warmer": warmup_warmer, "stop": False},
    }
    tab._posting_stop_requested = False

    ap_mod.AutoPosterTab._stop_everything(tab)

    _assert(tab.stop_all is True, "_stop_everything should set stop_all=True")
    _assert(tab._posting_stop_requested is True,
            "_stop_everything should persist posting-stop flag for final completion wording")
    _assert(tab.campaigns[0].stop_requested is True,
            "_stop_everything should mark campaigns as stop requested")
    _assert(campaign_warmer.stop_requested is True,
            "_stop_everything should propagate stop to campaign warmer")
    _assert(tab._active_warmups["pid"]["stop"] is True,
            "_stop_everything should mark active warmup entry as stopped")
    _assert(warmup_warmer.stop_requested is True,
            "_stop_everything should propagate stop to standalone warmer")


def case_auto_poster_stop_single_account_marks_posting_run_stopped():
    from gui import auto_poster_tab as ap_mod

    tab = _make_min_auto_poster_for_campaign()
    campaign_warmer = SimpleNamespace(stop_requested=False)
    tab._acct_map = {"acct (pid-one)": ("pid-one", "acct")}
    tab._active_warmups = {}
    tab.campaigns = [
        SimpleNamespace(profile_id="pid-one", stop_requested=False, warmer=campaign_warmer),
    ]
    tab._posting_stop_requested = False

    ap_mod.AutoPosterTab._stop_single_account(tab, "acct (pid-one)")

    _assert(tab._posting_stop_requested is True,
            "_stop_single_account should preserve stopped completion semantics for posting runs")
    _assert(tab.campaigns[0].stop_requested is True,
            "_stop_single_account should mark matching campaign as stop requested")
    _assert(campaign_warmer.stop_requested is True,
            "_stop_single_account should propagate stop to active campaign warmer")


def case_auto_poster_campaign_exception_after_warmer_attach_clears_ref():
    tab, campaign, state = _run_auto_poster_campaign_worker_smoke(
        warmup_mode="none",
        warmer_get_day_exc=RuntimeError("boom after warmer attach"),
    )

    _assert(campaign.warmer is None,
            "campaign.warmer should be cleared in finally after mid-startup exception")
    _assert(any("/browser/stop" in c for c in state["request_calls"]),
            "AdsPower browser should be stopped after mid-startup exception")
    _assert(("P", campaign.profile_id) in tab._released,
            "proxy group should be released after mid-startup exception")
    _assert(any(status == "error" for _, status, _ in tab._status_updates),
            f"expected error status for injected exception, got {tab._status_updates}")


def main():
    cases = [
        ("account_warmer browser_closed feed-load classification", case_account_warmer_browser_closed_feed_load),
        ("WarmupTab single bad AdsPower response -> explicit failure stats", case_warmup_tab_single_bad_adspower_response_stats),
        ("WarmupTab single malformed AdsPower JSON -> explicit failure stats", case_warmup_tab_single_malformed_json_classified),
        ("WarmupTab single nested AdsPower payload anomaly cleans browser", case_warmup_tab_single_nested_payload_type_cleans_browser),
        ("WarmupTab single missing CDP + stop failure does not crash", case_warmup_tab_single_missing_cdp_stop_failure_no_crash),
        ("WarmupTab single no-live-page after CDP connect cleans browser", case_warmup_tab_single_no_live_page_after_connect_cleans_browser),
        ("WarmupTab single stop before Playwright connect", case_warmup_tab_single_stop_before_connect),
        ("WarmupTab single AdsPower start timeout classification", case_warmup_tab_single_start_timeout_classified),
        ("WarmupTab Run All non-dict AdsPower response returns failed (no crash)", case_warmup_tab_run_all_non_dict_adspower_response_no_crash),
        ("WarmupTab Run All malformed nested AdsPower payload cleans browser", case_warmup_tab_run_all_nested_payload_type_cleans_browser),
        ("WarmupTab Run All stop before Playwright connect cleans browser", case_warmup_tab_run_all_stop_before_connect_cleans_browser),
        ("WarmupTab Run All stop during CDP retry returns stopped + cleans browser", case_warmup_tab_run_all_stop_during_cdp_retry_cleans_browser),
        ("WarmupTab Run All Playwright import failure returns error + cleans browser", case_warmup_tab_run_all_playwright_import_error_cleans_browser_and_classifies),
        ("WarmupTab proxy rotation settle wait is stop-interruptible", case_warmup_tab_rotate_proxy_stop_during_settle_wait_returns_none),
        ("WarmupTab Run All daily-cap shared state uses locks/cache path", case_warmup_tab_run_group_cycle_daily_cap_shared_state_uses_lock),
        ("WarmupTab Run All group progress reads shared snapshot under lock", case_warmup_tab_update_group_progress_uses_locked_snapshot),
        ("WarmupTab Run All completion summary reads shared state under lock", case_warmup_tab_on_run_all_complete_reads_shared_state_under_lock),
        ("WarmupTab Run All cycle status reads at-cap set under lock", case_warmup_tab_update_cycle_status_reads_accounts_at_cap_under_lock),
        ("WarmupTab Run All stop snapshots active warmers under lock", case_warmup_tab_stop_run_all_reads_group_warmers_under_lock),
        ("WarmupTab Run All reset_today clears shared state under lock", case_warmup_tab_reset_today_clears_shared_state_under_lock),
        ("WarmupTab Run All initial rotation failure sets flag under lock", case_warmup_tab_run_group_cycle_initial_rotation_failure_sets_flag_under_lock),
        ("AutoPoster standalone stop after proxy rotation skips browser start", case_auto_poster_standalone_stop_after_rotation_skips_start),
        ("AutoPoster standalone AdsPower start timeout classification", case_auto_poster_standalone_start_timeout_classified),
        ("AutoPoster standalone malformed AdsPower JSON -> explicit failure stats", case_auto_poster_standalone_malformed_json_classified),
        ("AutoPoster standalone malformed nested AdsPower payload cleans browser", case_auto_poster_standalone_nested_payload_type_cleans_browser),
        ("AutoPoster standalone missing CDP + stop failure does not crash", case_auto_poster_standalone_missing_cdp_stop_failure_no_crash),
        ("AutoPoster standalone stop during proxy rotation wait reports stopped", case_auto_poster_standalone_stop_during_proxy_rotation_wait_reports_stopped),
        ("AutoPoster standalone no-live-page after connect cleans browser", case_auto_poster_standalone_no_live_page_after_connect_cleans_browser),
        ("AutoPoster campaign stop before Playwright connect cleans + releases", case_auto_poster_campaign_stop_before_connect_cleans_and_releases),
        ("AutoPoster campaign stop during proxy rotation wait is stopped", case_auto_poster_campaign_stop_during_proxy_rotation_wait_stopped),
        ("AutoPoster campaign malformed nested AdsPower payload cleans + releases", case_auto_poster_campaign_nested_payload_type_cleans_and_releases),
        ("AutoPoster campaign no-live-page after connect cleans + releases", case_auto_poster_campaign_no_live_page_after_connect_cleans_and_releases),
        ("AutoPoster campaign stop before browser start (no proxy group) skips start", case_auto_poster_campaign_stop_before_browser_start_without_proxy_group),
        ("AutoPoster campaign stop after interleaved browse skips post", case_auto_poster_campaign_stop_after_interleaved_browse_before_post_skips_post),
        ("AutoPoster campaign stop during pre-post browse skips post", case_auto_poster_campaign_stop_during_pre_post_browse_skips_post),
        ("AutoPoster campaign stop+exception during pre-post browse finalizes stopped", case_auto_poster_campaign_stop_during_pre_post_browse_exception_is_stopped),
        ("AutoPoster campaign stop during rate-limit wait finalizes stopped", case_auto_poster_campaign_rate_limit_wait_stop_finalizes_stopped),
        ("AutoPoster campaign stop during inter-post wait finalizes stopped", case_auto_poster_campaign_inter_post_wait_stop_finalizes_stopped),
        ("AutoPoster campaign stop during post-result exception finalizes stopped", case_auto_poster_campaign_stop_during_post_result_exception_finalizes_stopped),
        ("AutoPoster campaign stop during result-recording exception finalizes stopped", case_auto_poster_campaign_stop_during_result_recording_exception_finalizes_stopped),
        ("AutoPoster campaign stop during rate-limit wait exception finalizes stopped", case_auto_poster_campaign_stop_during_rate_limit_wait_exception_finalizes_stopped),
        ("AutoPoster campaign stop during inter-post wait exception finalizes stopped", case_auto_poster_campaign_stop_during_inter_post_wait_exception_finalizes_stopped),
        ("AutoPoster campaign non-stop inter-post wait exception surfaces error", case_auto_poster_campaign_inter_post_wait_exception_without_stop_is_error),
        ("AutoPoster campaign non-stop post-result exception records failed and completes", case_auto_poster_campaign_post_result_exception_without_stop_records_failed_and_completes),
        ("AutoPoster campaign exception after warmer attach clears ref + cleanup", case_auto_poster_campaign_exception_after_warmer_attach_clears_ref),
        ("AutoPoster posting completion wording/timers for stopped runs", case_auto_poster_posting_complete_stop_uses_stopped_wording_and_skips_perf_schedule),
        ("AutoPoster posting completion wording/timers for completed runs", case_auto_poster_posting_complete_success_schedules_perf_checks),
        ("AutoPoster stop-everything propagates persistent stop flags", case_auto_poster_stop_everything_sets_posting_stop_flag_and_propagates),
        ("AutoPoster single-account stop marks posting run as stopped", case_auto_poster_stop_single_account_marks_posting_run_stopped),
        ("AutoPoster proxy rotation settle wait is stop-interruptible", case_auto_poster_rotate_proxy_stop_during_settle_wait_returns_none),
        ("AutoPoster rate-limit wait helper interrupts on stop", case_auto_poster_wait_with_stop_interrupts_rate_limit_wait),
        ("AutoPoster account-row status treats connecting campaigns as active posting", case_auto_poster_account_row_connecting_state_is_active_posting),
        ("AutoPoster proxy-group lock contention", case_auto_poster_proxy_group_lock_contention),
    ]

    passed = 0
    failed = 0
    for name, fn in cases:
        try:
            fn()
            print(f"PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {name} -> {e}")
            failed += 1

    print(f"\nSummary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
