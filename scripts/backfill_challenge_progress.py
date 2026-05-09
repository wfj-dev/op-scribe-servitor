#!/usr/bin/env python3
"""Backfill challenge progress from existing AAR records.

This script processes all existing AAR records and populates challenge_progress.json
with tracked mission completions for various challenge roles.
"""

import sys
import os
import asyncio
import json
from pathlib import Path

# Add parent directory to path to import opscribe modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import discord
from discord.ext import commands

from opscribe.constants import (
    PIPEHITTER_ELIGIBLE_MISSIONS,
    PIPEHITTER_ROLE_ID,
    DISTINGUISHED_PIPEHITTER_ROLE_ID,
    KADAKU_CAMPAIGN_REQUIRED_MISSIONS,
    KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
    BLACK_REEF_REQUIRED_MISSIONS,
    BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    BLACK_LAURELS_ROLE_ID,
    CRUX_TERMINATUS_ROLE_ID,
    ORDER_OMEGA_REQUIRED_MISSIONS,
    THE_ORDER_OMEGA_ROLE_ID,
    TERMINUS_SLAYER_ROLE_IDS,
    CHALLENGE_PROGRESS_PATH,
    AAR_RECORDS_PATH,
)

# Get Discord token from environment
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')


def load_aar_records():
    """Load all AAR records from file."""
    try:
        with open(AAR_RECORDS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading AAR records: {e}")
        return {}


def save_challenge_progress(progress_data):
    """Save challenge progress to file."""
    try:
        tmp_path = CHALLENGE_PROGRESS_PATH + ".tmp"
        bak_path = CHALLENGE_PROGRESS_PATH + ".bak"
        
        with open(tmp_path, 'w') as f:
            json.dump(progress_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        
        if os.path.exists(CHALLENGE_PROGRESS_PATH):
            try:
                os.replace(CHALLENGE_PROGRESS_PATH, bak_path)
            except Exception:
                pass
        
        os.replace(tmp_path, CHALLENGE_PROGRESS_PATH)
        print(f"✓ Saved challenge progress to {CHALLENGE_PROGRESS_PATH}")
    except Exception as e:
        print(f"Error saving challenge progress: {e}")


async def backfill_challenge_progress(guild: discord.Guild):
    """Process all AAR records and populate challenge progress."""
    print("Loading AAR records...")
    aar_records = load_aar_records()
    print(f"Found {len(aar_records)} AAR records")
    
    # Initialize progress tracking
    progress_data = {}
    
    # Sort AARs by timestamp (oldest first) for chronological processing
    sorted_aars = sorted(
        aar_records.items(),
        key=lambda x: x[1].get('timestamp', ''),
    )
    
    print("Processing AARs for challenge tracking...")
    processed = 0
    
    for aar_id, record in sorted_aars:
        processed += 1
        if processed % 100 == 0:
            print(f"  Processed {processed}/{len(sorted_aars)} AARs...")
        
        # Extract AAR fields
        mission_name = (record.get('mission') or record.get('mission_name') or '').lower()
        # Clean mission name (remove role tags)
        mission_name = mission_name.split('<')[0].strip() if '<' in mission_name else mission_name.strip()
        
        brother_ids = record.get('brother_ids', [])
        message_url = record.get('message_url', '')
        timestamp = record.get('timestamp', '')
        
        # Tag detection
        pipehitter_mentioned = record.get('pipehitter_mentioned', False)
        leviathan_protocol = record.get('leviathan_protocol_in_mission', False)
        black_reef_persecution = record.get('black_reef_persecution_in_mission', False)
        black_laurels = record.get('black_laurels_in_mission', False) or record.get('black_laurels_in_difficulty', False)
        difficulty_class = record.get('difficulty_class') or ''
        
        # Skip if no mission name or no participants
        if not mission_name or not brother_ids:
            continue
        
        # Process each brother in the AAR
        for brother_id in brother_ids:
            user_id_str = str(brother_id)
            member = guild.get_member(int(brother_id))
            
            # Skip if member not in guild (left server)
            if not member:
                continue
            
            # Initialize user progress if needed
            if user_id_str not in progress_data:
                progress_data[user_id_str] = {
                    'display_name': member.display_name,
                    'notified': []
                }
            
            user_progress = progress_data[user_id_str]
            
            # Update display name
            user_progress['display_name'] = member.display_name
            
            # === SOK-G: Pipehitter tracking ===
            if pipehitter_mentioned and mission_name in PIPEHITTER_ELIGIBLE_MISSIONS:
                # Skip if already has the role
                if discord.utils.get(member.roles, id=PIPEHITTER_ROLE_ID) or \
                   discord.utils.get(member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID):
                    continue
                
                # Check if team has existing Pipehitter
                team_has_pipehitter = False
                for other_brother_id in brother_ids:
                    other_member = guild.get_member(int(other_brother_id))
                    if other_member and (
                        discord.utils.get(other_member.roles, id=PIPEHITTER_ROLE_ID) or
                        discord.utils.get(other_member.roles, id=DISTINGUISHED_PIPEHITTER_ROLE_ID)
                    ):
                        team_has_pipehitter = True
                        break
                
                if team_has_pipehitter:
                    if 'sok_g_pipehitter' not in user_progress:
                        user_progress['sok_g_pipehitter'] = []
                    
                    # Check if mission already tracked
                    existing_missions = {m['mission'] for m in user_progress['sok_g_pipehitter']}
                    if mission_name not in existing_missions:
                        user_progress['sok_g_pipehitter'].append({
                            'mission': mission_name,
                            'aar_id': aar_id,
                            'message_url': message_url,
                            'timestamp': timestamp,
                        })
            
            # === Kadaku Campaign tracking ===
            if leviathan_protocol and mission_name in KADAKU_CAMPAIGN_REQUIRED_MISSIONS:
                # Skip if already has the role
                if discord.utils.get(member.roles, id=KADAKU_CAMPAIGN_MEDAL_ROLE_ID):
                    continue
                
                if 'kadaku_campaign' not in user_progress:
                    user_progress['kadaku_campaign'] = []
                
                existing_missions = {m['mission'] for m in user_progress['kadaku_campaign']}
                if mission_name not in existing_missions:
                    user_progress['kadaku_campaign'].append({
                        'mission': mission_name,
                        'aar_id': aar_id,
                        'message_url': message_url,
                        'timestamp': timestamp,
                    })
            
            # === Black Reef tracking ===
            if black_reef_persecution and mission_name in BLACK_REEF_REQUIRED_MISSIONS:
                # Skip if already has the role
                if discord.utils.get(member.roles, id=BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID):
                    continue
                
                if 'black_reef' not in user_progress:
                    user_progress['black_reef'] = []
                
                existing_missions = {m['mission'] for m in user_progress['black_reef']}
                if mission_name not in existing_missions:
                    user_progress['black_reef'].append({
                        'mission': mission_name,
                        'aar_id': aar_id,
                        'message_url': message_url,
                        'timestamp': timestamp,
                    })
            
            # === Distinguished Black Reef tracking ===
            if black_reef_persecution and black_laurels and mission_name in BLACK_REEF_REQUIRED_MISSIONS:
                # Skip if already has the role
                if discord.utils.get(member.roles, id=DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID):
                    continue
                
                if 'black_reef_distinguished' not in user_progress:
                    user_progress['black_reef_distinguished'] = []
                
                existing_missions = {m['mission'] for m in user_progress['black_reef_distinguished']}
                if mission_name not in existing_missions:
                    user_progress['black_reef_distinguished'].append({
                        'mission': mission_name,
                        'aar_id': aar_id,
                        'message_url': message_url,
                        'timestamp': timestamp,
                    })
            
            # === The Order Omega tracking ===
            if black_laurels and difficulty_class == 'omega_ops' and mission_name in ORDER_OMEGA_REQUIRED_MISSIONS:
                # Skip if already has the role
                if discord.utils.get(member.roles, id=THE_ORDER_OMEGA_ROLE_ID):
                    continue
                
                if 'order_omega' not in user_progress:
                    user_progress['order_omega'] = []
                
                existing_missions = {m['mission'] for m in user_progress['order_omega']}
                if mission_name not in existing_missions:
                    user_progress['order_omega'].append({
                        'mission': mission_name,
                        'aar_id': aar_id,
                        'message_url': message_url,
                        'timestamp': timestamp,
                    })
    
    print(f"✓ Processed {processed} AAR records")
    
    # Print summary
    print("\n=== Challenge Progress Summary ===")
    
    users_with_progress = 0
    for user_id, user_data in progress_data.items():
        has_progress = any(k != 'notified' and k != 'display_name' for k in user_data.keys())
        if has_progress:
            users_with_progress += 1
    
    print(f"Users with challenge progress: {users_with_progress}")
    
    # Count challenge types
    challenge_counts = {
        'sok_g_pipehitter': 0,
        'kadaku_campaign': 0,
        'black_reef': 0,
        'black_reef_distinguished': 0,
        'order_omega': 0,
    }
    
    for user_data in progress_data.values():
        for challenge_key in challenge_counts.keys():
            if challenge_key in user_data and user_data[challenge_key]:
                challenge_counts[challenge_key] += 1
    
    print("\nUsers tracking each challenge:")
    print(f"  SOK-G Pipehitter: {challenge_counts['sok_g_pipehitter']}")
    print(f"  Kadaku Campaign: {challenge_counts['kadaku_campaign']}")
    print(f"  Black Reef: {challenge_counts['black_reef']}")
    print(f"  Distinguished Black Reef: {challenge_counts['black_reef_distinguished']}")
    print(f"  Order Omega: {challenge_counts['order_omega']}")
    
    # Save progress
    save_challenge_progress(progress_data)
    
    return progress_data


async def main():
    """Main entry point."""
    print("=== Challenge Progress Backfill Script ===\n")
    
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found in environment")
        return 1
    
    # Create bot client
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    
    bot = commands.Bot(command_prefix='!', intents=intents)
    
    @bot.event
    async def on_ready():
        print(f"✓ Connected as {bot.user}")
        
        # Get the guild (assuming single guild)
        if not bot.guilds:
            print("ERROR: Bot is not in any guilds")
            await bot.close()
            return
        
        guild = bot.guilds[0]
        print(f"✓ Using guild: {guild.name} (ID: {guild.id})")
        print(f"  Members: {guild.member_count}")
        
        try:
            # Run backfill
            await backfill_challenge_progress(guild)
            print("\n✓ Backfill complete!")
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Close the bot
            await bot.close()
    
    # Run bot with proper cleanup
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception:
        pass  # Bot already closed in on_ready
    
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
