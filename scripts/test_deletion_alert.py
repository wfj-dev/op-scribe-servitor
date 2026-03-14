#!/usr/bin/env python3
"""Test the AAR deletion notification without actually deleting anything.

Usage:
  python scripts/test_deletion_alert.py           # Preview what alert would look like
  python scripts/test_deletion_alert.py --send    # Actually send a test alert to data-vault
"""
import argparse
import asyncio
import json
import os
import sys

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")


def load_records():
    with open(AAR_RECORDS_PATH, "r") as f:
        return json.load(f)


def build_alert_message(message_id: str, record: dict) -> str:
    """Build the alert message that would be sent on deletion."""
    brother_ids = record.get("brother_ids", [])
    mission = record.get("mission", "Unknown")
    difficulty = record.get("difficulty", "Unknown")
    timestamp = record.get("timestamp", "Unknown")
    author_mention = f"<@{brother_ids[0]}>" if brother_ids else "Unknown"
    
    preserved_content = record.get("content", "")
    if not preserved_content:
        preserved_content = "(No content stored - this AAR predates content preservation)"
    content_preview = preserved_content[:500] + "..." if len(preserved_content) > 500 else preserved_content
    
    alert_lines = [
        "@Watch Command ⚠️ **AAR DELETION DETECTED** (TEST)",
        "",
        f"**Message ID:** `{message_id}`",
        f"**Likely Author:** {author_mention}",
        f"**Mission:** {mission}",
        f"**Difficulty:** {difficulty}",
        f"**Original Timestamp:** {timestamp}",
        "",
        "**Preserved Content:**",
        f"```\n{content_preview}\n```",
        "",
        "*The AAR record remains in the archive. Review whether this deletion was authorized.*",
    ]
    return "\n".join(alert_lines)


def preview_alert(aar_id: str = None):
    """Preview what a deletion alert would look like."""
    records = load_records()
    
    if not records:
        print("No AAR records found.")
        return None, None
    
    if aar_id:
        if aar_id not in records:
            print(f"AAR {aar_id} not found in records.")
            return None, None
        record = records[aar_id]
    else:
        # Pick a recent record for testing
        aar_id = list(records.keys())[-1]
        record = records[aar_id]
        print(f"Using most recent AAR: {aar_id}\n")
    
    alert = build_alert_message(aar_id, record)
    print("=" * 60)
    print("PREVIEW OF DELETION ALERT:")
    print("=" * 60)
    print(alert)
    print("=" * 60)
    print(f"\nAlert length: {len(alert)} chars")
    print(f"Content field present: {'content' in record}")
    
    return aar_id, record


async def send_test_alert(aar_id: str, record: dict):
    """Actually send a test alert to the data-vault channel."""
    import discord
    from bot import bot, CONFIG
    
    token = CONFIG.get("token")
    if not token:
        print("ERROR: No bot token in config.")
        return
    
    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user}")
        
        # Find the guild and channel
        guild = None
        for g in bot.guilds:
            if discord.utils.get(g.channels, name="❖⋅data-vault⋅❖"):
                guild = g
                break
        
        if not guild:
            print("ERROR: Could not find guild with data-vault channel.")
            await bot.close()
            return
        
        notify_channel = discord.utils.get(guild.channels, name="❖⋅data-vault⋅❖")
        if not notify_channel:
            print("ERROR: data-vault channel not found.")
            await bot.close()
            return
        
        alert = build_alert_message(aar_id, record)
        # Mark it clearly as a test
        alert = alert.replace("(TEST)", "(TEST - IGNORE)")
        
        try:
            await notify_channel.send(
                alert,
                allowed_mentions=discord.AllowedMentions(roles=False, users=False),  # No pings for test
            )
            print(f"✅ Test alert sent to #{notify_channel.name}")
        except Exception as e:
            print(f"ERROR sending alert: {e}")
        
        await bot.close()
    
    await bot.start(token)


def main():
    parser = argparse.ArgumentParser(description="Test AAR deletion alert")
    parser.add_argument("--send", action="store_true", help="Actually send a test alert (suppresses pings)")
    parser.add_argument("--aar", type=str, help="Specific AAR ID to use for test")
    args = parser.parse_args()
    
    aar_id, record = preview_alert(args.aar)
    
    if args.send and aar_id and record:
        print("\nSending test alert to data-vault (pings suppressed)...")
        asyncio.run(send_test_alert(aar_id, record))


if __name__ == "__main__":
    main()
