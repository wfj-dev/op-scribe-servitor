# OP-Scribe Servitor - Watch Command Guide

-# **Audience:** Watch Sergeant+ (including specialists, High Command, Forgemaster, configured admin)
-# If you are below Watch Sergeant, use GUIDE_WATCH_BROTHER.md.
-# If the bot returns `Access denied`, rank/role/channel permissions did not pass.

---

## ᛭⋅ ʀᴏsᴛᴇʀ ᴀɴᴅ ʀᴇᴄᴏʀᴅs ⋅᛭

### /tally_deeds
-# Shows an individual Deeds Ledger or a Kill Team summary.

```text
OPTIONS
brother:@User (optional)
killteam:@Role (optional)
ACCESS
Watch Command
```

### /company_roster
-# Displays all Kill Teams and member totals across the Fortress.

```text
ACCESS
Watch Command
```

### /promotion_queue
-# Lists members approaching or meeting stud and veteran milestones.

```text
ACCESS
Watch Command
```

### /audit_service_studs
-# Audits displayed studs against earned studs.

```text
ACCESS
Watch Command
```

### /pick_home_chapters
-# Randomly assigns home chapter(s) from the active rotation pool.

```text
OPTIONS
member:@User (required)
ACCESS
Watch Command
```

### /set_induction
-# Sets or clears a manual induction date override.

```text
OPTIONS
member:@User (required)
date: YYYY-MM-DD (optional; blank clears override)
ACCESS
Forgemaster
```

---

## ᛭⋅ ᴀʀᴄʜɪᴠᴇ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ ⋅᛭

### /sanctify_battle_records
-# Ingests new AARs from chronicle channels into the archive.

```text
OPTIONS
span_days (optional)
ACCESS
Forgemaster
```

### /reconcile_records
-# Reprocesses archived AARs and recalculates stats.

```text
OPTIONS
span_days (optional)
ACCESS
Forgemaster
```

### /audit_archive_discrepancies
-# Rechecks previously rejected AARs and restores corrected entries.

```text
OPTIONS
span_days (optional)
ACCESS
Forgemaster
```

### /reparse_records
-# Reparses stored AAR records from original Discord URLs.

```text
OPTIONS
limit (optional)
ACCESS
Configured admin user only
```

### /requeue_award
-# Re-enqueues a missed award announcement.

```text
OPTIONS
member:@User (required)
ACCESS
Forgemaster
```

---

## ᛭⋅ ғᴏʀɢᴇ ᴀɴᴅ ᴀʀᴍᴏʀ sᴜʙsʏsᴛᴇᴍ ⋅᛭

### /forge_rite
-# Generates a formal attestation block for induction/promotion ceremonies.

```text
OPTIONS
member:@User (required)
ACCESS
Techmarine / Forgemaster
```

### /set_rite
-# Stores your personal consecration rite text.

```text
OPTIONS
rite_text (required)
ACCESS
Techmarine / Forgemaster
```

### /armor_status
-# Shows armor integrity tier, scan state, and active alerts.

```text
OPTIONS
brother:@User (optional)
ACCESS
Watch Techmarine / Forgemaster
```

### /forge_chronicle
-# Posts or refreshes the Forge Chronicle dashboard.

```text
ACCESS
Techmarine / Forgemaster
```

### /requisition_supplies
-# Spends armory reserve on blessing charges or intensive scans.

```text
ACCESS
Techmarine / Forgemaster
```

### /preview_armor_alert
-# Preview-only armor alert render (no live alert post).

```text
ACCESS
Techmarine / Forgemaster
```

### /test_armor_alert
-# Sends a live armor alert for routing tests.

```text
ACCESS
Forgemaster
```

### /forge_override
-# Enables/disables the armor subsystem.

```text
ACCESS
Forgemaster
```

---

## ᛭⋅ ʟɪʙʀᴀʀɪᴀɴ sᴜʙsʏsᴛᴇᴍ ⋅᛭

### /warp_status
-# Lists members at warp-exposure risk.
-# Librarian scope: own company (+ overflow); Void Warden: fortress-wide.

```text
ACCESS
Librarian / Void Warden
```

### /warp_cleanse
-# Performs a warp cleansing rite.

```text
OPTIONS
member:@User (required)
intensive (optional)
force (optional; Void Warden only)
ACCESS
Librarian / Void Warden
```

### /warp_scry
-# Traces a full warp contagion subtree.

```text
ACCESS
Librarian / Void Warden
```

### /librarium_chronicle
-# Posts a sanitized Librarium status snapshot.

```text
ACCESS
Void Warden / Forgemaster
```

### /librarium_override
-# Enables/disables the Librarian subsystem.

```text
ACCESS
Forgemaster
```

---

## ᛭⋅ ᴀᴜᴛᴏ-ɪɴɢᴇsᴛ (ᴀᴀʀ ᴀᴜᴛᴏᴍᴀᴛɪᴏɴ) ⋅᛭

### /auto_ingest_status
-# Shows ingest state and current processing pressure.

```text
ACCESS
Watch Techmarine / Watch Librarian
```

### /auto_ingest_set
-# Enables/disables auto-ingest.

```text
ACCESS
Forgemaster
```

### /auto_ingest_force
-# Forces an immediate ingest tick.

```text
ACCESS
Forgemaster
```

---

## ᛭⋅ ʟᴏᴏᴋɪɴɢ ғᴏʀ ɢʀᴏᴜᴘ ⋅᛭

### /lfg_queue
-# Creates a joinable operation/siege/omega queue.

```text
OPTIONS
queue_type (required): operation | siege | omega
ACCESS
Watch Brother+
```

### /lfg_join
-# Join an existing queue.

```text
ACCESS
Watch Brother+
```

### /lfg_leave
-# Leave your current queue.

```text
ACCESS
Watch Brother+
```

### /lfg_close
-# Close a queue you created.

```text
ACCESS
Watch Brother+
```

---

## ᛭⋅ ᴛᴇʀᴍɪɴᴜs sʟᴀʏᴇʀ ⋅᛭

### /submit_kill_log
-# Submit a Terminus Slayer kill log entry.

```text
ACCESS
Watch Brother+
```

### /verifier_standing
-# Shows 7-day verifier activity leaderboard.

```text
ACCESS
Watch Veteran+
```

---

## ᛭⋅ ᴅɪᴀɢɴᴏsᴛɪᴄs ⋅᛭

### /cache_stats
-# Displays DataStore cache size, dirty flags, and flush timestamps.

```text
ACCESS
Watch Techmarine / Watch Master
```

### /record_of_blood
-# Cross-references chapter declarations from record-of-blood posts.

```text
ACCESS
Forgemaster / Watch Master
```

### /preview_stud_announcement
-# Preview-only render of a service stud announcement.

```text
ACCESS
Forgemaster
```

### `/litany_of_function`
**What it does:** Posts a compact summary of available commands.

**Access:** Watch Command

---

## Notes

- **Ephemeral responses:** Most admin commands respond only to you. Output is not visible to the rest of the server unless the command is designed to post publicly (e.g., `/forge_chronicle`, `/librarium_chronicle`).
- **Channel restrictions:** Several commands are limited to specific channels (e.g., data-vault, kill log channel, arming chamber). The bot will tell you if you're in the wrong place.
- **Permission config:** Role access is configured in `config/config.json` under `permissions`. If you need to adjust who can run a specific command, that's where to look.
- **Lock contention:** A small number of commands (reconcile, sanctify) cannot run in parallel — the bot will tell you if another operation is already in progress.
