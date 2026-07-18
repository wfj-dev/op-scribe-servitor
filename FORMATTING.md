# Message Formatting Standard

Date: 2026-07-16
Status: Draft v1
Scope: Bot-authored Discord messages (embed text, container text, and structured report output)

## Goal

Define one consistent writing and presentation style for:

- small-caps style labels and headers
- -# subtext lines
- code block output

This standard is visual and editorial. It does not attempt to change Discord fonts (Discord does not support custom fonts in embeds).

## Core style primitives

## 1) Small-caps style labels

Use stylized small-caps text for:

- metadata labels
- section titles
- short banner lines
- ceremonial signature lines

Do not use stylized small-caps for long paragraphs.

Recommended pattern:

- bold + inline-code label form for metadata and section headers
- short and explicit wording

Examples:

- **`ʀᴇᴄɪᴘɪᴇɴᴛ:`** @everyone
- **`sᴜʙᴊᴇᴄᴛ:`** ɢᴜɪᴅᴇ ᴜᴘᴅᴀᴛᴇ
- **`ᴄʜᴀʟʟᴇɴɢᴇs`**

## 2) -# subtext lines

Use -# lines for:

- contextual notes
- brief narrative explanation under section headers
- soft guidance and caveats

Guidelines:

- keep each -# block concise
- prefer 1-3 lines per section
- avoid burying critical instructions only inside -# lines

## 3) Code blocks

Use code blocks only for structured technical output:

- diagnostics
- tabular summaries
- machine-spirit style logs
- command or parser outputs

Do not place long prose announcements in code blocks.

## Separator convention

For high-ceremony announcements and major updates, separators may be used:

- top and bottom separator lines framing the message
- optional separator between major sections

Example separator:

- ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯

## Message templates

## A) Announcement template

Use for public updates, policy changes, and guide drops.

Pattern:

1. Separator
2. metadata lines (small-caps labels)
3. separator
4. -# opener/context
5. repeated section blocks:
   - small-caps section header
   - -# supporting narrative
6. separator
7. -# thank-you or service line
8. separator
9. ceremonial signature line

## B) Report-card embed template

Use for status reports, progression cards, and operation state cards.

Pattern:

1. concise title (may include small-caps words)
2. short readable description in normal text
3. field labels consistent and concise
4. optional -# context lines where useful
5. concise footer

## C) Technical output template

Use for parser/audit/diagnostic outputs.

Pattern:

1. short intro line in normal text
2. code block body for structured output
3. optional -# interpretation note

## Accessibility and readability rules

1. Keep body prose in normal readable text.
2. Restrict stylized small-caps to labels, headers, and short lines.
3. Avoid all-caps walls and overdecorated symbols in dense content.
4. Preserve mention clarity and role ping intent.
5. Verify mobile readability for all long messages.

## Discord platform constraints

1. No custom font support in embeds or message content.
2. Small-caps style is Unicode substitution, not a true font.
3. Unicode style can affect copy/paste and search behavior.
4. For components-v2 messages, use that model intentionally and respect its payload rules.

## Migration plan: apply to all embed-producing subsystems

## Phase 1: Styling helpers and policy

1. Create a shared formatting helper module for:
   - small-caps conversion helper
   - metadata label formatter
   - section header formatter
   - optional separator constant
   - guarded -# formatter for multiline sections
2. Keep helper output deterministic and testable.
3. Add unit tests for helper behavior and edge cases.

## Phase 2: High-volume embed modules

Migrate these first:

1. opscribe/target_packages_ops.py
2. opscribe/forge_ops.py
3. opscribe/roster_ops.py
4. opscribe/terminus_ops.py

Apply:

- consistent metadata label style
- consistent section-title style
- controlled use of -# subtext in narrative fields/descriptions
- no unnecessary code blocks in announcement prose

## Phase 3: Report and utility modules

Migrate next:

1. opscribe/aar_ops.py
2. opscribe/auto_ingest.py
3. opscribe/roster_embeds.py (container text sections where applicable)

Apply:

- improved readability for diagnostics
- code block usage only for true structured output
- selective -# context lines where they improve clarity

## Phase 4: Validation and documentation

1. Update tests that assert literal embed strings.
2. Add style snapshot tests for key messages.
3. Update user-facing docs where format examples exist.
4. Add this file to contributor references.

## Rollout guardrails

1. Do not perform a single giant migration PR.
2. Migrate per subsystem and verify readability in Discord clients.
3. Preserve existing business logic and permissions behavior.
4. Prefer visual consistency without sacrificing clarity.

## Discussion checklist for full transfer to all embeds

Before broad migration, decide:

1. Which commands are ceremonial vs technical vs operational.
2. Required strictness for -# usage (mandatory or optional by message type).
3. Maximum stylistic density allowed per message category.
4. Whether legacy message snapshots should be updated in one wave or incrementally.
5. Who signs off on visual style changes per subsystem.

## Suggested next action

Start with a pilot migration in one subsystem (recommended: opscribe/forge_ops.py), review output in Discord, then apply the same pattern to the remaining modules in phases.
