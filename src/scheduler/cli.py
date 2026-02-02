"""CLI for scheduler - start, stop, status, add, scan, errors."""

import os
import sys
import signal
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("Missing dependencies. Run: pip install typer rich")
    sys.exit(1)

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.database import Database
from scheduler.config import load_config
from scheduler.scheduler import Scheduler
from scheduler.sources import scan_local_folder

app = typer.Typer(help="RedGIFs upload scheduler")
console = Console()

PID_FILE = Path(__file__).parent / "scheduler.pid"


def get_config_and_db():
    """Load config and database."""
    config = load_config()
    db = Database(config.database_path)
    return config, db


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def get_running_pid() -> int | None:
    """Get PID of running scheduler, or None if not running."""
    if not PID_FILE.exists():
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
        if is_process_running(pid):
            return pid
    except (ValueError, OSError):
        pass

    # Stale PID file
    PID_FILE.unlink(missing_ok=True)
    return None


@app.command()
def start(daemon: bool = typer.Option(False, "--daemon", "-d", help="Run in background")):
    """Start the scheduler."""
    running_pid = get_running_pid()
    if running_pid:
        console.print(f"[yellow]Scheduler is already running (PID: {running_pid})[/yellow]")
        console.print("Use 'scheduler stop' first")
        return

    if daemon:
        # Start in background
        python_exe = sys.executable
        script_path = Path(__file__).parent / "scheduler.py"

        # On Windows, use pythonw for no console
        if sys.platform == "win32":
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if Path(pythonw).exists():
                python_exe = pythonw

            # DETACHED_PROCESS ensures the child survives parent exit
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            creationflags = 0

        process = subprocess.Popen(
            [python_exe, str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=True if sys.platform != "win32" else False,
        )
        PID_FILE.write_text(str(process.pid))
        console.print(f"[green]Scheduler started in background (PID: {process.pid})[/green]")
    else:
        # Run in foreground
        console.print("[green]Starting scheduler in foreground...[/green]")
        console.print("Press Ctrl+C to stop\n")
        scheduler = Scheduler()
        scheduler.run()


@app.command()
def stop():
    """Stop the running scheduler."""
    running_pid = get_running_pid()
    if not running_pid:
        console.print("[yellow]Scheduler is not running[/yellow]")
        return

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(running_pid), "/F"], capture_output=True)
        else:
            os.kill(running_pid, signal.SIGTERM)
        console.print(f"[green]Scheduler stopped (PID: {running_pid})[/green]")
    except (ProcessLookupError, OSError) as e:
        console.print(f"[yellow]Could not stop process: {e}[/yellow]")

    PID_FILE.unlink(missing_ok=True)


@app.command()
def status():
    """Show scheduler status."""
    config, db = get_config_and_db()

    # Check if running
    running_pid = get_running_pid()
    if running_pid:
        console.print(f"[green]Scheduler: RUNNING[/green] (PID: {running_pid})")
    else:
        console.print("[yellow]Scheduler: STOPPED[/yellow]")

    console.print(f"Mode: {config.schedule_mode}, Posts/day: {config.posts_per_day}")
    console.print(f"Hours: {config.active_hours_start} - {config.active_hours_end}\n")

    # Account stats
    table = Table(title="Account Status")
    table.add_column("Account", style="cyan")
    table.add_column("Today", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("In Queue", justify="right")

    accounts = set(s.account for s in config.sources)
    for account in sorted(accounts):
        uploaded = db.get_uploads_today(account)
        remaining = config.posts_per_day - uploaded
        pending = db.get_pending_count(account)
        table.add_row(
            account,
            f"{uploaded}/{config.posts_per_day}",
            str(remaining),
            str(pending)
        )

    console.print(table)
    db.close()


@app.command()
def add(
    path: str = typer.Argument(..., help="File or folder path"),
    account: str = typer.Option(..., "--account", "-a", help="Account name"),
):
    """Add video(s) to the queue."""
    config, db = get_config_and_db()

    path_obj = Path(path)
    if not path_obj.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        return

    files = []
    if path_obj.is_file():
        files = [path_obj]
    else:
        files = list(scan_local_folder(path))

    if not files:
        console.print("[yellow]No video files found[/yellow]")
        return

    added = 0
    for file_path in files:
        if db.file_in_queue(account, file_path):
            console.print(f"[dim]Already in queue: {file_path.name}[/dim]")
            continue

        # Calculate schedule time
        pending = db.get_pending_count(account)
        uploaded = db.get_uploads_today(account)
        remaining = config.posts_per_day - uploaded

        if pending >= remaining:
            console.print(f"[yellow]Queue full for {account} today[/yellow]")
            break

        # Simple: schedule for now (scheduler will pick it up)
        scheduled_at = datetime.now()
        db.add_to_queue(account, str(file_path), scheduled_at)
        console.print(f"[green]Added: {file_path.name}[/green]")
        added += 1

    console.print(f"\nAdded {added} file(s) to queue")
    db.close()


@app.command()
def scan():
    """Scan all configured sources and add to queue."""
    console.print("Scanning sources...")
    scheduler = Scheduler()
    added = scheduler.scan_and_queue()
    console.print(f"\n[green]Added {added} file(s) to queue[/green]")
    scheduler.db.close()


@app.command()
def errors(limit: int = typer.Option(20, "--limit", "-n", help="Number of errors to show")):
    """Show recent errors."""
    config, db = get_config_and_db()

    error_list = db.get_errors(limit)
    if not error_list:
        console.print("[green]No errors logged[/green]")
        return

    table = Table(title=f"Recent Errors (last {limit})")
    table.add_column("Time", style="dim")
    table.add_column("Account", style="cyan")
    table.add_column("File")
    table.add_column("Type", style="yellow")
    table.add_column("Message", style="red")

    for err in error_list:
        occurred = err["occurred_at"][:16] if err["occurred_at"] else ""
        table.add_row(
            occurred,
            err["account_name"],
            Path(err["file_path"]).name if err["file_path"] else "",
            err["error_type"],
            err["error_message"][:50] + "..." if len(err["error_message"]) > 50 else err["error_message"]
        )

    console.print(table)
    db.close()


@app.command()
def history(
    account: str = typer.Option(None, "--account", "-a", help="Filter by account"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of entries to show"),
):
    """Show upload history."""
    config, db = get_config_and_db()

    history_list = db.get_history(account, limit)
    if not history_list:
        console.print("[dim]No history yet[/dim]")
        return

    table = Table(title=f"Upload History (last {limit})")
    table.add_column("Time", style="dim")
    table.add_column("Account", style="cyan")
    table.add_column("File")
    table.add_column("Status")
    table.add_column("URL/Error")

    for h in history_list:
        completed = h["completed_at"][:16] if h["completed_at"] else ""
        status_style = "green" if h["status"] == "success" else "red"
        url_or_error = h["redgifs_url"] or h["error_message"] or ""
        if len(url_or_error) > 40:
            url_or_error = url_or_error[:40] + "..."

        table.add_row(
            completed,
            h["account_name"],
            Path(h["file_path"]).name,
            f"[{status_style}]{h['status']}[/{status_style}]",
            url_or_error
        )

    console.print(table)
    db.close()


def main():
    app()


if __name__ == "__main__":
    main()
