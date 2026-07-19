# OP-Scribe Servitor - Techmarine Troubleshooting Guide

-# Audience: Techmarines and support staff who do not write bot code.
-# Goal: Resolve common user issues quickly and escalate only high-signal reports.
-# This guide is operational, not technical.

## FAST TRIAGE (60-120 seconds)

-# 1) Confirm command spelling and slash usage (`/` command, not plain chat).
-# 2) Confirm right channel (many commands are channel-restricted).
-# 3) Confirm rank/role requirements for the user.
-# 4) Confirm they waited 10-20 seconds and retried once.
-# 5) Check whether this is user-specific or affecting multiple Brothers.

If fixed by the above: close with guidance and no escalation needed.

## COMMON ISSUES AND FIXES

### 1) "Access denied"

Likely cause:
- Role/rank mismatch, or command limited to specific roles.

Techmarine actions:
- Ask the Brother which command they ran.
- Confirm their current roles in Discord.
- Verify they are in an allowed command channel.
- Have them retry once in the correct channel.

Escalate when:
- User clearly has required role and channel, but denial persists.

### 2) "This command cannot be used in this channel"

Likely cause:
- Command is restricted to specific channels/threads.

Techmarine actions:
- Move the Brother to the proper command channel.
- For kill-log or forum-bound commands, confirm parent forum/thread location.

Escalate when:
- Error appears in a channel known to be valid for that command.

### 3) Command missing from slash menu

Likely cause:
- Discord command cache delay or local client sync delay.

Techmarine actions:
- Ask user to wait 30-60 seconds.
- Ask user to close and reopen Discord or switch channel and back.
- Confirm they are typing `/` and the command name prefix correctly.

Escalate when:
- Multiple users report same command missing for more than 10 minutes.

### 4) Command runs but returns unexpected/empty result

Likely cause:
- Bad/missing input value, stale assumptions, or data edge case.

Techmarine actions:
- Verify exact input used (all options and values).
- Try same command with a known-good test case.
- Ask if issue is repeatable or one-off.

Escalate when:
- Reproducible with clean inputs, or affects multiple users.

### 5) Queue/record command appears "stuck" or delayed

Likely cause:
- Lock-protected or long-running maintenance behavior.

Techmarine actions:
- Confirm whether an admin maintenance command was recently run.
- Ask users to wait 2-5 minutes before retesting.

Escalate when:
- Delay persists beyond normal window and impacts active workflows.

## WHAT TO COLLECT BEFORE ESCALATION

Always gather this package before tagging the coder:

-# Reporter: Discord @mention and display name
-# Time: UTC timestamp of failure (or nearest minute)
-# Command: full slash command and all option values
-# Channel: exact channel/thread link where command was used
-# Expected result: what the Brother thought should happen
-# Actual result: exact bot message or screenshot
-# Scope: one user or multiple users
-# Repro steps: short, numbered steps that reproduce the issue
-# Retry status: whether they retried and what changed

If any of the above is missing, complete it first.

## ESCALATION THRESHOLDS

Escalate immediately when:
- Multiple Brothers report the same failure.
- Core flows are blocked (queueing, logging, roster-critical actions).
- Command output is clearly malformed or contradictory.
- Previously working command now fails without workflow changes.

Do not escalate yet when:
- It is clearly channel misuse.
- It is clearly permission misuse.
- User has not retried once after correction.
- No reproducible steps are available.

## ESCALATION MESSAGE TEMPLATE

Copy and fill this block:

```text
Techmarine Escalation - OP-Scribe

Reporter: @user (Display Name)
Time (UTC): 2026-07-19 18:42
Command: /example_command option_a:value option_b:value
Channel: #channel-name (link)

Expected:
<what should happen>

Actual:
<exact error text or screenshot link>

Scope:
<single user | multiple users>

Repro Steps:
1) ...
2) ...
3) ...

Retry Attempted:
<yes/no + result>

Triage Completed:
- Channel check: pass/fail
- Role check: pass/fail
- Input format check: pass/fail
- Second attempt: pass/fail
```

## RESPONSE MACROS FOR TECHMARINES

Use these quick responses in support channels:

Permission/channel check:
-# "Brother, this looks like a role or channel restriction. Please run the same command in the command channel and confirm your current roles; I will verify from this side."

Repro info request:
-# "Please send the exact command input, channel link, timestamp, and screenshot of the bot response so we can reproduce before escalation."

Escalation confirmation:
-# "Issue reproduced and triaged. Escalating to the codex-keeper now with full incident details."

## OPERATING PRINCIPLES

-# Be calm, fast, and specific.
-# Avoid guessing root causes in public.
-# Collect facts first, escalate second.
-# High-quality reports reduce fix time dramatically.
