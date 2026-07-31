#!/usr/bin/env python3
"""Audit Kill Team role/image mappings against live Discord roles.

Checks performed:
1. Configured Kill Team role IDs resolve to real guild roles.
2. Configured role-image mappings exist for those roles.
3. Mapped image filenames exist in assets paths.
4. Role names align with mapped filename stems.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord


VALID_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
EXTENSION_PREFERENCE = [".png", ".jpg", ".jpeg", ".webp"]


@dataclass(slots=True)
class RoleAuditResult:
	role_id: int
	role_name: str | None
	exists_in_discord: bool
	discovered_as_kill_team: bool
	in_kt_role_ids: bool
	has_mapping: bool
	mapped_filename: str | None
	mapped_file_exists: bool
	mapped_file_path: Path | None
	mapped_file_case_mismatch: bool
	name_aligned: bool | None
	expected_name_matches: list[Path]


@dataclass(slots=True)
class AuditReport:
	guild_name: str
	guild_id: int
	results: list[RoleAuditResult]
	extra_mapping_ids: list[int]
	issues: list[str]

	@property
	def has_failures(self) -> bool:
		return bool(self.issues)


def _repo_root() -> Path:
	return Path(__file__).resolve().parent.parent


def _default_config_path() -> Path:
	return _repo_root() / "config" / "config.json"


def _asset_roots() -> list[Path]:
	base = _repo_root() / "assets"
	return [base / "roster images", base]


def _normalize_for_compare(text: str) -> str:
	value = (text or "").strip().lower()
	value = re.sub(r"[_.-]+", " ", value)
	value = re.sub(r"\s+", " ", value)
	value = re.sub(r"[^a-z0-9 ]+", "", value)
	return re.sub(r"\s+", " ", value).strip()


def _is_kill_team_role_name(role_name: str) -> bool:
	lowered = (role_name or "").lower()
	return "kill" in lowered and "team" in lowered and "champion" not in lowered


def _load_config(config_path: Path) -> dict[str, Any]:
	with config_path.open("r", encoding="utf-8") as handle:
		parsed = json.load(handle)
	if not isinstance(parsed, dict):
		raise ValueError(f"Config is not a JSON object: {config_path}")
	return parsed


def _parse_kt_ids(target_packages: dict[str, Any]) -> set[int]:
	raw = target_packages.get("kt_role_ids") or []
	parsed_ids: set[int] = set()
	for entry in raw:
		try:
			parsed_ids.add(int(entry))
		except (TypeError, ValueError):
			continue
	return parsed_ids


def _parse_kt_mapping(target_packages: dict[str, Any]) -> dict[int, str]:
	raw = target_packages.get("kt_role_image_assets") or {}
	if not isinstance(raw, dict):
		return {}

	parsed: dict[int, str] = {}
	for role_id, filename in raw.items():
		try:
			numeric_role_id = int(role_id)
		except (TypeError, ValueError):
			continue
		clean_filename = str(filename or "").strip()
		if not clean_filename:
			continue
		parsed[numeric_role_id] = clean_filename
	return parsed


def _resolve_guild(
	client: discord.Client,
	config: dict[str, Any],
	guild_name_override: str | None,
	guild_id_override: int | None,
) -> discord.Guild | None:
	if guild_name_override:
		for guild in client.guilds:
			if guild.name == guild_name_override:
				return guild

	cfg_name = str(config.get("guild_name") or "").strip()
	if cfg_name:
		for guild in client.guilds:
			if guild.name == cfg_name:
				return guild

	if guild_id_override is not None:
		guild = client.get_guild(guild_id_override)
		if guild is not None:
			return guild

	cfg_id = config.get("guild_id")
	if cfg_id:
		try:
			guild = client.get_guild(int(cfg_id))
			if guild is not None:
				return guild
		except (TypeError, ValueError):
			pass

	return client.guilds[0] if client.guilds else None


def _index_assets(asset_roots: list[Path]) -> list[Path]:
	files: list[Path] = []
	for root in asset_roots:
		if not root.exists() or not root.is_dir():
			continue
		for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
			if child.is_file() and child.suffix.lower() in VALID_IMAGE_EXTENSIONS:
				files.append(child)
	return files


def _resolve_mapped_file(filename: str, asset_files: list[Path]) -> tuple[Path | None, bool]:
	# Return (path, case_mismatch) where case_mismatch means same filename
	# exists but only by case-insensitive match.
	for asset_file in asset_files:
		if asset_file.name == filename:
			return asset_file, False

	lowered = filename.lower()
	for asset_file in asset_files:
		if asset_file.name.lower() == lowered:
			return asset_file, True

	return None, False


def _expected_name_matches(role_name: str, asset_files: list[Path]) -> list[Path]:
	role_norm = _normalize_for_compare(role_name)
	matches: list[Path] = []
	for asset_file in asset_files:
		stem_norm = _normalize_for_compare(asset_file.stem)
		if stem_norm == role_norm:
			matches.append(asset_file)
	return matches


def _preferred_expected_match(matches: list[Path], asset_roots: list[Path]) -> Path | None:
	if not matches:
		return None

	root_order = {str(root): idx for idx, root in enumerate(asset_roots)}
	ext_order = {ext: idx for idx, ext in enumerate(EXTENSION_PREFERENCE)}

	def _rank(path: Path) -> tuple[int, int, str]:
		parent_rank = root_order.get(str(path.parent), len(root_order))
		ext_rank = ext_order.get(path.suffix.lower(), len(ext_order))
		return (parent_rank, ext_rank, path.name.lower())

	return sorted(matches, key=_rank)[0]


def _build_mapping_updates(
	report: AuditReport,
	asset_roots: list[Path],
) -> tuple[dict[int, str], list[str]]:
	updates: dict[int, str] = {}
	notes: list[str] = []

	for row in report.results:
		if not row.in_kt_role_ids:
			continue
		if not row.exists_in_discord:
			notes.append(f"skip {row.role_id}: role not found in Discord")
			continue
		if not row.discovered_as_kill_team:
			notes.append(f"skip {row.role_id}: role is not a Kill Team role")
			continue

		preferred = _preferred_expected_match(row.expected_name_matches, asset_roots)
		if preferred is None:
			notes.append(
				f"skip {row.role_id}: no image file matching role name '{row.role_name}' found in assets"
			)
			continue

		target_filename = preferred.name
		current_filename = row.mapped_filename
		if current_filename != target_filename:
			updates[row.role_id] = target_filename

	return updates, notes


def _write_config_atomically(config_path: Path, config: dict[str, Any]) -> Path:
	ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	backup_path = config_path.with_name(f"{config_path.name}.bak.{ts}")
	tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")

	if config_path.exists():
		shutil.copy2(config_path, backup_path)

	with tmp_path.open("w", encoding="utf-8") as handle:
		json.dump(config, handle, indent=2)
		handle.write("\n")
		handle.flush()
		os.fsync(handle.fileno())

	tmp_path.replace(config_path)
	return backup_path


def _build_report(
	guild: discord.Guild,
	kt_role_ids: set[int],
	kt_mapping: dict[int, str],
	asset_files: list[Path],
) -> AuditReport:
	discovered_kt_roles = {role.id: role for role in guild.roles if _is_kill_team_role_name(role.name)}
	mapping_ids = set(kt_mapping.keys())
	all_role_ids = sorted(kt_role_ids | mapping_ids | set(discovered_kt_roles.keys()))

	results: list[RoleAuditResult] = []
	issues: list[str] = []

	for role_id in all_role_ids:
		role = guild.get_role(role_id)
		role_name = role.name if role else None
		mapped_filename = kt_mapping.get(role_id)
		has_mapping = mapped_filename is not None
		mapped_path: Path | None = None
		mapped_case_mismatch = False

		if mapped_filename:
			mapped_path, mapped_case_mismatch = _resolve_mapped_file(mapped_filename, asset_files)

		alignment: bool | None = None
		expected_matches: list[Path] = []
		if role_name:
			expected_matches = _expected_name_matches(role_name, asset_files)
			if mapped_filename:
				alignment = _normalize_for_compare(Path(mapped_filename).stem) == _normalize_for_compare(role_name)

		result = RoleAuditResult(
			role_id=role_id,
			role_name=role_name,
			exists_in_discord=role is not None,
			discovered_as_kill_team=role_id in discovered_kt_roles,
			in_kt_role_ids=role_id in kt_role_ids,
			has_mapping=has_mapping,
			mapped_filename=mapped_filename,
			mapped_file_exists=mapped_path is not None,
			mapped_file_path=mapped_path,
			mapped_file_case_mismatch=mapped_case_mismatch,
			name_aligned=alignment,
			expected_name_matches=expected_matches,
		)
		results.append(result)

		if result.in_kt_role_ids and not result.exists_in_discord:
			issues.append(f"Configured role ID not found in guild: {result.role_id}")
		if result.exists_in_discord and result.discovered_as_kill_team and not result.has_mapping:
			issues.append(f"Kill Team role missing image mapping: {result.role_name} ({result.role_id})")
		if result.has_mapping and not result.mapped_file_exists:
			issues.append(
				f"Mapped image file not found: role_id={result.role_id} file={result.mapped_filename}"
			)
		if result.name_aligned is False:
			issues.append(
				"Mapped filename does not align with role name: "
				f"{result.role_name} ({result.role_id}) -> {result.mapped_filename}"
			)

	extra_mapping_ids = sorted(mapping_ids - kt_role_ids)
	return AuditReport(
		guild_name=guild.name,
		guild_id=guild.id,
		results=results,
		extra_mapping_ids=extra_mapping_ids,
		issues=issues,
	)


def _status_icon(value: bool | None) -> str:
	if value is None:
		return "-"
	return "Y" if value else "N"


def _format_report(report: AuditReport) -> str:
	lines: list[str] = []
	lines.append(f"Kill Team role/image audit for guild: {report.guild_name} ({report.guild_id})")
	lines.append("=" * 88)

	total = len(report.results)
	exists_count = sum(1 for r in report.results if r.exists_in_discord)
	mapped_count = sum(1 for r in report.results if r.has_mapping)
	mapped_file_count = sum(1 for r in report.results if r.mapped_file_exists)
	aligned_count = sum(1 for r in report.results if r.name_aligned is True)
	discovered_kt_count = sum(1 for r in report.results if r.discovered_as_kill_team)

	lines.append(
		"Summary: "
		f"tracked={total} | exists_in_discord={exists_count} | discovered_kill_team_roles={discovered_kt_count}"
	)
	lines.append(
		"         "
		f"has_mapping={mapped_count} | mapped_file_exists={mapped_file_count} | aligned_name={aligned_count}"
	)
	lines.append("")

	lines.append("Per-role status")
	lines.append("ID                 EX MAP IMG ALN DISC CFG  ROLE NAME -> MAPPED FILE")
	lines.append("-" * 88)
	for row in report.results:
		role_name = row.role_name or "<missing role>"
		mapped_name = row.mapped_filename or "<no mapping>"
		lines.append(
			f"{row.role_id:<18} "
			f"{_status_icon(row.exists_in_discord):<2} "
			f"{_status_icon(row.has_mapping):<3} "
			f"{_status_icon(row.mapped_file_exists):<3} "
			f"{_status_icon(row.name_aligned):<3} "
			f"{_status_icon(row.discovered_as_kill_team):<4} "
			f"{_status_icon(row.in_kt_role_ids):<3} "
			f"{role_name} -> {mapped_name}"
		)
		if row.expected_name_matches:
			candidates = ", ".join(str(path.relative_to(_repo_root())) for path in row.expected_name_matches)
			lines.append(f"  expected-name asset(s): {candidates}")
		if row.mapped_file_case_mismatch and row.mapped_file_path is not None:
			resolved = row.mapped_file_path.relative_to(_repo_root())
			lines.append(f"  note: mapped filename matched case-insensitively at {resolved}")

	lines.append("")
	if report.extra_mapping_ids:
		lines.append("Mapping keys not present in target_packages.kt_role_ids:")
		for role_id in report.extra_mapping_ids:
			lines.append(f"- {role_id}")
		lines.append("")

	if report.issues:
		lines.append("Issues detected")
		lines.append("-" * 88)
		for issue in report.issues:
			lines.append(f"- {issue}")
	else:
		lines.append("No issues detected.")

	return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Audit Discord Kill Team roles against config target_packages.kt_role_image_assets "
			"for role existence, image existence, and role-image alignment."
		)
	)
	parser.add_argument(
		"--config",
		default=str(_default_config_path()),
		help="Path to config.json (default: ./config/config.json)",
	)
	parser.add_argument(
		"--guild-name",
		default=None,
		help="Override guild name selection.",
	)
	parser.add_argument(
		"--guild-id",
		type=int,
		default=None,
		help="Override guild ID selection.",
	)
	parser.add_argument(
		"--strict",
		action="store_true",
		help="Exit with code 1 if issues are detected.",
	)
	parser.add_argument(
		"--apply",
		action="store_true",
		help="Apply safe kt_role_image_assets fixes to config.json when confident matches are available.",
	)
	return parser.parse_args()


def _run_audit(args: argparse.Namespace) -> int:
	token = os.getenv("DISCORD_TOKEN")
	if not token:
		print("ERROR: DISCORD_TOKEN environment variable is not set.")
		return 2

	config_path = Path(args.config).expanduser().resolve()
	if not config_path.exists():
		print(f"ERROR: config file not found: {config_path}")
		return 2

	try:
		config = _load_config(config_path)
	except Exception as exc:
		print(f"ERROR: failed to load config: {exc}")
		return 2

	target_packages = config.get("target_packages") or {}
	if not isinstance(target_packages, dict):
		print("ERROR: config target_packages section is missing or invalid.")
		return 2

	kt_role_ids = _parse_kt_ids(target_packages)
	kt_mapping = _parse_kt_mapping(target_packages)
	asset_files = _index_assets(_asset_roots())
	asset_roots = _asset_roots()

	intents = discord.Intents.none()
	intents.guilds = True

	class AuditClient(discord.Client):
		def __init__(self, *, intents: discord.Intents):
			super().__init__(intents=intents)
			self.exit_code = 2

		async def on_ready(self) -> None:
			try:
				guild = _resolve_guild(self, config, args.guild_name, args.guild_id)
				if guild is None:
					print("ERROR: no guild was resolved from connected guilds.")
					self.exit_code = 2
					return

				report = _build_report(guild, kt_role_ids, kt_mapping, asset_files)
				print(_format_report(report))

				if args.apply:
					updates, notes = _build_mapping_updates(report, asset_roots)
					if updates:
						target_packages_cfg = config.setdefault("target_packages", {})
						if not isinstance(target_packages_cfg, dict):
							target_packages_cfg = {}
							config["target_packages"] = target_packages_cfg

						mapping_cfg = target_packages_cfg.setdefault("kt_role_image_assets", {})
						if not isinstance(mapping_cfg, dict):
							mapping_cfg = {}
							target_packages_cfg["kt_role_image_assets"] = mapping_cfg

						for role_id, filename in sorted(updates.items()):
							mapping_cfg[str(role_id)] = filename

						backup_path = _write_config_atomically(config_path, config)
						print("")
						print("Applied config updates")
						print("-" * 88)
						for role_id, filename in sorted(updates.items()):
							print(f"- {role_id} -> {filename}")
						print(f"Backup created: {backup_path}")
					else:
						print("")
						print("No safe auto-fixes were found to apply.")

					if notes:
						print("")
						print("Apply notes")
						print("-" * 88)
						for note in notes:
							print(f"- {note}")

					# Rebuild report from in-memory mapping after apply for strict-mode exit.
					updated_mapping = _parse_kt_mapping((config.get("target_packages") or {}))
					report = _build_report(guild, kt_role_ids, updated_mapping, asset_files)
					print("")
					print("Post-apply audit")
					print("=" * 88)
					print(_format_report(report))

				if args.strict and report.has_failures:
					self.exit_code = 1
				else:
					self.exit_code = 0
			except Exception as exc:
				print(f"ERROR: audit failed: {exc}")
				self.exit_code = 2
			finally:
				await self.close()

	client = AuditClient(intents=intents)
	try:
		client.run(token, reconnect=False, log_handler=None)
	except discord.LoginFailure:
		print("ERROR: Discord login failed. Check DISCORD_TOKEN.")
		return 2
	except Exception as exc:
		print(f"ERROR: failed to start Discord client: {exc}")
		return 2

	return client.exit_code


def main() -> int:
	args = _parse_args()
	try:
		return _run_audit(args)
	except KeyboardInterrupt:
		print("Interrupted.")
		return 130


if __name__ == "__main__":
	sys.exit(main())
