# OP-Scribe Servitor - Watch Command Guide

-# Audience: Watch Sergeant+ (specialists, High Command, Forgemaster, configured admin)
-# Below Watch Sergeant: use GUIDE_WATCH_BROTHER.md.
-# Shared WB+ command details are in GUIDE_WATCH_BROTHER.md.

**`᛭⋅ Roster and Records ⋅᛭`**
-# `/tally_deeds [brother:@User] [killteam:@Role]` (WC) - Ledger lookup for member or kill team.
-# `/company_roster` (WC) - Fortress-wide company/member totals.
-# `/promotion_queue` (WC) - Members near or at promotion thresholds.
-# `/audit_service_studs` (WC) - Stud display mismatches vs earned values.
-# `/pick_home_chapters member:@User` (WC) - Roll home chapter assignment.
-# `/set_induction member:@User [date:YYYY-MM-DD]` (FM) - Set/clear induction date override.
-# `/set_loa member:@User start_date:<YYYY-MM-DD> end_date:<YYYY-MM-DD>` (WA/FM) - Set Leave of Absence window.

**`᛭⋅ Archive Management ⋅᛭`**
-# `/sanctify_battle_records [span_days]` (FM) - Ingest new AARs.
-# `/reconcile_records [span_days]` (FM) - Rebuild stats from archived AARs.
-# `/audit_archive_discrepancies [span_days]` (FM) - Recheck rejected AARs.
-# `/reparse_records [limit]` (configured admin) - Reparse stored AAR URLs.
-# `/requeue_award member:@User` (FM) - Re-enqueue missed award announcement.

**`᛭⋅ Shared WB+ Commands ⋅᛭`**
-# For LFG, strike queue, kill logs, challenge progress: see GUIDE_WATCH_BROTHER.md.
-# `/verifier_standing` (WV+) - 7-day kill-log verifier activity leaderboard.

**`᛭⋅ Diagnostics ⋅᛭`**
-# `/cache_stats` (WM) - DataStore cache/dirty/flush status.
-# `/record_of_blood` (FM/WM) - Cross-check chapter declarations from record posts.
-# `/preview_stud_announcement` (FM) - Preview stud announcement render.
-# `/litany_of_function` (WC) - Compact command summary post.

**`᛭⋅ Notes ⋅᛭`**
-# Most admin replies are ephemeral unless posted publicly.
-# Channel restrictions apply.
-# `Access denied` = permission/channel mismatch.
-# `reconcile` and `sanctify` are lock-protected.
