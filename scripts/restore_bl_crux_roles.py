"""
One-shot recovery: restore Black Laurels and Crux Terminatus roles that were
erroneously stripped by the grace-period enforcer bug (inverted BL flag logic).

Usage (on the server, with DISCORD_TOKEN set):
    python3 scripts/restore_bl_crux_roles.py

What it does:
  1. Restores Black Laurels role to all 55 members listed in AFFECTED_USER_IDS.
  2. Restores Crux Terminatus to anyone who now meets all requirements after BL
     is back (BL role held + Distinguished Pipehitter + 2+ Terminus Slayer
     classes + all post-enforcement BL AARs at Rank A).
  3. Sets black_laurels_notified=True in promotion_tracking.json for every
     restored member so the grace-period sweep doesn't strip them again.
  4. Removes the pending Black Laurels entries from award_announcement_queue.json
     (avoids duplicate public announcements for people who already have the role).
  5. Does NOT post any announcement in Discord — silent role assignment only.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG_PATH = ROOT / "config" / "config.json"
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Role / mission constants (mirrors opscribe/constants.py)
# ---------------------------------------------------------------------------
BLACK_LAURELS_ROLE_ID            = 1440108298115485716
CRUX_TERMINATUS_ROLE_ID          = 1476288996756820109
DISTINGUISHED_PIPEHITTER_ROLE_ID = 1480420419063386275
KILL_LOG_CLASS_ROLE_IDS = {
    1449257352112111646,  # Assault
    1450230789034737748,  # Bulwark
    1450231189028737166,  # Heavy
    1450231020686278656,  # Sniper
    1450230281599713451,  # Tactical
    1476623936254115992,  # Techmarine
    1450230501804609697,  # Vanguard
}
BLACK_LAURELS_REQUIRED_MISSIONS = {
    "inferno", "decapitation", "vox liberatis", "ballistic engine",
    "exfiltration", "termination", "reclamation", "disruption", "purgation",
}
BLACK_LAURELS_STRICT_ENFORCEMENT_DATE = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

# Members who had Order Omega wrongly revoked — fully qualified (all 13 missions
# logged in challenge_progress.json) but stripped by the grace-period run.
ORDER_OMEGA_REVOKED_USER_IDS = [
    1294159011331051530,
    152625031183073281,
]

# ---------------------------------------------------------------------------
# Members confirmed to have had Black Laurels erroneously revoked.
# Source: promotion_tracking.json entries where black_laurels_notified=False.
# All 55 held the role at time of revocation (grace-period code only strips
# members who currently hold the role).
# ---------------------------------------------------------------------------
AFFECTED_USER_IDS = [
    # 9/9 missions — definitely qualified
    1397282700167086141,
    488551994806501396,
    437645667838590976,
    277562824387985408,
    228703987816202240,
    308032160089243649,
    1493784189890465932,
    512474407768293379,
    141769315085975552,
    330463982521679884,
    737764008924545085,
    352606122349428736,
    1059255753900298251,
    734497256534835210,
    281651485782310914,
    775986735477424148,
    592823285713076232,
    301998534754959370,
    710530020803870730,
    440987777039990786,
    624772672349405204,
    737778434478178325,
    226166830006403072,
    333364344710758400,
    405887897171001345,
    266088909467811840,
    1294159011331051530,
    205788575587893249,
    152625031183073281,
    554000605627285514,
    727577031914815508,
    539139736581832710,
    387344967200145412,
    1444810056821637133,
    1107074905993904198,
    571035376135962645,
    922934628145299548,
    477133017769574402,
    444912473280348160,
    428661670710345739,
    283081950804312066,
    226427216475455490,
    200014552505647104,
    357013541586468866,
    1301268611054174251,
    235940504041291776,
    # 8/9 missions — still had the role before bug; restore it
    318123902960140299,
    95276058542080000,
    1317207422510694456,
    1217601361571610716,
    572049142457827328,
    878402547960926238,
    1285774550608642193,
    835352763176190013,
    # 5/9 missions — edge case; included since they held the role at revocation time
    933789838136717414,
]


def _normalize_mission(raw: str) -> str:
    text = re.sub(r"<@&\d+>", "", raw or "").lower().strip()
    return re.split(r"\s*@", text)[0].strip()


def _load_aar_records() -> dict:
    path = DATA_DIR / "aar_records.json"
    with open(path) as f:
        return json.load(f)


def _meets_crux_requirements(member: discord.Member, aar_records: dict) -> tuple[bool, list[str]]:
    """Return (meets_requirements, list_of_failures)."""
    role_ids = {r.id for r in member.roles}
    failures = []

    # Req 1: Black Laurels role held
    if BLACK_LAURELS_ROLE_ID not in role_ids:
        failures.append("Black Laurels role not held")
    else:
        # All post-enforcement BL AARs must be Rank A
        uid_str = str(member.id)
        all_rank_a = True
        saw_any = False
        for rec in aar_records.values():
            if not (rec.get("black_laurels_in_mission") or rec.get("black_laurels_in_difficulty")):
                continue
            mission = _normalize_mission(rec.get("mission") or "")
            if mission not in BLACK_LAURELS_REQUIRED_MISSIONS:
                continue
            if uid_str not in [str(b) for b in (rec.get("brother_ids") or [])]:
                continue
            saw_any = True
            rank = (rec.get("rank") or "").upper()
            if rank != "A":
                ts = rec.get("timestamp", "")
                try:
                    if ts and datetime.fromisoformat(ts) >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
                        all_rank_a = False
                        break
                except Exception:
                    pass
        if saw_any and not all_rank_a:
            failures.append("Black Laurels — not all post-enforcement missions at Rank A")

    # Req 2: Distinguished SOK-G Pipehitter
    if DISTINGUISHED_PIPEHITTER_ROLE_ID not in role_ids:
        failures.append("Distinguished SOK-G: Pipehitter role not held")

    # Req 3: 2+ Terminus Slayer class roles
    ts_count = sum(1 for rid in KILL_LOG_CLASS_ROLE_IDS if rid in role_ids)
    if ts_count < 2:
        failures.append(f"Terminus Slayer classes: {ts_count}/2")

    return (len(failures) == 0), failures


def _fix_challenge_progress_notified(uid: int, key: str) -> None:
    """Re-add a challenge key to the notified list in challenge_progress.json."""
    path = DATA_DIR / "challenge_progress.json"
    with open(path) as f:
        data = json.load(f)
    uid_str = str(uid)
    if uid_str in data:
        notified = data[uid_str].get("notified", [])
        if key not in notified:
            notified.append(key)
            data[uid_str]["notified"] = notified
            with open(path, "w") as f:
                json.dump(data, f, indent=2)


def _fix_promotion_tracking(restored_ids: list[int]) -> None:
    path = DATA_DIR / "promotion_tracking.json"
    with open(path) as f:
        data = json.load(f)
    changed = 0
    for uid in restored_ids:
        uid_str = str(uid)
        if uid_str in data and isinstance(data[uid_str], dict):
            if data[uid_str].get("black_laurels_notified") is False:
                data[uid_str]["black_laurels_notified"] = True
                changed += 1
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[data] promotion_tracking: set black_laurels_notified=True for {changed} members")


def _clear_bl_queue_entries() -> int:
    path = DATA_DIR / "award_announcement_queue.json"
    with open(path) as f:
        queue = json.load(f)
    before = len(queue)
    queue = [e for e in queue if e.get("award_type") != "black_laurels"]
    after = len(queue)
    with open(path, "w") as f:
        json.dump(queue, f, indent=2)
    removed = before - after
    print(f"[data] award_announcement_queue: removed {removed} pending Black Laurels entries")
    return removed


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        sys.exit("ERROR: DISCORD_TOKEN environment variable not set.")

    with open(CONFIG_PATH) as f:
        config = json.load(f)
    guild_id = config.get("guild_id")  # may be None; fall back to first guild

    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        print("Connecting to guild and loading member cache (this can take a few minutes)...")
        try:
            guild = None
            if guild_id:
                guild = client.get_guild(int(guild_id))
                if guild is None:
                    try:
                        guild = await client.fetch_guild(int(guild_id))
                    except Exception:
                        pass
            if guild is None:
                guild = client.guilds[0] if client.guilds else None
            if guild is None:
                print("ERROR: could not resolve guild — bot is not in any guild.")
                await client.close()
                return

            print(f"Guild: {guild.name} ({guild.id}) — loading member cache...")
            await guild.chunk()  # ensure member cache is populated
            print(f"Member cache loaded ({len(guild.members)} members).")

            bl_role = guild.get_role(BLACK_LAURELS_ROLE_ID)
            oo_role = guild.get_role(THE_ORDER_OMEGA_ROLE_ID)
            crux_role = guild.get_role(CRUX_TERMINATUS_ROLE_ID)
            if bl_role is None:
                sys.exit("ERROR: Black Laurels role not found in guild.")
            if crux_role is None:
                print("WARNING: Crux Terminatus role not found — skipping Crux restoration.")

            print(f"\nLoading AAR records for Crux eligibility checks...")
            aar_records = _load_aar_records()
            print(f"Loaded {len(aar_records)} AAR records.")

            bl_restored = 0
            bl_already_had = 0
            bl_not_in_server = 0
            crux_restored = 0
            crux_skipped_ineligible = 0
            oo_restored = 0

            print(f"\nProcessing {len(AFFECTED_USER_IDS)} affected members...\n")

            for uid in AFFECTED_USER_IDS:
                member = guild.get_member(uid)
                if member is None:
                    print(f"  [{uid}] NOT IN SERVER — skipping")
                    bl_not_in_server += 1
                    continue

                name = member.display_name
                member_role_ids = {r.id for r in member.roles}

                # --- Restore Black Laurels ---
                if BLACK_LAURELS_ROLE_ID in member_role_ids:
                    print(f"  [{uid}] {name}: BL already present — skipping assign")
                    bl_already_had += 1
                else:
                    try:
                        await member.add_roles(bl_role, reason="Recovery: BL erroneously revoked by grace-period bug")
                        print(f"  [{uid}] {name}: ✓ Black Laurels restored")
                        bl_restored += 1
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        print(f"  [{uid}] {name}: ERROR adding BL role: {e}")
                        continue

                # Refresh role IDs after potential add
                member = guild.get_member(uid)
                if member is None:
                    continue

                # --- Check and restore Crux Terminatus ---
                if crux_role is None:
                    continue
                if CRUX_TERMINATUS_ROLE_ID in {r.id for r in member.roles}:
                    # Already has it — nothing to do
                    pass
                else:
                    eligible, failures = _meets_crux_requirements(member, aar_records)
                    if eligible:
                        try:
                            await member.add_roles(crux_role, reason="Recovery: Crux Terminatus erroneously revoked (BL bug cascade)")
                            print(f"  [{uid}] {name}: ✓ Crux Terminatus restored")
                            crux_restored += 1
                            await asyncio.sleep(0.4)
                        except Exception as e:
                            print(f"  [{uid}] {name}: ERROR adding Crux role: {e}")
                    else:
                        crux_skipped_ineligible += 1
                        # Only log if they're close (1 failure) to avoid noise
                        if len(failures) == 1:
                            print(f"  [{uid}] {name}: Crux not restored — {failures[0]}")

            # --- Restore Order Omega for the 2 wrongly revoked members ---
            print(f"\nRestoring Order Omega for {len(ORDER_OMEGA_REVOKED_USER_IDS)} members...")
            for uid in ORDER_OMEGA_REVOKED_USER_IDS:
                member = guild.get_member(uid)
                if member is None:
                    print(f"  [{uid}] NOT IN SERVER — skipping OO")
                    continue
                name = member.display_name
                if oo_role is None:
                    print("  WARNING: Order Omega role not found in guild — skipping")
                    break
                if oo_role in member.roles:
                    print(f"  [{uid}] {name}: OO already present — skipping")
                else:
                    try:
                        await member.add_roles(oo_role, reason="Recovery: Order Omega wrongly revoked by grace-period run")
                        _fix_challenge_progress_notified(uid, "order_omega")
                        print(f"  [{uid}] {name}: ✓ Order Omega restored")
                        oo_restored += 1
                        await asyncio.sleep(0.4)
                    except Exception as e:
                        print(f"  [{uid}] {name}: ERROR adding OO role: {e}")

            print(f"\n=== ROLE RESTORATION COMPLETE ===")
            print(f"  Black Laurels restored:      {bl_restored}")
            print(f"  Black Laurels already held:  {bl_already_had}")
            print(f"  Not in server:               {bl_not_in_server}")
            print(f"  Crux Terminatus restored:    {crux_restored}")
            print(f"  Crux ineligible (skipped):   {crux_skipped_ineligible}")
            print(f"  Order Omega restored:        {oo_restored}")

            # Fix data files
            print()
            all_restored_ids = [uid for uid in AFFECTED_USER_IDS
                                 if guild.get_member(uid) is not None]
            _fix_promotion_tracking(all_restored_ids)
            _clear_bl_queue_entries()

            print("\nDone. You can restart the bot now.")

        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
