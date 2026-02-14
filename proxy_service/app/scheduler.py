import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from typing import List, Optional
import datetime

from app.integrations.nyt import NYTClient
from app.database import (
    backup_jobs_collection, 
    settings_collection, 
    get_all_nyt_subscriptions, 
    update_nyt_subscription_last_run, 
    save_imported_list
)
from app.database.backup import create_backup, cleanup_old_backups

# Global Scheduler Instance
scheduler = AsyncIOScheduler()

async def sync_nyt_lists():
    """Fetches and updates all subscribed NYT lists."""
    print("Starting NYT List Sync...")
    subscriptions = await get_all_nyt_subscriptions(enabled_only=True)
    if not subscriptions:
        print("No active NYT subscriptions found.")
        return

    client = NYTClient()
    count = 0
    
    for sub in subscriptions:
        try:
            list_name = sub.get("list_name_encoded")
            display_name = sub.get("display_name")
            
            books = await client.get_list_details(list_name)
            if not books:
                print(f"No books found for NYT list: {list_name}")
                continue
                
            # Parse (Process similar to router)
            raw_items = []
            for book in books:
                item = {
                    "title": book.get("title", "").title(),
                    "author": book.get("author"),
                    "description": book.get("description"),
                    "publisher": book.get("publisher"),
                    "isbn13": book.get("primary_isbn13"),
                    "isbn10": book.get("primary_isbn10"),
                    "rank": book.get("rank"),
                    "weeks_on_list": book.get("weeks_on_list"),
                    "cover": book.get("book_image"),
                    "authors_str": book.get("author"),
                    "cover_image": book.get("book_image") or "/static/img/cover_placeholder.jpg",
                    "asin": None,
                    "primary_isbn13": book.get("primary_isbn13")
                }
                raw_items.append(item)
            
            await save_imported_list(
                name=f"NYT: {display_name}", 
                url=f"nyt://{list_name}", 
                asins=[], 
                source="NYT",
                raw_items=raw_items
            )
            
            await update_nyt_subscription_last_run(list_name)
            count += 1
            print(f"Updated NYT List: {display_name}")
            
        except Exception as e:
            print(f"Failed to sync NYT list {sub.get('list_name_encoded')}: {e}")
            
    print(f"NYT Sync Completed. Updated {count} lists.")

async def run_backup_job(job_id: str, sections: List[str]):
    """Wrapper to run backup from scheduler"""
    print(f"Starting Scheduled Backup: {job_id}")
    try:
        await create_backup(sections, trigger=f"schedule_{job_id}")
    except Exception as e:
        print(f"Scheduled Backup {job_id} Failed: {e}")

async def run_cleanup_job():
    """Wrapper to run cleanup"""
    try:
        # Fetch retention settings
        settings = await settings_collection.find_one({"_id": "system_settings"})
        if settings:
            retention_days = settings.get("backup_retention_days", 0)
            if retention_days > 0:
                print(f"Starting Backup Cleanup (Retention: {retention_days} days)")
                cleaned = await cleanup_old_backups(retention_days)
                print(f"Cleanup Completed: {cleaned} files removed")
    except Exception as e:
        print(f"Cleanup Job Failed: {e}")

async def refresh_scheduler_jobs():
    """
    Reloads all jobs from the database.
    """
    scheduler.remove_all_jobs()
    
    # 1. Add Cleanup Job (Daily at 03:00 UTC)
    scheduler.add_job(
        run_cleanup_job,
        CronTrigger(hour=3, minute=0),
        id="system_cleanup",
        replace_existing=True
    )

    # 2. Add NYT Sync Job (Mondays at 08:00 UTC)
    scheduler.add_job(
        sync_nyt_lists,
        CronTrigger(day_of_week='mon', hour=8, minute=0),
        id="nyt_sync",
        replace_existing=True
    )
    
    # 3. Add User Backup Jobs
    
    # 2. Add User Backup Jobs
    jobs = await backup_jobs_collection.find({"enabled": True}).to_list(length=None)
    for job in jobs:
        try:
            job_id = str(job["_id"])
            sections = job.get("sections", ["full"])
            time_str = job.get("time", "00:00") # "HH:MM"
            schedule_type = job.get("schedule_type", "daily") # daily, weekly
            day_of_week = job.get("day_of_week", "sun") # for weekly (mon,tue,...)
            
            hour, minute = map(int, time_str.split(":"))
            
            trigger = None
            if schedule_type == "daily":
                trigger = CronTrigger(hour=hour, minute=minute)
            elif schedule_type == "weekly":
                 trigger = CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute)
            
            if trigger:
                scheduler.add_job(
                    run_backup_job,
                    trigger,
                    args=[job_id, sections],
                    id=job_id,
                    replace_existing=True
                )
        except Exception as e:
            print(f"Failed to schedule job {job.get('_id')}: {e}")

    print(f"Scheduler refreshed. Total jobs: {len(scheduler.get_jobs())}")

def start_scheduler():
    """Starts the scheduler if not already running."""
    if not scheduler.running:
        scheduler.start()
        print("Backup Scheduler Started")
