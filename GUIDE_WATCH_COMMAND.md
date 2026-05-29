# OP-Scribe Servitor — Watch Command Guide

This guide covers commands available to Watch Command staff (Watch Sergeant and above, with specialist roles, Forgemaster, or admin access). Regular member commands are covered in the Watch Brother Guide.

Commands are grouped by function. Access level is noted for each — if the bot says "access denied," the command requires a higher rank or a specific role.

---

## Roster & Records

### `/tally_deeds`
**What it does:** Shows the Deeds Ledger for any brother or a Kill Team roster summary.

**Options:**
- `brother:@User` — Individual brother's lifetime tallies and activity status.
- `killteam:@Role` — Kill Team roster ordered by rank, studs, and AAR points, with a 7-day summary.

**Access:** Watch Command

---

### `/combat_bonds`
**What it does:** Shows the strongest operational bonds between brothers over a chosen time window. Useful for identifying active partnerships and team health.

**Options:**
- `brother:@User` — (Optional) Focus results on a specific brother.
- `window:` — Days to look back (default: 30).

**Access:** Watch Command

---

### `/company_roster`
**What it does:** Shows all Kill Teams and their member counts across the entire Fortress.

**Access:** Watch Command

---

### `/promotion_queue`
**What it does:** Shows brothers who are approaching or have met the requirements for service stud milestones and veteran promotions. Use this to identify who is due for recognition.

**Access:** Watch Command

---

### `/audit_service_studs`
**What it does:** Lists brothers whose displayed service studs don't match what the bot has calculated they're entitled to. Use this to find and correct display mismatches.

**Access:** Watch Command

---

### `/pick_home_chapters`
**What it does:** Randomly selects home chapter(s) for a member from the current rotation pool.

**Options:**
- `member:@User` — The brother to assign.

**Access:** Watch Command

---

### `/set_induction`
**What it does:** Sets or clears a custom induction date for a member. Use when a brother's join date needs a manual override (e.g., was inducted before the bot was active).

**Options:**
- `member:@User` — The brother whose date to set.
- `date:` — Date in `YYYY-MM-DD` format. Leave blank to clear any existing override.

**Access:** Forgemaster

---

## Archive Management

### `/sanctify_battle_records`
**What it does:** Ingests new AARs from the chronicle channel and adds them to the archive. Run this after a batch of new AARs has been posted and approved.

**Options:**
- `span_days:` — (Optional) Only scan the last N days of messages.

**Access:** Forgemaster

---

### `/reconcile_records`
**What it does:** Reprocesses all archived AARs and recalculates member stats. Use this after bulk corrections or if you suspect records are out of sync.

**Options:**
- `span_days:` — (Optional) Limit to the last N days.

**Access:** Forgemaster

---

### `/audit_archive_discrepancies`
**What it does:** Rechecks AARs that were previously rejected and restores any that have since been fixed (e.g., the linked message was corrected).

**Options:**
- `span_days:` — (Optional) Limit scope.

**Access:** Forgemaster

---

### `/reparse_records`
**What it does:** Re-reads stored AAR records from their original Discord message URLs and updates the archive entries. Use this if record data has drifted from source messages.

**Options:**
- `limit:` — (Optional) Maximum number of records to reparse.

**Access:** Configured admin user only

---

### `/requeue_award`
**What it does:** Manually enqueues a missed award announcement for a member. Use this when an automated announcement failed to post.

**Options:**
- `member:@User` — The member to re-announce for.

**Access:** Forgemaster

---

## Forge & Armor Subsystem

### `/forge_rite`
**What it does:** Generates and posts a formatted cogitator attestation block for a member. Used for formal induction or promotion ceremonies.

**Options:**
- `member:@User` — The brother to generate the attestation for.

**Access:** Techmarine / Forgemaster

---

### `/set_rite`
**What it does:** Sets your personal consecration rite text, which gets embedded into attestations you generate.

**Options:**
- `rite_text:` — Your rite text (multiline supported).

**Access:** Techmarine / Forgemaster

---

### `/armor_status`
**What it does:** Shows armor integrity status for any brother — damage tier, scan result, and active alerts.

**Options:**
- `brother:@User` — (Optional) Check a specific brother.

**Access:** Watch Techmarine / Forgemaster

---

### `/forge_chronicle`
**What it does:** Posts or updates the Forge Chronicle dashboard — a summary of atmospheric forge statistics and armor health across the Fortress.

**Access:** Techmarine / Forgemaster

---

### `/requisition_supplies`
**What it does:** Spends from the community armory reserve to fund blessing charges or intensive armor scans.

**Access:** Techmarine / Forgemaster

---

### `/preview_armor_alert`
**What it does:** Previews what an automated armor damage alert would look like for a specific brother, without sending the real alert.

**Access:** Techmarine / Forgemaster

---

### `/test_armor_alert`
**What it does:** Force-sends a real armor alert to the arming chamber channel. For testing alert routing only.

**Access:** Forgemaster

---

### `/forge_override`
**What it does:** Enables or disables the Techmarine / armor subsystem entirely.

**Access:** Forgemaster

---

## Librarian Subsystem (Warp Exposure)

### `/warp_status`
**What it does:** Shows brothers currently at risk from warp exposure. Librarians see their own company plus any overflow; Void Wardens see the entire Fortress.

**Access:** Librarian (own company) / Void Warden (fortress-wide)

---

### `/warp_cleanse`
**What it does:** Performs a Warp Cleansing rite on a brother to reduce their corruption level.

**Options:**
- `member:@User` — The brother to cleanse.
- `intensive:` — (Optional) Pay extra charges for a guaranteed full purge with no dice roll.
- `force:` — (Void Warden only) Bypass recipient cooldowns.

**Access:** Librarian / Void Warden

---

### `/warp_scry`
**What it does:** Traces a brother's full warp contagion subtree — deeper than `/warp_status`. Use to investigate an active corruption chain.

**Access:** Librarian / Void Warden

---

### `/librarium_chronicle`
**What it does:** Posts a sanitized Librarium status snapshot to the designated channel. For public reporting on warp threat levels.

**Access:** Void Warden / Forgemaster

---

### `/librarium_override`
**What it does:** Enables or disables the Librarian subsystem entirely.

**Access:** Forgemaster

---

## Auto-Ingest (AAR Automation)

### `/auto_ingest_status`
**What it does:** Shows the current state of the auto-AAR-ingest system and the current pressure level (how aggressively it's scanning).

**Access:** Watch Techmarine / Watch Librarian

---

### `/auto_ingest_set`
**What it does:** Enables or disables the auto-ingest system.

**Access:** Forgemaster

---

### `/auto_ingest_force`
**What it does:** Forces an immediate auto-ingest tick, bypassing the normal cooldown. Use when you need the archive updated immediately.

**Access:** Forgemaster

---

## Looking For Group

### `/lfg_queue`
**What it does:** Creates a Looking For Group queue for an operation, siege, or omega mission. Posts a joinable embed in the LFG channel and pings the relevant role.

**Options:**
- `queue_type:` — operation, siege, or omega.
- Other options (expiry, notes, etc.) will appear as prompts.

**Access:** Watch Brother+

---

### `/lfg_join`
**What it does:** Join an existing LFG queue.

**Access:** Watch Brother+

---

### `/lfg_leave`
**What it does:** Leave an LFG queue you're currently signed up for.

**Access:** Watch Brother+

---

### `/lfg_close`
**What it does:** Close and delete an LFG queue you created.

**Access:** Watch Brother+

---

## Terminus Slayer

### `/submit_kill_log`
**What it does:** Submit a kill log entry for the Terminus Slayer challenge. Brothers submit their own.

**Access:** Watch Brother+

---

### `/verifier_standing`
**What it does:** Shows the rolling 7-day verifier leaderboard — who has been most active processing kill log submissions.

**Access:** Watch Veteran+

---

## Diagnostics

### `/cache_stats`
**What it does:** Shows the internal DataStore cache sizes, dirty flags, and last flush timestamps. Use this to troubleshoot slow ingest or data sync issues.

**Access:** Watch Techmarine / Watch Master

---

### `/record_of_blood`
**What it does:** Scans the record-of-blood channel and cross-references home chapter declarations for all Watch Brothers. Produces a full report of matches and any unrecognized chapter mentions.

**Access:** Forgemaster / Watch Master

---

### `/preview_stud_announcement`
**What it does:** Previews a service stud announcement for a member without actually posting it.

**Access:** Forgemaster

---

### `/litany_of_function`
**What it does:** Posts a compact summary of available commands.

**Access:** Watch Command

---

## Notes

- **Ephemeral responses:** Most admin commands respond only to you. Output is not visible to the rest of the server unless the command is designed to post publicly (e.g., `/forge_chronicle`, `/librarium_chronicle`).
- **Channel restrictions:** Several commands are limited to specific channels (e.g., data-vault, kill log channel, arming chamber). The bot will tell you if you're in the wrong place.
- **Permission config:** Role access is configured in `config/config.json` under `permissions`. If you need to adjust who can run a specific command, that's where to look.
- **Lock contention:** A small number of commands (reconcile, sanctify) cannot run in parallel — the bot will tell you if another operation is already in progress.
