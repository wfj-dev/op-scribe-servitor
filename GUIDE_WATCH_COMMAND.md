# OP-Scribe Servitor - Watch Command Guide

-# Audience: Watch Sergeant+ (specialists, High Command, Forgemaster, configured admin)
-# Below Watch Sergeant: use GUIDE_WATCH_BROTHER.md.
-# Shared WB+ command details are in GUIDE_WATCH_BROTHER.md.

**`᛭⋅ Roster and Records ⋅᛭`**
-# `/tally_deeds [brother:@User] [killteam:@Role]` (WC) - Ledger lookup for member or kill team.
-# `/promotion_queue` (WC) - Members near or at promotion thresholds.
-# `/audit_service_studs` (WC) - Stud display mismatches vs earned values.
-# `/pick_home_chapters member:@User` (WC) - Roll home chapter assignment.
-# `/set_induction member:@User [date:YYYY-MM-DD]` (FM) - Set/clear induction date override.
-# `/set_loa member:@User start_date:<YYYY-MM-DD> end_date:<YYYY-MM-DD>` (WA/FM) - Set Leave of Absence window.

**`᛭⋅ Transfer Review ⋅᛭`**
`/initiate_transfer member:@User company:<company> kt:<kill team>` - Submit a reassignment petition for an active Brother.
-# Both company and Kill Team are required. Only Veteran Sergeant, Watch Lieutenant, Watch Captain, and Watch Master may process transfers.
-# Both transfer commands post the same persistent Approve/Deny embed. Only approval changes the named Brother's roles; Deny requires a reason.
-# A matching open petition is reused. Deny a conflicting petition before proposing another destination. Rulings resolve the original embed and remove its buttons.
-# Approved transfers post a welcome in the destination KT, tagging the Kill Team and Brother. Delivery errors are DM'd to the configured welcome-error contact; the transfer remains approved.
-# Reserves/LOA returns use the existing processes. The bot requires Manage Roles and sufficient role hierarchy.

**Governance Votes**
`/generate_poll title:<title> target_role:@Role subject_member:@User [equerry_appointment:true]`
- Destination role and subject member are mandatory. The subject is recused from voting and all applicable turnout counts.
- For an Equerry appointment, select the destination's ordinary specialist role, specify the subject and set `equerry_appointment:true`. The bot records and displays the office confirmation; it does not assign an office or role when the poll closes.
- Turnout requires 60% of eligible destination members. Captain, cadre leader, Equerry and Watch Master appointments also require 60% of current High Command independently. An empty required electorate blocks creation.
- Passing requires 80% weighted yes. The existing five-percentage-point close-margin revote rule still applies.
- Base weights: Sergeant/specialist 1.00; Veteran Sergeant 1.15; Lieutenant/Equerry 1.30; Captain/leader 1.50; Watch Master 1.75. High Command adds 0.25, then destination membership multiplies the ballot by 2. Highest tier only; each person casts one ballot.
- Current Equerrys must be recorded in `governance_poll.equerry_assignments` as member ID to destination key, e.g. `{"123456789": "armory"}`. No Equerry Discord role is required or inferred. Update the registry after an appointment is actually enacted, not merely proposed.
- Destination keys: `battle_line`, `armory`, `librarius`, `reclusiam`, `apothecarion`, `blades`, `black_vault`, `dreadnought`. Battle-line is fortress-wide Sergeant through Watch Master; Bladeguard cannot vote; dreadnoughts have their own voting group. First Blade uses the specialist tier; Honored Dreadnought uses the Lieutenant tier; Venerable uses the leader tier.
- Eligibility, weights and required groups are frozen when the poll opens. Candidate, bots, Reserves and Interred Brother are excluded. Existing polls keep their original unweighted calculation.

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
-# `/preview_stud_announcement` (FM) - Preview stud announcement render.
-# `/litany_of_function` (WC) - Compact command summary post.

**`᛭⋅ Notes ⋅᛭`**
-# Most admin replies are ephemeral unless posted publicly.
-# Channel restrictions apply.
-# `Access denied` = permission/channel mismatch.
-# `reconcile` and `sanctify` are lock-protected.
