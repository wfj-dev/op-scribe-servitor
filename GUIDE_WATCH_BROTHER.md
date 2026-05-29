# OP-Scribe Servitor — Watch Brother+ Guide

This guide covers every command available to regular Watch Brothers. All commands are typed in Discord using the `/` prefix. Most commands only work in designated channels — the bot will tell you if you're in the wrong place.

---

## Your Records

### `/my_deeds`
**What it does:** Shows your personal Deeds Ledger — your lifetime AAR points, gene-seed stewardship score, and armory recovery score, along with whether you're currently active.

**How to use:** Just type `/my_deeds` — no extra options needed. Use it in your Kill Team's channel.

---

## Challenges & Kill Logs

### `/submit_kill_log`
**What it does:** Submit a Terminus Slayer kill log for the Terminus Slayer challenge. Requires a video or clip of the kill and a link to the AAR from that mission.

**Options:**
- `class:` — Your class for the kill (must match your challenge class).
- `terminus:` — Which Terminus enemy you killed (select from the dropdown).
- `aar_link:` — Paste the Discord link to your AAR post.
- `video_url:` — Link to your kill clip (YouTube, Medal, Streamable, etc.).
- `video:` — Alternatively, attach a video file directly.

**Example:**
- `/submit_kill_log class:@Primaris Intercessor terminus:Carnifex aar_link:https://discord.com/... video_url:https://medal.tv/...`

---

### `/challenge-progress`
**What it does:** Shows your current challenge progress — mission awards you've earned and your Terminus Slayer kill count.

**How to use:** Just type `/challenge-progress`.

---

## Looking For Group

### `/lfg_queue`
**What it does:** Create a Looking For Group post to find brothers for an operation or omega mission.

**Options:**
- `mission_type:` — The type of mission (operations, omega, etc.).
- Other options will appear — fill in what's relevant.

---

### `/lfg_join`
**What it does:** Join an existing LFG queue that another brother created.

---

### `/lfg_leave`
**What it does:** Leave an LFG queue you're currently signed up for.

---

### `/lfg_close`
**What it does:** Close and delete an LFG queue that you created.

---

## Standings & Leaderboards

### `/verifier_standing`
**What it does:** Shows the rolling 7-day verifier activity leaderboard — who has been most active in verifying kill logs.

> **Who can use this:** Watch Veteran and above.

---

## Tips

- Commands only work in allowed channels. If the bot says "wrong channel," check the pinned channel list.
- All responses are **ephemeral** (only visible to you) unless the command is designed to post publicly.
- If a command doesn't respond, you may not have the required rank for it yet.
