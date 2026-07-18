# OP-Scribe Servitor - Enlisted Guide

-# **Audience:** Watch Inductee, Watch Brother, Watch Veteran, Bladeguard, Oathsworn
-# **Command usage:** open Discord chat and type `/` to browse slash commands

---

## ᛭⋅ ʀᴇᴀᴅ ᴛʜɪs ғɪʀsᴛ ⋅᛭

-# Most bot responses are private by default.
-# Some commands are channel-restricted.
-# `Access denied` means rank, role, or channel mismatch.
-# Watch Sergeant+ should use GUIDE_WATCH_COMMAND.md.

```text
FAST CHECK
1) Use slash commands only.
2) Confirm you are in the right channel.
3) Confirm you have the required role/rank.
```

---

## ᛭⋅ ᴅᴇᴇᴅs ᴀɴᴅ ᴘʀᴏғɪʟᴇ ⋅᛭

### /tally_deeds
-# Shows your Deeds Ledger.
-# Use with no options.

### /submit_portrait
-# Upload a profile image for your Deeds Ledger card.

```text
OPTIONS
image (required): image upload
```

-# Cooldown: once every 28 days.
-# Portraits are staff-reviewed before use.

---

## ᛭⋅ sᴛʀɪᴋᴇ ᴅɪʀᴇᴄᴛɪᴠᴇ ǫᴜᴇᴜᴇ ⋅᛭

### /queue_strike
-# Join the strike queue.

```text
OPTIONS
minutes (optional): queue duration
mode_preference (optional): any | hard | omega
```

### /leave_strike_queue
-# Leave the strike queue.

### /strike_queue_status
-# View queue position and rough wait estimate.

### /log_strike_report
-# Submit completed strike report through an AAR link.

```text
OPTIONS
aar_link (required): link to AAR post
```

---

## ᛭⋅ ᴄʜᴀʟʟᴇɴɢᴇs ᴀɴᴅ ᴘʀᴏɢʀᴇss ⋅᛭

### /submit_kill_log
-# Submit a Terminus Slayer kill log.

```text
OPTIONS
class (required): class role
terminus (required): defeated target
aar_link (required): mission AAR
video_url (optional): clip URL
video (optional): clip upload
```

-# Recording is mandatory (URL or upload).
-# Kill-log channel only.

### /challenge-progress
-# Displays your current challenge progress.
-# Use with no options.

---

## ᛭⋅ ʟᴏᴏᴋɪɴɢ ғᴏʀ ɢʀᴏᴜᴘ ⋅᛭

### /lfg_queue
-# Create an LFG queue for teammates.

```text
OPTIONS
queue_type (required): operation | siege | omega
initiation_trial (optional): mark as trial
expire_minutes (optional): expiry timer
message (optional): extra note
```

-# Requires either PC or Console player role.
-# Omega may limit console slots.

### /lfg_join
-# Join an existing LFG queue.

```text
OPTIONS
queue (required): select queue
```

### /lfg_leave
-# Leave an LFG queue.

```text
OPTIONS
queue (required): select queue
```

### /lfg_close
-# Close an LFG queue you created.

```text
OPTIONS
queue (required): select queue
```

---

## ᛭⋅ ᴄʜᴀᴘᴛᴇʀ ʀᴇǫᴜᴇsᴛs ⋅᛭

### /chapter_request
-# Request transfer to a standard chapter.

```text
OPTIONS
chapter_name (required): requested chapter
```

-# Cooldown: once every 28 days.
-# Apothecary review required.

### /request_homebrew_chapter
-# Request creation of a homebrew chapter.

```text
OPTIONS
name (required): chapter name
geneseed_lineage (required): chapter lineage
lore_blurb (required): short lore summary
```

-# Cooldown: once every 28 days.
-# Staff and command review required.

---

## ᛭⋅ ǫᴜɪᴄᴋ ᴛʀᴏᴜʙʟᴇsʜᴏᴏᴛɪɴɢ ⋅᛭

-# `Access denied` -> wrong rank/role/channel.
-# `This command cannot be used in this channel` -> move to an allowed channel.
-# Command missing from `/` menu -> wait briefly, then retry.
