"""
Launch the daily scheduler daemon.

Usage:
    python run_scheduler.py                 # Run daemon (checks every 60s)
    python run_scheduler.py --plan-only     # Show today's plan and exit
    python run_scheduler.py --dry-run       # Generate plan, show it, don't execute
    python run_scheduler.py --status        # Show today's task status
    python run_scheduler.py --history       # Show last 7 days summary
"""

import sys
import argparse
import logging
from datetime import date

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(name)s %(levelname)s %(message)s",
)


def main():
    parser = argparse.ArgumentParser(
        description="Daily scheduler for account warmup and posting")
    parser.add_argument("--plan-only", action="store_true",
                        help="Show today's plan and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate plan but don't execute")
    parser.add_argument("--status", action="store_true",
                        help="Show current status and exit")
    parser.add_argument("--history", action="store_true",
                        help="Show last 7 days summary")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to schedule_config.json")
    args = parser.parse_args()

    from core.daily_scheduler import DailyScheduler

    scheduler = DailyScheduler(config_path=args.config)

    if args.status:
        status = scheduler.get_status()
        print(f"\nScheduler status for {status['date']}:")
        print(f"  Pending:  {status['pending']}")
        print(f"  Running:  {status['running']}")
        print(f"  Done:     {status['done']}")
        print(f"  Failed:   {status['failed']}")
        print(f"  Skipped:  {status['skipped']}")
        return

    if args.history:
        history = scheduler.get_history(days=7)
        if not history:
            print("No scheduler history found.")
            return
        current_date = ""
        for row in history:
            if row["date"] != current_date:
                current_date = row["date"]
                print(f"\n  {current_date}:")
            print(f"    {row['task_type']:8s} {row['status']:8s} × {row['cnt']}")
        print()
        return

    # Generate / load plan
    plan = scheduler.generate_daily_plan()

    print(f"\n{'=' * 60}")
    print(f"  Daily Schedule — {date.today()}")
    print(f"  Active window: "
          f"{scheduler.config['active_hours']['start']} – "
          f"{scheduler.config['active_hours']['end']}")
    print(f"  Accounts: {len(scheduler._get_enabled_accounts())}")
    print(f"  Tasks: {len(plan)}")
    print(f"{'=' * 60}\n")
    scheduler.print_plan(plan)
    print()

    if args.plan_only or args.dry_run:
        if args.dry_run:
            print("[DRY RUN] Would execute the above tasks at scheduled times.\n")
        return

    scheduler.run()


if __name__ == "__main__":
    main()
