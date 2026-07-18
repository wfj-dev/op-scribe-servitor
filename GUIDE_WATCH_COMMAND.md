# OP-Scribe Servitor - Watch Command Guide

-# Audience: Watch Sergeant+ (specialists, High Command, Forgemaster, configured admin)
-# Below Watch Sergeant: use GUIDE_WATCH_BROTHER.md.

Tags: WC, FM, TM, WT, WL, VW, WM, WB+, WV+.

## ᛭⋅ Roster and Records ⋅᛭
- `/tally_deeds [brother:@User] [killteam:@Role]` (WC)
- `/company_roster` (WC)
- `/promotion_queue` (WC)
- `/audit_service_studs` (WC)
- `/pick_home_chapters member:@User` (WC)
- `/set_induction member:@User [date:YYYY-MM-DD]` (FM, blank clears)

## ᛭⋅ Archive Management ⋅᛭
- `/sanctify_battle_records [span_days]` (FM)
- `/reconcile_records [span_days]` (FM)
- `/audit_archive_discrepancies [span_days]` (FM)
- `/reparse_records [limit]` (configured admin)
- `/requeue_award member:@User` (FM)

## ᛭⋅ Forge and Armor ⋅᛭
- `/forge_rite member:@User` (TM/FM)
- `/set_rite rite_text:<text>` (TM/FM)
- `/armor_status [brother:@User]` (WT/FM)
- `/forge_chronicle` (TM/FM)
- `/requisition_supplies` (TM/FM)
- `/preview_armor_alert` (TM/FM)
- `/test_armor_alert` (FM)
- `/forge_override` (FM)

## ᛭⋅ Librarian Subsystem ⋅᛭
- `/warp_status` (Librarian/VW, scope: own company + overflow)
- `/warp_cleanse member:@User [intensive] [force]` (Librarian/VW, force VW-only)
- `/warp_scry` (Librarian/VW)
- `/librarium_chronicle` (VW/FM)
- `/librarium_override` (FM)

## ᛭⋅ Auto-Ingest ⋅᛭
- `/auto_ingest_status` (WT/WL)
- `/auto_ingest_set` (FM)
- `/auto_ingest_force` (FM)

## ᛭⋅ LFG and Challenges ⋅᛭
- `/lfg_queue queue_type:<operation|siege|omega>` (WB+)
- `/lfg_join` (WB+)
- `/lfg_leave` (WB+)
- `/lfg_close` (WB+)
- `/submit_kill_log` (WB+)
- `/verifier_standing` (WV+)

## ᛭⋅ Diagnostics ⋅᛭
- `/cache_stats` (WT/WM)
- `/record_of_blood` (FM/WM)
- `/preview_stud_announcement` (FM)
- `/litany_of_function` (WC)

## ᛭⋅ Notes ⋅᛭
- Most admin replies are ephemeral unless command posts publicly.
- Some commands are channel-restricted.
- `Access denied` = permission/channel mismatch.
- `reconcile` and `sanctify` are lock-protected.
