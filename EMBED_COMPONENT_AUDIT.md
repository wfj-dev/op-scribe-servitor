# Roster Containers vs Classic Embeds

Date: 2026-07-16

This memo is intentionally narrow: it explains the difference between the roster container approach and classic embed usage in this repo.

## Source docs

- https://discordjs.guide/legacy/popular-topics/display-components
- https://discordjs.guide/legacy/popular-topics/embeds

## The short answer

- Roster containers in this repo are display components v2 messages.
- Most other messages in this repo are classic embeds.
- They are different message systems with different limits and edit semantics.

## What roster containers are here

The roster container poster has been retired; no current roster publisher uses this pattern.

Legacy pattern used:

- LayoutView
- Container
- TextDisplay
- MediaGallery (optional image)

Intent:

- Present high-density roster text as a single structured block instead of many embed fields.

## What classic embeds are here

Major embed-first modules:

- opscribe/terminus_ops.py
- opscribe/forge_ops.py
- opscribe/roster_ops.py
- opscribe/aar_ops.py
- opscribe/auto_ingest.py

Intent:

- Report cards, alerts, announcements, and workflow state snapshots using title/description/fields/footer/image.

## Docs-grounded differences that matter

From display components doc:

1. Components v2 requires opting into that message model.
2. In a components-v2 payload, you cannot send content, embeds, poll, or stickers.
3. After moving a message to components v2, edit semantics are one-way for that message shape.
4. Limits differ: up to 40 components total, and text display content has a shared 4000-character cap.

From embeds doc:

1. Embeds are the standard rich card model.
2. Embed limits are field/card specific (for example 25 fields, 4096 description, 6000 combined embed chars per message).
3. Embeds are ideal for structured card-style status/report output.

## Decision rule for this repo

Use roster container (components v2) when all are true:

1. The message is roster-heavy, text-dense, and reads better as a unified text block.
2. You do not need embed cards in the same message payload.
3. You accept components-v2 constraints and one-way edit shape behavior.

Use classic embeds when any are true:

1. The message is a report/alert/announcement card.
2. You need embed field structure and conventional bot card UX.
3. You do not need components-v2 layout behavior.

## Recommendation

1. Keep non-roster systems embed-first unless a separate design review approves a migration.

## Practical note for maintainers

These transition notes are historical; there is no active roster container upsert path.
