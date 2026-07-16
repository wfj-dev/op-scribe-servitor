# OP-Scribe Servitor - Enlisted Guide

This guide is for these ranks:

- Watch Inductee
- Watch Brother
- Watch Veteran
- Bladeguard
- Oathsworn

## Read This First

- Type commands with `/` in Discord.
- Most bot replies are private (only you can see them).
- Some commands only work in certain channels.
- If you see `Access denied`, you are in the wrong channel or do not have permission.
- If you are Watch Sergeant or higher, use `GUIDE_WATCH_COMMAND.md`.

## Deeds and Profile

### `/tally_deeds`
Shows your Deeds Ledger.

Use it with no options.

### `/submit_portrait`
Send a picture for your Deeds Ledger profile.

Options:
- `image` (required): upload an image file.

Notes:
- You can submit once every 28 days.
- Staff review it before it is used.

## Strike Directive Queue (Ordo Xenos)

### `/queue_strike`
Join the strike queue.

Options:
- `minutes` (optional): how long to stay in queue.
- `mode_preference` (optional): queue for `any`, `hard`, or `omega`.

### `/leave_strike_queue`
Leave the strike queue.

### `/strike_queue_status`
See where you are in line and roughly how long you may wait.

### `/log_strike_report`
Submit your finished strike using the AAR link.

Options:
- `aar_link` (required): link to your AAR post.

## Challenges and Progress

### `/submit_kill_log`
Send in a Terminus Slayer kill log.

Options:
- `class` (required): your class role.
- `terminus` (required): which Terminus enemy you killed.
- `aar_link` (required): link to the mission AAR.
- `video_url` (optional): link to your clip.
- `video` (optional): upload your clip file.

Notes:
- You must include a recording (link or uploaded video).
- This only works in the kill-log channel.

### `/challenge-progress`
Shows your challenge progress.

Use it with no options to check your own progress.

## Looking For Group (LFG)

### `/lfg_queue`
Create an LFG post to find players.

Options:
- `queue_type` (required): `operation`, `siege`, or `omega`.
- `initiation_trial` (optional): mark it as an initiation trial.
- `expire_minutes` (optional): set when it expires.
- `message` (optional): extra note for players.

Notes:
- You need either the PC or Console player role.
- Omega may limit how many Console players can join.

### `/lfg_join`
Join an LFG post.

Options:
- `queue` (required): pick from the list.

### `/lfg_leave`
Leave an LFG post.

Options:
- `queue` (required): pick from the list.

### `/lfg_close`
Close your own LFG post.

Options:
- `queue` (required): pick from the list.

## Chapter Requests

### `/chapter_request`
Ask to change to a standard chapter.

Options:
- `chapter_name` (required): chapter you want.

Notes:
- You can send one request every 28 days.
- Apothecary staff review the request.

### `/request_homebrew_chapter`
Ask for a new homebrew chapter.

Options:
- `name` (required): chapter name.
- `geneseed_lineage` (required): chapter lineage.
- `lore_blurb` (required): short lore text.

Notes:
- You can send one request every 28 days.
- Staff and command review it.

## Quick Troubleshooting

- `Access denied`: wrong channel or no permission.
- `This command cannot be used in this channel`: use it in an allowed channel.
- Command not showing in `/` list: wait a moment and try again.
