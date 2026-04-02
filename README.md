# OP-Scribe Servitor — User Guide

This document is a concise user-facing guide describing available slash commands, basic usage, and permission notes.

Commands
- `/litany_of_function` — Show help summary of available commands.
- `/tally_deeds brother:@User` — Show the Deeds Ledger for a single Brother (lifetime tallies and recent activity).
- `/tally_deeds killteam:@Role` — Show the roster for a Kill Team role and a short 7‑day summary (operational tempo, averages).
- `/combat_bonds [brother:@User] [window:int]` — Show top combat bonds globally or limited to a Brother; `window` is days (default 30).
- `/set_rite rite_text` — Set your personal consecration rite text (multiline allowed).
- `/forge_rite member:@User` — Generate and post an attestation block for a member (role-restricted).
- `/pick_home_chapters member:@User` — Randomly select home chapter(s) for a member from rotation pool.
- `/armor_status [brother:@User]` — Show armor integrity status for a Brother.
- `/preview_armor_alert` — Preview what an armor integrity alert would look like (admin-only).
- `/preview_stud_announcement` — Preview what a service stud announcement would look like (admin-only).
- `/record_of_blood` — Show gene-seed and armory recovery records.
- `/reconcile_records [span_days:int]` — Reprocess AARs and update the archive (admin-only).
- `/sanctify_battle_records [span_days:int]` — Ingest new sanctioned AARs (admin-only).
- `/audit_archive_discrepancies [span_days:int]` — Recheck rejected AARs and restore fixed entries (admin-only).
- `/reparse_records [limit:int]` — Re-parse stored AARs from message URLs (admin-only).
- `/cache_stats` — Show DataStore cache and flush stats (admin-only).
- `/audit_service_studs` — List brothers whose displayed service studs differ from entitlement (Watch Command only).
- `/librarian_audit` — Audit data integrity and identify inconsistencies (admin-only).
- `/roster_audit [company:str]` — Audit roster data for a watch company (admin-only).
- `/promotion_queue` — Show brothers approaching promotion milestones (Watch Command only).
- `/company_roster company:str` — Display the full roster for a watch company.

Quick notes
- Permissions: Several commands are restricted by roles and admin IDs defined in the bot configuration (`config/config.json`). If a command is denied, check role aliases and configured admin IDs.
- Channels: Some commands only operate in allowed channels (for example the bot checks `ALLOWED_COMMAND_CHANNELS` and certain forum/thread parents for Kill Team posts).
- Message limits: The bot ensures command output fits Discord limits; large outputs are truncated or paginated. The `litany_of_function` help command returns a concise summary kept under 2000 characters.

Examples
- `/tally_deeds brother:@Watch Veteran Moloch`
- `/tally_deeds killteam:@Kill Team Solomon`
- `/combat_bonds window:60`

Support
- Config and role mappings live in `config/config.json`.
- Data files are in the `data/` directory (AAR records, errors, processed IDs, rites).

If you want the README expanded with setup, contributor info, or development notes, open an issue or request the addition.

Detailed command descriptions

- `/tally_deeds brother:@User`
	- Purpose: Show an individual Brother's Deeds Ledger — a compact summary of their lifetime contribution metrics and recent activity.
	- What you see: display name, active/inactive status (active = any recent AAR within 28 days), lifetime AAR points, gene‑seed stewardship points, and armory recovery points. Lines are aligned for readability.
	- Who can run: general use, but some detailed views may be restricted by channel/role.
	- Example: `/tally_deeds brother:@Watch Veteran Moloch` — returns a short ledger for Moloch.

- `/tally_deeds killteam:@Role`
	- Purpose: Produce a roster for the specified Kill Team and a 7‑day summary of team activity.
	- What you see: ordered roster (by rank priority, service studs, AAR points, then name), activity flags, and short team metrics (avg AAR, ops count, average waves, gene/armory preservation averages).
	- Notes: Very large rosters are truncated to keep messages readable; the bot will indicate if some members are omitted.
	- Example: `/tally_deeds killteam:@Kill Team Solomon`.

- `/combat_bonds [brother:@User] [window:int]`
	- Purpose: Show which members form the strongest operational bonds together over a recent window.
	- What you see: top bond triplets or pairs and a compact per-Brother spread summary (how broadly and deeply someone partnered). When run for a `brother`, results focus on bonds involving that Brother.
	- Window: number of days to include; default is 30. Shorter windows reflect recent activity, longer windows show historical patterns.
	- Interpretation: higher spread or percentile means broader partner breadth and/or repeated pairings.
	- Example: `/combat_bonds window:60`.

- `/set_rite rite_text`
	- Purpose: Save a short personal consecration text that can later be used in attestations.
	- What you see: confirmation the rite was saved.
	- Notes: multiline text is supported; your saved rite is linked to your user ID.

- `/forge_rite member:@User`
	- Purpose: Generate a formatted attestation for the target member using stored rites and account metadata.
	- What you see: a posted attestation block suitable for archival or ceremonial display.
	- Who can run: restricted to configured roles (e.g., Techmarine, Forgemaster) and not allowed in some channels.

- `/reconcile_records [span_days:int]`, `/sanctify_battle_records [span_days:int]`, `/audit_archive_discrepancies [span_days:int]`, `/reparse_records [limit:int]`
	- Purpose: Administrative record maintenance routines. They reparse messages, ingest sanctioned AARs, recheck previously rejected entries, and repair archive inconsistencies.
	- Who can run: admin-only (Watch Master, Forgemaster, or configured admin IDs). These commands operate on the bot's archive and may produce long, ephemeral reports.

- `/cache_stats`
	- Purpose: Show sizes and state of internal DataStore caches and last flush times.
	- Who can run: admin-only. Useful for troubleshooting ingest and flush behaviour.

- `/audit_service_studs`
	- Purpose: List members whose displayed service studs do not match computed entitlement.
	- Who can run: Watch Command only. Use this to find and correct display mismatches.

- `/litany_of_function`
	- Purpose: Show a concise summary of available commands.
	- Who can run: Watch Sergeant+ by default (configurable via permission mappings).

- `/pick_home_chapters member:@User`
	- Purpose: Randomly select home chapter(s) for a member from the rotation pool.
	- Who can run: role-restricted (Forgemaster, Watch Master).

- `/armor_status [brother:@User]`
	- Purpose: Show armor integrity status for a Brother or yourself.
	- Who can run: general use.

- `/preview_armor_alert`
	- Purpose: Preview what an automated armor damage alert would look like.
	- Who can run: Techmarine or Forgemaster only.

- `/preview_stud_announcement`
	- Purpose: Preview what a service-stud announcement would look like.
	- Who can run: debug mode only, or configured admin user IDs.

- `/record_of_blood`
	- Purpose: Show gene-seed recovery and armory preservation records.
	- Who can run: role-restricted (Forgemaster, Watch Master).

- `/librarian_audit`
	- Purpose: Audit data integrity across all stored records.
	- Who can run: Watch Command only (or as configured via permissions).

- `/roster_audit [company:str]`
	- Purpose: Audit roster data for a watch company.
	- Who can run: admin-only.

- `/promotion_queue`
	- Purpose: Show brothers approaching promotion milestones.
	- Who can run: Watch Command only.

- `/company_roster company:str`
	- Purpose: Display full roster for a watch company (Primus, Secundus, Tertius, Quartus, Quintus).
	- Who can run: general use.

How to interpret outputs
- The bot favors concise, human-friendly text blocks. Numeric scores are short summaries; percentile or "spread" values compare a Brother to peers in the selected window.
- When results are long, the bot will trim and indicate omitted lines rather than exceed Discord message limits.

Where to configure
- See `config/config.json` for role aliases, admin user IDs, allowed channels, and permission mappings.

Contact / contributions
- If you'd like clearer layouts, additional fields, or different default windows, open an issue or request the change.
