import asyncio
import base64
import email.policy
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import discord

from . import _bot_globals as _g
from .constants import DATA_DIR, HIGH_COMMAND_ROLE_ID, TECHMARINE_ROLE_NAME
from .forge_ops import (
	LFGQueueView,
	_build_lfg_embed,
	_get_lfg_initiation_trial_role_id,
	_get_lfg_max_expiry_minutes,
	_get_lfg_queue_types,
	_get_player_platform,
	_load_lfg_queues,
	_queue_player_mentions,
	_remove_lfg_queue_from_storage,
	_save_lfg_queues,
)


API_STATE_PATH = os.path.join(DATA_DIR, "api_auth_state.json")
DEFAULT_API_VERSION = 4
DEFAULT_LINK_TTL_SECONDS = 300
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8080
DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
MAX_WEB_AAR_FILES = 10
MAX_WEB_AAR_FILE_BYTES = 32 * 1024 * 1024
MAX_WEB_AAR_TOTAL_BYTES = 32 * 1024 * 1024
MAX_WEB_AAR_REQUEST_BYTES = 34 * 1024 * 1024
MAX_WEB_AAR_INFLIGHT_REQUESTS = 2
MAX_WEB_AAR_INFLIGHT_BYTES = 48 * 1024 * 1024
WEB_AAR_SUBMISSIONS_PATH = os.path.join(DATA_DIR, "web_aar_submissions.json")


def _web_aar_shared_secret() -> str:
	return os.getenv("STRATEGIUM_BOT_SHARED_SECRET") or os.getenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "")


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


def _iso_now() -> str:
	return _utcnow().isoformat()


def _parse_iso(ts: str) -> Optional[datetime]:
	try:
		return datetime.fromisoformat(ts)
	except Exception:
		return None


def _sha256_text(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
	try:
		if value is None:
			return default
		return int(value)
	except Exception:
		return None


def _pkce_verifier() -> str:
	return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
	digest = hashlib.sha256(verifier.encode("utf-8")).digest()
	return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _json_ok(payload: dict[str, Any], status: int = 200) -> web.Response:
	return web.json_response(payload, status=status)


def _json_error(code: str, message: str, status: int) -> web.Response:
	return web.json_response({"ok": False, "error": code, "message": message}, status=status)


def _parse_web_aar_multipart(body: bytes, content_type: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	if not content_type.lower().startswith("multipart/form-data;"):
		raise ValueError("multipart/form-data is required")
	message = BytesParser(policy=email.policy.default).parsebytes(
		f"MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n".encode("ascii") + body
	)
	if not message.is_multipart():
		raise ValueError("invalid multipart body")
	submission = None
	files: list[dict[str, Any]] = []
	for part in message.iter_parts():
		name = part.get_param("name", header="content-disposition")
		payload = part.get_payload(decode=True) or b""
		filename = part.get_filename()
		if name == "submission" and filename is None:
			if submission is not None:
				raise ValueError("submission field must appear once")
			try:
				submission = json.loads(payload.decode(part.get_content_charset() or "utf-8"))
			except (UnicodeDecodeError, json.JSONDecodeError) as error:
				raise ValueError("submission must contain a JSON object") from error
		elif name == "screenshots" and filename is not None:
			files.append({"filename": filename, "content_type": part.get_content_type(), "data": payload})
		else:
			raise ValueError("unexpected multipart field")
	if not isinstance(submission, dict):
		raise ValueError("submission must contain a JSON object")
	if not 1 <= len(files) <= MAX_WEB_AAR_FILES:
		raise ValueError("attach between 1 and 10 screenshots")
	total_bytes = 0
	for item in files:
		data = item["data"]
		if len(data) > MAX_WEB_AAR_FILE_BYTES:
			raise ValueError("each screenshot must be 32 MiB or smaller")
		total_bytes += len(data)
		content_type = item["content_type"]
		is_png = data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82") and content_type == "image/png"
		is_jpeg = data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9") and content_type == "image/jpeg"
		is_webp = (
			len(data) >= 12
			and data[:4] == b"RIFF"
			and data[8:12] == b"WEBP"
			and int.from_bytes(data[4:8], "little") + 8 <= len(data)
			and content_type == "image/webp"
		)
		if not (is_png or is_jpeg or is_webp):
			raise ValueError("screenshots must be valid PNG, JPEG, or WebP images")
	if total_bytes > MAX_WEB_AAR_TOTAL_BYTES:
		raise ValueError("combined screenshots must be 32 MiB or smaller")
	return submission, files


def _web_aar_submission_digest(submission: dict[str, Any], files: list[dict[str, Any]]) -> str:
	digest = hashlib.sha256(json.dumps(submission, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
	for item in files:
		digest.update(b"\0")
		digest.update(item["content_type"].encode("ascii"))
		digest.update(hashlib.sha256(item["data"]).digest())
	return digest.hexdigest()


def _load_web_aar_submissions() -> dict[str, Any]:
	try:
		with open(WEB_AAR_SUBMISSIONS_PATH, "r", encoding="utf-8") as file:
			payload = json.load(file)
		return payload if isinstance(payload, dict) else {}
	except (OSError, json.JSONDecodeError):
		return {}


def _save_web_aar_submissions(payload: dict[str, Any]) -> None:
	os.makedirs(os.path.dirname(WEB_AAR_SUBMISSIONS_PATH), exist_ok=True)
	temporary_path = WEB_AAR_SUBMISSIONS_PATH + ".tmp"
	with open(temporary_path, "w", encoding="utf-8") as file:
		json.dump(payload, file, ensure_ascii=False, indent=2)
		file.flush()
		os.fsync(file.fileno())
	os.replace(temporary_path, WEB_AAR_SUBMISSIONS_PATH)


def _prune_web_aar_submissions(
	journal: dict[str, Any],
	*,
	now: datetime,
	retention_days: int,
	cooldown_seconds: int,
	max_completed_entries: int,
) -> tuple[bool, int]:
	changed = False
	last_by_user = journal.get("_last_submission_by_user")
	if isinstance(last_by_user, dict):
		oldest_allowed = int(now.timestamp()) - max(3600, cooldown_seconds)
		for user_id, last_submission in list(last_by_user.items()):
			try:
				if int(last_submission) < oldest_allowed:
					del last_by_user[user_id]
					changed = True
			except (TypeError, ValueError):
				del last_by_user[user_id]
				changed = True

	cutoff = now - timedelta(days=max(1, retention_days))
	completed: list[tuple[str, datetime]] = []
	pending_count = 0
	for key, entry in list(journal.items()):
		if key.startswith("_") or not isinstance(entry, dict):
			continue
		if entry.get("status") != "complete":
			pending_count += 1
			continue
		created_at = _parse_iso(str(entry.get("created_at") or ""))
		if created_at is None:
			created_at = now
		elif created_at.tzinfo is None:
			created_at = created_at.replace(tzinfo=timezone.utc)
		if created_at < cutoff:
			del journal[key]
			changed = True
		else:
			completed.append((key, created_at))

	for key, _created_at in sorted(completed, key=lambda item: item[1])[:-max(1, max_completed_entries)]:
		del journal[key]
		changed = True
	return changed, pending_count


@dataclass
class AuthContext:
	token_id: str
	user_id: int
	display_name: str


class APIStateStore:
	def __init__(self, path: str = API_STATE_PATH):
		self.path = path
		self.lock = asyncio.Lock()

	def _load_unsafe(self) -> dict[str, Any]:
		try:
			if not os.path.exists(self.path):
				return {"links": {}, "tokens": {}, "token_index": {}, "issued_for_user": {}}
			with open(self.path, "r", encoding="utf-8") as f:
				data = json.load(f) or {}
			data.setdefault("links", {})
			data.setdefault("tokens", {})
			data.setdefault("token_index", {})
			data.setdefault("issued_for_user", {})
			return data
		except Exception:
			return {"links": {}, "tokens": {}, "token_index": {}, "issued_for_user": {}}

	def _save_unsafe(self, data: dict[str, Any]) -> None:
		os.makedirs(os.path.dirname(self.path), exist_ok=True)
		tmp = self.path + ".tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
			f.flush()
			try:
				os.fsync(f.fileno())
			except Exception:
				pass
		os.replace(tmp, self.path)

	async def create_link(self, public_base_url: str, redirect_path: str, ttl_seconds: int) -> dict[str, Any]:
		link_id = secrets.token_urlsafe(24)
		state = secrets.token_urlsafe(32)
		verifier = _pkce_verifier()
		challenge = _pkce_challenge(verifier)
		expires_at = (_utcnow() + timedelta(seconds=ttl_seconds)).isoformat()

		async with self.lock:
			data = self._load_unsafe()
			data["links"][link_id] = {
				"link_id": link_id,
				"state": state,
				"pkce_verifier": verifier,
				"pkce_challenge": challenge,
				"status": "pending",
				"created_at": _iso_now(),
				"expires_at": expires_at,
				"token_issued": False,
				"token_value": None,
				"oauth_consumed": False,
				"user_id": None,
				"display_name": None,
			}
			self._save_unsafe(data)

		redirect_uri = f"{public_base_url.rstrip('/')}{redirect_path}"
		return {
			"link_id": link_id,
			"state": state,
			"pkce_challenge": challenge,
			"expires_at": expires_at,
			"redirect_uri": redirect_uri,
		}

	async def get_link(self, link_id: str) -> Optional[dict[str, Any]]:
		async with self.lock:
			data = self._load_unsafe()
			return data["links"].get(link_id)

	async def update_link(self, link_id: str, patch: dict[str, Any]) -> bool:
		async with self.lock:
			data = self._load_unsafe()
			row = data["links"].get(link_id)
			if not row:
				return False
			row.update(patch)
			data["links"][link_id] = row
			self._save_unsafe(data)
			return True

	async def consume_link_token(self, link_id: str) -> Optional[dict[str, Any]]:
		async with self.lock:
			data = self._load_unsafe()
			row = data["links"].get(link_id)
			if not row:
				return None
			if row.get("status") != "linked" or row.get("token_issued"):
				return None
			token_value = row.get("token_value")
			if not token_value:
				return None
			row["token_issued"] = True
			data["links"][link_id] = row
			self._save_unsafe(data)
			return {
				"token": token_value,
				"user_id": row.get("user_id"),
				"display_name": row.get("display_name") or "",
			}

	async def issue_token_for_user(
		self,
		user_id: int,
		display_name: str,
		ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
	) -> str:
		raw_token = secrets.token_urlsafe(48)
		token_hash = _sha256_text(raw_token)
		now = _iso_now()
		expires_at = (_utcnow() + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()

		async with self.lock:
			data = self._load_unsafe()
			prev_token_id = data["issued_for_user"].get(str(user_id))
			if prev_token_id and prev_token_id in data["tokens"]:
				prev_hash = data["tokens"][prev_token_id].get("token_hash")
				if prev_hash:
					data["token_index"].pop(str(prev_hash), None)
				del data["tokens"][prev_token_id]

			token_id = secrets.token_urlsafe(18)
			data["tokens"][token_id] = {
				"token_hash": token_hash,
				"user_id": int(user_id),
				"display_name": str(display_name or ""),
				"created_at": now,
				"expires_at": expires_at,
				"revoked": False,
			}
			data["token_index"][token_hash] = token_id
			data["issued_for_user"][str(user_id)] = token_id
			self._save_unsafe(data)

		return raw_token

	async def resolve_token(self, raw_token: str) -> Optional[AuthContext]:
		expected_hash = _sha256_text(raw_token)
		now = _utcnow()
		async with self.lock:
			data = self._load_unsafe()

			token_id = data.get("token_index", {}).get(expected_hash)
			if not token_id:
				# Backward-compat path for old state files without index.
				for cand_id, row in data["tokens"].items():
					stored_hash = row.get("token_hash") or ""
					if hmac.compare_digest(stored_hash, expected_hash):
						token_id = cand_id
						data.setdefault("token_index", {})[expected_hash] = cand_id
						self._save_unsafe(data)
						break

			if not token_id:
				return None

			row = data["tokens"].get(token_id)
			if not row or row.get("revoked"):
				return None

			expires_at = _parse_iso(str(row.get("expires_at") or ""))
			if expires_at and expires_at < now:
				row["revoked"] = True
				data["tokens"][token_id] = row
				self._save_unsafe(data)
				return None

			stored_hash = row.get("token_hash") or ""
			if not hmac.compare_digest(stored_hash, expected_hash):
				return None

			return AuthContext(
				token_id=token_id,
				user_id=int(row.get("user_id") or 0),
				display_name=str(row.get("display_name") or ""),
			)
		return None

	async def revoke_token(self, token_id: str) -> bool:
		async with self.lock:
			data = self._load_unsafe()
			row = data["tokens"].get(token_id)
			if not row:
				return False
			row["revoked"] = True
			data["tokens"][token_id] = row
			token_hash = row.get("token_hash")
			if token_hash:
				data.get("token_index", {}).pop(str(token_hash), None)
			self._save_unsafe(data)
			return True

	async def cleanup(self) -> None:
		now = _utcnow()
		async with self.lock:
			data = self._load_unsafe()
			links = data.get("links", {})
			to_delete: list[str] = []
			for link_id, row in links.items():
				expires_at = _parse_iso(str(row.get("expires_at") or ""))
				if expires_at and expires_at < now - timedelta(hours=1):
					to_delete.append(link_id)
			for link_id in to_delete:
				del links[link_id]
			data["links"] = links

			tokens = data.get("tokens", {})
			for token_id, row in list(tokens.items()):
				expires_at = _parse_iso(str(row.get("expires_at") or ""))
				if row.get("revoked") or (expires_at and expires_at < now - timedelta(hours=1)):
					token_hash = row.get("token_hash")
					if token_hash:
						data.get("token_index", {}).pop(str(token_hash), None)
					del tokens[token_id]
			data["tokens"] = tokens
			self._save_unsafe(data)


class JerichoAPIBridge:
	def __init__(self, bot_client: discord.Client, config: dict[str, Any], logger: logging.Logger):
		self.bot = bot_client
		self.config = config
		self.logger = logger
		self.state = APIStateStore()
		configured_body_limit = int(config.get("max_body_bytes") or DEFAULT_MAX_BODY_BYTES)
		self.app = web.Application(client_max_size=configured_body_limit)
		self.web_aar_app = web.Application(client_max_size=MAX_WEB_AAR_REQUEST_BYTES)
		self.runner: Optional[web.AppRunner] = None
		self.site: Optional[web.TCPSite] = None
		self.started = False
		self.web_aar_lock = asyncio.Lock()
		self.web_aar_budget_lock = asyncio.Lock()
		self.web_aar_inflight_requests = 0
		self.web_aar_inflight_bytes = 0
		self.web_aar_recovery_task: Optional[asyncio.Task] = None

	def _public_base_url(self) -> str:
		return str(self.config.get("public_base_url") or "").strip()

	def _oauth_client_id(self) -> str:
		return str(os.getenv("DISCORD_OAUTH_CLIENT_ID") or self.config.get("oauth_client_id") or "").strip()

	def _oauth_client_secret(self) -> str:
		return str(os.getenv("DISCORD_OAUTH_CLIENT_SECRET") or "").strip()

	def _oauth_redirect_path(self) -> str:
		return str(self.config.get("oauth_redirect_path") or "/v1/link/callback").strip()

	def _oauth_redirect_uri(self) -> str:
		return f"{self._public_base_url().rstrip('/')}{self._oauth_redirect_path()}"

	def _link_ttl_seconds(self) -> int:
		return int(self.config.get("link_ttl_seconds") or DEFAULT_LINK_TTL_SECONDS)

	def _token_ttl_seconds(self) -> int:
		return int(self.config.get("token_ttl_seconds") or DEFAULT_TOKEN_TTL_SECONDS)

	async def start(self) -> None:
		if self.started:
			return

		self.app.router.add_get("/health", self.handle_health)
		self.app.router.add_post("/v1/link/start", self.handle_link_start)
		self.app.router.add_get("/v1/link/status/{link_id}", self.handle_link_status)
		self.app.router.add_get(self._oauth_redirect_path(), self.handle_link_callback)
		self.app.router.add_get("/v1/me", self.handle_me)
		self.app.router.add_get("/v1/missions", self.handle_missions)
		self.app.router.add_post("/v1/missions/start", self.handle_mission_start)
		self.app.router.add_post("/v1/missions/{queue_id}/join", self.handle_mission_join)
		self.app.router.add_post("/v1/missions/{queue_id}/leave", self.handle_mission_leave)
		self.app.router.add_post("/v1/unlink", self.handle_unlink)
		self.web_aar_app.router.add_post("/submissions", self.handle_web_aar_submission)
		self.web_aar_app.router.add_post("/access", self.handle_web_aar_access)
		self.app.add_subapp("/v1/aar/", self.web_aar_app)

		self.runner = web.AppRunner(self.app, access_log=None)
		await self.runner.setup()
		host = str(self.config.get("host") or DEFAULT_API_HOST)
		port = int(self.config.get("port") or DEFAULT_API_PORT)
		self.site = web.TCPSite(self.runner, host=host, port=port)
		await self.site.start()
		self.started = True
		self.web_aar_recovery_task = asyncio.create_task(self._web_aar_recovery_loop())
		self.logger.info("API bridge started on %s:%s", host, port)

	async def stop(self) -> None:
		if not self.started:
			return
		try:
			if self.web_aar_recovery_task:
				self.web_aar_recovery_task.cancel()
				try:
					await self.web_aar_recovery_task
				except asyncio.CancelledError:
					pass
				self.web_aar_recovery_task = None
			if self.site:
				await self.site.stop()
			if self.runner:
				await self.runner.cleanup()
		finally:
			self.started = False
			self.site = None
			self.runner = None
			self.logger.info("API bridge stopped")

	def _parse_bearer(self, req: web.Request) -> Optional[str]:
		auth = req.headers.get("Authorization", "")
		if not auth.startswith("Bearer "):
			return None
		token = auth[7:].strip()
		return token or None

	async def _require_auth(self, req: web.Request) -> Optional[AuthContext]:
		raw_token = self._parse_bearer(req)
		if not raw_token:
			return None
		return await self.state.resolve_token(raw_token)

	async def _load_body_json(self, req: web.Request) -> Optional[dict[str, Any]]:
		ct = (req.headers.get("Content-Type") or "").lower()
		if not ct.startswith("application/json"):
			return None
		try:
			body = await req.json()
			if isinstance(body, dict):
				return body
		except Exception:
			return None
		return None

	def _resolve_member(self, user_id: int) -> Optional[discord.Member]:
		guild = self._resolve_guild()
		if not guild:
			return None
		return guild.get_member(user_id)

	def _web_aar_member_allowed(self, member: Any) -> bool:
		if member is None or getattr(member, "bot", False):
			return False
		access_mode = (self.config.get("web_submission") or {}).get("access_mode", "staff")
		if access_mode == "members":
			return True
		if access_mode != "staff":
			return False
		return any(
			getattr(role, "id", None) == HIGH_COMMAND_ROLE_ID
			or getattr(role, "name", "") == TECHMARINE_ROLE_NAME
			for role in getattr(member, "roles", [])
		)

	def _web_aar_max_file_bytes(self, guild: Any = None) -> int:
		web_cfg = self.config.get("web_submission") or {}
		try:
			configured_limit = int(web_cfg.get("max_file_bytes") or MAX_WEB_AAR_FILE_BYTES)
		except (TypeError, ValueError):
			configured_limit = MAX_WEB_AAR_FILE_BYTES
		limit = min(MAX_WEB_AAR_FILE_BYTES, max(1, configured_limit))
		guild_limit = getattr(guild, "filesize_limit", None)
		if isinstance(guild_limit, int) and guild_limit > 0:
			limit = min(limit, guild_limit)
		return limit

	async def _fresh_web_member(self, user_id: int) -> Any:
		guild = self._resolve_guild()
		if guild is None:
			return None
		fetch_member = getattr(guild, "fetch_member", None)
		if callable(fetch_member):
			try:
				return await fetch_member(user_id)
			except discord.NotFound:
				return None
		return guild.get_member(user_id)

	async def handle_web_aar_access(self, req: web.Request) -> web.Response:
		secret = _web_aar_shared_secret()
		if not secret or not self.bot.is_ready():
			return _json_error("not_ready", "AAR access verification is unavailable.", 503)
		user_id = req.headers.get("X-Strategium-User-ID", "")
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		if not user_id.isdigit() or len(user_id) > 20:
			return _json_error("unauthorized", "Invalid account.", 401)
		try:
			if abs(int(_utcnow().timestamp()) - int(timestamp)) > 300:
				return _json_error("unauthorized", "Expired authorization.", 401)
		except ValueError:
			return _json_error("unauthorized", "Invalid authorization.", 401)
		if req.content_length is None or not 0 < req.content_length <= 1024:
			return _json_error("invalid_size", "Invalid access request size.", 413)
		body = await req.read()
		key = f"access-{user_id}"
		signed = f"{timestamp}\n{key}\n{user_id}\n{hashlib.sha256(body).hexdigest()}".encode()
		expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Invalid authorization.", 401)
		try:
			member = await self._fresh_web_member(int(user_id))
		except discord.HTTPException:
			return _json_error("not_ready", "Role verification is unavailable.", 503)
		is_member = member is not None and not getattr(member, "bot", False)
		guild = self._resolve_guild()
		return _json_ok({
			"allowed": self._web_aar_member_allowed(member),
			"guild_member": is_member,
			"display_name": str(member.display_name) if is_member else "",
			"max_file_bytes": self._web_aar_max_file_bytes(guild) if is_member else 0,
		})

	def _resolve_guild(self) -> Optional[discord.Guild]:
		gid = self.config.get("guild_id") or _g.CONFIG.get("guild_id")
		if gid:
			try:
				guild = self.bot.get_guild(int(gid))
				if guild:
					return guild
			except Exception:
				pass
		try:
			return self.bot.guilds[0] if self.bot.guilds else None
		except Exception:
			return None

	def _mission_from_queue(self, queue_id: int, queue_data: dict[str, Any]) -> dict[str, Any]:
		guild = self._resolve_guild()
		players: list[dict[str, Any]] = []
		for p in queue_data.get("players") or []:
			uid = int(p.get("user_id") or 0)
			if uid <= 0:
				continue
			member = guild.get_member(uid) if guild else None
			display_name = getattr(member, "display_name", None) or getattr(member, "name", None) or str(uid)
			players.append(
				{
					"user_id": str(uid),
					"platform": str(p.get("platform") or "unknown"),
					"display_name": str(display_name),
				}
			)
		return {
			"queue_id": str(queue_id),
			"queue_type": str(queue_data.get("queue_type") or ""),
			"created_at": str(queue_data.get("created_at") or ""),
			"expires_at": str(queue_data.get("expires_at") or ""),
			"player_count": len(players),
			"players": players,
			"message": str(queue_data.get("message") or ""),
			"initiation_trial": bool(queue_data.get("initiation_trial")),
		}

	async def handle_health(self, _req: web.Request) -> web.Response:
		await self.state.cleanup()
		return _json_ok(
			{
				"ok": True,
				"version": DEFAULT_API_VERSION,
				"ready": bool(self.bot.is_ready()),
				"timestamp": _iso_now(),
			}
		)

	async def handle_link_start(self, req: web.Request) -> web.Response:
		body = await self._load_body_json(req)
		if body is None:
			return _json_error("invalid_json", "Expected JSON object body.", 400)

		if body:
			return _json_error("invalid_body", "Body must be an empty object.", 400)

		base_url = self._public_base_url()
		oauth_client_id = self._oauth_client_id()
		if not base_url or not oauth_client_id:
			return _json_error("not_configured", "OAuth/public URL not configured.", 503)

		link = await self.state.create_link(base_url, self._oauth_redirect_path(), self._link_ttl_seconds())
		q = {
			"client_id": oauth_client_id,
			"response_type": "code",
			"redirect_uri": link["redirect_uri"],
			"scope": "identify guilds",
			"state": link["state"],
			"code_challenge": link["pkce_challenge"],
			"code_challenge_method": "S256",
		}
		authorize_url = f"https://discord.com/api/oauth2/authorize?{urlencode(q)}"

		return _json_ok(
			{
				"ok": True,
				"link_id": link["link_id"],
				"authorize_url": authorize_url,
				"expires_in": self._link_ttl_seconds(),
			}
		)

	async def handle_link_status(self, req: web.Request) -> web.Response:
		link_id = req.match_info.get("link_id", "")
		if not link_id:
			return _json_error("invalid_link", "Missing link id.", 400)

		row = await self.state.get_link(link_id)
		if not row:
			return _json_error("link_missing", "Unknown link id.", 404)

		expires_at = _parse_iso(str(row.get("expires_at") or ""))
		if not expires_at or expires_at < _utcnow():
			return _json_ok({"ok": True, "status": "expired"})

		status = str(row.get("status") or "pending")
		if status != "linked":
			return _json_ok({"ok": True, "status": "pending"})

		consumed = await self.state.consume_link_token(link_id)
		if not consumed:
			return _json_ok({"ok": True, "status": "linked"})

		return _json_ok(
			{
				"ok": True,
				"status": "linked",
				"token": consumed["token"],
				"user_id": str(consumed["user_id"]),
				"display_name": consumed["display_name"],
			}
		)

	async def handle_link_callback(self, req: web.Request) -> web.Response:
		code = req.query.get("code", "")
		state = req.query.get("state", "")
		if not code or not state:
			return web.Response(status=400, text="Missing OAuth state/code.")

		link_id: Optional[str] = None
		link_row: Optional[dict[str, Any]] = None
		async with self.state.lock:
			data = self.state._load_unsafe()
			for cand_id, cand in data.get("links", {}).items():
				if cand.get("state") == state:
					link_id = cand_id
					link_row = cand
					break

			if not link_id or not link_row:
				return web.Response(status=404, text="Link session not found.")

			if str(link_row.get("status") or "pending") != "pending":
				return web.Response(status=409, text="Link session already completed.")

			if bool(link_row.get("oauth_consumed", False)):
				return web.Response(status=409, text="Link session already consumed.")

			if bool(link_row.get("oauth_in_progress", False)):
				return web.Response(status=409, text="Link session already in progress.")

			expires_at = _parse_iso(str(link_row.get("expires_at") or ""))
			if not expires_at or expires_at < _utcnow():
				return web.Response(status=410, text="Link session expired.")

			link_row["oauth_in_progress"] = True
			data["links"][link_id] = link_row
			self.state._save_unsafe(data)

		oauth_client_id = self._oauth_client_id()
		oauth_client_secret = self._oauth_client_secret()
		redirect_uri = self._oauth_redirect_uri()
		if not oauth_client_id or not oauth_client_secret or not redirect_uri:
			await self.state.update_link(link_id, {"oauth_in_progress": False})
			return web.Response(status=503, text="OAuth not configured.")

		token_payload = {
			"client_id": oauth_client_id,
			"client_secret": oauth_client_secret,
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": redirect_uri,
			"code_verifier": str(link_row.get("pkce_verifier") or ""),
		}

		user_id = 0
		display_name = ""
		try:
			async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
				async with session.post(
					"https://discord.com/api/oauth2/token",
					data=token_payload,
					headers={"Content-Type": "application/x-www-form-urlencoded"},
				) as resp:
					if resp.status != 200:
						await self.state.update_link(link_id, {"oauth_in_progress": False})
						return web.Response(status=401, text="OAuth token exchange failed.")
					token_json = await resp.json()
				access_token = str(token_json.get("access_token") or "")
				if not access_token:
					await self.state.update_link(link_id, {"oauth_in_progress": False})
					return web.Response(status=401, text="OAuth token exchange failed.")
				async with session.get(
					"https://discord.com/api/users/@me",
					headers={"Authorization": f"Bearer {access_token}"},
				) as resp:
					if resp.status != 200:
						await self.state.update_link(link_id, {"oauth_in_progress": False})
						return web.Response(status=401, text="Unable to fetch Discord account.")
					user_json = await resp.json()
			user_id = int(user_json.get("id") or 0)
			display_name = str(user_json.get("global_name") or user_json.get("username") or "Unknown")
		except Exception:
			await self.state.update_link(link_id, {"oauth_in_progress": False})
			return web.Response(status=401, text="OAuth exchange failed.")

		member = self._resolve_member(user_id)
		if not member:
			await self.state.update_link(link_id, {"oauth_in_progress": False})
			return web.Response(status=403, text="Discord user is not a member of the configured guild.")
		display_name = member.display_name

		issued = await self.state.issue_token_for_user(user_id, display_name, ttl_seconds=self._token_ttl_seconds())
		await self.state.update_link(
			link_id,
			{
				"status": "linked",
				"oauth_consumed": True,
				"oauth_in_progress": False,
				"user_id": user_id,
				"display_name": display_name,
				"token_value": issued,
				"linked_at": _iso_now(),
			},
		)
		return web.Response(status=200, text="Link complete. You can return to the mod loader.")

	async def handle_me(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)
		member = self._resolve_member(auth.user_id)
		if not member:
			return _json_error("unauthorized", "User no longer in guild.", 401)
		return _json_ok(
			{
				"ok": True,
				"user": {
					"user_id": str(member.id),
					"display_name": member.display_name,
				},
			}
		)

	async def handle_missions(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)

		all_queues = _load_lfg_queues()
		items: list[dict[str, Any]] = []
		for queue_id_str, queue_data in all_queues.items():
			if str(queue_data.get("queue_type") or "") != "omega":
				continue
			try:
				queue_id = int(queue_id_str)
			except Exception:
				continue
			items.append(self._mission_from_queue(queue_id, queue_data))
		return _json_ok({"ok": True, "missions": items})

	async def handle_mission_start(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)

		body = await self._load_body_json(req)
		if body is None:
			return _json_error("invalid_json", "Expected JSON object body.", 400)

		expire_minutes = _safe_int(body.get("expire_minutes"), 0)
		if expire_minutes is None:
			return _json_error("invalid_expiry", "expire_minutes must be an integer.", 400)
		if expire_minutes <= 0:
			expire_minutes = int(self.config.get("mission_default_expire_minutes") or 30)
		max_expiry = _get_lfg_max_expiry_minutes()
		if expire_minutes > max_expiry:
			return _json_error("invalid_expiry", f"expire_minutes exceeds {max_expiry}", 400)

		initiation_trial_raw = body.get("initiation_trial", False)
		if not isinstance(initiation_trial_raw, bool):
			return _json_error("invalid_initiation_trial", "initiation_trial must be a boolean.", 400)
		initiation_trial = initiation_trial_raw

		message_raw = body.get("message")
		if message_raw is None:
			message = None
		elif isinstance(message_raw, str):
			message = message_raw.strip() or None
		else:
			return _json_error("invalid_message", "message must be a string.", 400)

		member = self._resolve_member(auth.user_id)
		if not member:
			return _json_error("unauthorized", "User no longer in guild.", 401)
		platform = _get_player_platform(member)
		if not platform:
			return _json_error("platform_missing", "User needs PC or Console role.", 403)

		queue_types = _get_lfg_queue_types()
		omega_cfg = queue_types.get("omega", {})
		now = _utcnow()
		expires_at = now + timedelta(minutes=expire_minutes)
		queue_data = {
			"queue_type": "omega",
			"initiation_trial": initiation_trial,
			"message": message,
			"creator_id": member.id,
			"channel_id": None,
			"players": [{"user_id": member.id, "platform": platform}],
			"created_at": now.isoformat(),
			"expires_at": expires_at.isoformat(),
			"message_id": None,
			"created_via": "api",
		}

		queue_channel_id = _safe_int(self.config.get("queue_channel_id"), None)
		if not queue_channel_id:
			return _json_error("not_configured", "api.queue_channel_id is required for mission start.", 503)

		guild = self._resolve_guild()
		if not guild:
			return _json_error("guild_missing", "Configured guild is not available.", 503)
		channel = guild.get_channel(int(queue_channel_id))
		if not channel:
			return _json_error("queue_channel_missing", "Configured queue channel is not available.", 503)

		embed = _build_lfg_embed(queue_data, guild)
		pings: list[str] = []
		role_id = omega_cfg.get("ping_role_id")
		if role_id:
			pings.append(f"<@&{int(role_id)}>")
		if initiation_trial:
			trial_role_id = _get_lfg_initiation_trial_role_id()
			if trial_role_id:
				pings.append(f"<@&{int(trial_role_id)}>")

		try:
			msg = await channel.send(
				content=" ".join(pings) if pings else None,
				embed=embed,
				allowed_mentions=discord.AllowedMentions(roles=True) if pings else discord.AllowedMentions.none(),
			)
		except Exception:
			return _json_error("discord_unavailable", "Unable to create queue message right now.", 503)
		queue_id = int(msg.id)
		queue_data["channel_id"] = int(channel.id)
		queue_data["message_id"] = queue_id

		async with _g.LFG_QUEUE_LOCK:
			_g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
			all_queues = _load_lfg_queues()
			all_queues[str(queue_id)] = queue_data
			_save_lfg_queues(all_queues)

		try:
			await msg.edit(view=LFGQueueView(queue_id))
		except Exception:
			async with _g.LFG_QUEUE_LOCK:
				_g.LFG_ACTIVE_QUEUES.pop(queue_id, None)
				all_queues = _load_lfg_queues()
				all_queues.pop(str(queue_id), None)
				_save_lfg_queues(all_queues)
			try:
				await msg.delete()
			except Exception:
				pass
			return _json_error("discord_unavailable", "Unable to finalize queue message right now.", 503)
		mission = self._mission_from_queue(queue_id, queue_data)
		return _json_ok({"ok": True, "mission": mission}, status=201)

	async def handle_mission_join(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)

		queue_id_str = req.match_info.get("queue_id", "")
		try:
			queue_id = int(queue_id_str)
		except Exception:
			return _json_error("invalid_queue", "queue_id must be an integer", 400)

		member = self._resolve_member(auth.user_id)
		if not member:
			return _json_error("unauthorized", "User no longer in guild.", 401)
		platform = _get_player_platform(member)
		if not platform:
			return _json_error("platform_missing", "User needs PC or Console role.", 403)

		async with _g.LFG_QUEUE_LOCK:
			all_queues = _load_lfg_queues()
			queue_data = all_queues.get(str(queue_id))
			if not queue_data:
				return _json_error("queue_missing", "Queue does not exist.", 404)
			if str(queue_data.get("queue_type") or "") != "omega":
				return _json_error("queue_type_invalid", "Queue is not an omega mission.", 400)

			players = list(queue_data.get("players") or [])
			if any(int(p.get("user_id") or 0) == member.id for p in players):
				return _json_error("already_joined", "User is already in this queue.", 409)

			queue_types = _get_lfg_queue_types()
			type_cfg = queue_types.get("omega", {})
			max_players = int(type_cfg.get("max_players") or 5)
			if len(players) >= max_players:
				return _json_error("queue_full", "Queue is full.", 409)

			max_console = type_cfg.get("max_console")
			if max_console is not None and platform == "console":
				console_count = sum(1 for p in players if p.get("platform") == "console")
				if console_count >= int(max_console):
					return _json_error("console_limit", "Console player limit reached.", 409)

			players.append({"user_id": member.id, "platform": platform})
			queue_data["players"] = players
			all_queues[str(queue_id)] = queue_data
			_g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
			_save_lfg_queues(all_queues)
			is_full = len(players) >= max_players

		guild = self._resolve_guild()
		channel = guild.get_channel(int(queue_data.get("channel_id") or 0)) if guild else None
		if channel:
			try:
				msg = await channel.fetch_message(queue_id)
				if is_full:
					await _remove_lfg_queue_from_storage(queue_id)
					await msg.delete()
					creator = guild.get_member(int(queue_data.get("creator_id") or 0)) if guild else None
					if creator:
						await channel.send(
							f"Queue full for {creator.mention}: {_queue_player_mentions(guild, players)}",
							allowed_mentions=discord.AllowedMentions(users=True),
						)
				else:
					await msg.edit(embed=_build_lfg_embed(queue_data, guild), view=LFGQueueView(queue_id))
			except Exception:
				pass

		return _json_ok({"ok": True, "queue_id": str(queue_id), "full": is_full})

	async def handle_mission_leave(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)

		queue_id_str = req.match_info.get("queue_id", "")
		try:
			queue_id = int(queue_id_str)
		except Exception:
			return _json_error("invalid_queue", "queue_id must be an integer", 400)

		member = self._resolve_member(auth.user_id)
		if not member:
			return _json_error("unauthorized", "User no longer in guild.", 401)

		queue_closed = False
		queue_data: Optional[dict[str, Any]] = None
		async with _g.LFG_QUEUE_LOCK:
			all_queues = _load_lfg_queues()
			queue_data = all_queues.get(str(queue_id))
			if not queue_data:
				return _json_error("queue_missing", "Queue does not exist.", 404)
			if str(queue_data.get("queue_type") or "") != "omega":
				return _json_error("queue_type_invalid", "Queue is not an omega mission.", 400)

			players = list(queue_data.get("players") or [])
			player_entry = next((p for p in players if int(p.get("user_id") or 0) == member.id), None)
			if not player_entry:
				return _json_error("not_in_queue", "User is not in this queue.", 409)

			players.remove(player_entry)
			if not players:
				queue_closed = True
				_g.LFG_ACTIVE_QUEUES.pop(queue_id, None)
				all_queues.pop(str(queue_id), None)
				_save_lfg_queues(all_queues)
			else:
				queue_data["players"] = players
				all_queues[str(queue_id)] = queue_data
				_g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
				_save_lfg_queues(all_queues)

		guild = self._resolve_guild()
		channel = guild.get_channel(int(queue_data.get("channel_id") or 0)) if guild and queue_data else None
		if channel:
			try:
				msg = await channel.fetch_message(queue_id)
				if queue_closed:
					await msg.delete()
				else:
					await msg.edit(embed=_build_lfg_embed(queue_data, guild), view=LFGQueueView(queue_id))
			except Exception:
				pass

		if queue_closed:
			return _json_ok({"ok": True, "queue_id": str(queue_id), "closed": True, "mission": None})
		return _json_ok(
			{
				"ok": True,
				"queue_id": str(queue_id),
				"closed": False,
				"mission": self._mission_from_queue(queue_id, queue_data),
			}
		)

	def _build_web_aar_record(
		self,
		submission: dict[str, Any],
		guild: discord.Guild,
		channel: Any,
		submitter: discord.Member,
		message_id: int,
		created_at: datetime,
	) -> tuple[dict[str, Any], Any, list[discord.Member]]:
		from types import SimpleNamespace
		from . import aar_ops

		mode_key = submission.get("mode")
		mode_config = aar_ops._AAR_SUBMISSION_MODE_CONFIG.get(mode_key)
		if not mode_config:
			raise ValueError("Select a supported AAR mode")
		mission_value = submission.get("mission")
		mission_values = {option.value for option in aar_ops._mission_options_for_mode(mode_key)}
		if mission_value not in mission_values:
			raise ValueError("Select a valid mission for this AAR mode")
		difficulty = submission.get("difficulty")
		difficulty_values = {option.value for option in aar_ops._difficulty_options_for_mode(mode_key)}
		if difficulty not in difficulty_values:
			raise ValueError("Select a valid difficulty for this AAR mode")
		rank = submission.get("rank")
		if rank not in {"A", "B", "C", "D"}:
			raise ValueError("Mission rank must be A, B, C, or D")

		raw_brothers = submission.get("brother_ids")
		if not isinstance(raw_brothers, list):
			raise ValueError("Select the battle-brothers who took part")
		brother_ids = [str(value) for value in raw_brothers if str(value).isdigit()]
		if len(brother_ids) != len(raw_brothers) or len(set(brother_ids)) != len(brother_ids):
			raise ValueError("Participant IDs must be unique Discord member IDs")
		max_brothers = 6 if mode_config.get("pvp_only") else 5 if mode_key in {"omega", "induction_omega"} else 3
		if not 2 <= len(brother_ids) <= max_brothers:
			raise ValueError(f"This report requires between 2 and {max_brothers} participants")
		if str(submitter.id) not in brother_ids:
			raise ValueError("The signed-in submitter must be included among the participants")
		participants = []
		for brother_id in brother_ids:
			member = guild.get_member(int(brother_id))
			if member is None or member.bot:
				raise ValueError("Every participant must be a current, non-bot guild member")
			participants.append(member)

		tags = submission.get("tags", [])
		if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
			raise ValueError("Mission tags must be a list")
		if len(set(tags)) != len(tags):
			raise ValueError("Mission tags must not be repeated")
		mission, _aar_type = aar_ops._mission_value_to_name_and_type(mission_value)
		allowed_tags = set(aar_ops._allowed_tag_keys(mode_key, difficulty, mission, len(participants), tags))
		if not set(tags).issubset(aar_ops._AAR_SUBMISSION_TAG_KEY_SET) or not set(tags).issubset(allowed_tags):
			raise ValueError("One or more selected tags are not valid for this mission, difficulty, and team")

		def bounded_int(name: str, low: int, high: int, default: int) -> int:
			value = submission.get(name, default)
			if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
				raise ValueError(f"{name.replace('_', ' ').title()} must be between {low} and {high}")
			return value

		view = aar_ops.AARSubmissionView(guild, submitter, brother_mentions=[member.mention for member in participants])
		view.testing_mode = False
		view.mode = mode_key
		view.mode_config = mode_config
		view.selected_mission_value = mission_value
		view.mission = mission
		view.aar_type = "pvp" if mode_config.get("pvp_only") else "pve"
		view.difficulty = difficulty
		view.rank = rank
		view.brothers = [member.mention for member in participants]
		view.brother_ids = [int(member.id) for member in participants]
		view.tags = list(tags)
		view.armory_data = bounded_int("armory_data", 0, 20, 0)
		view.kia_count = bounded_int("kia", 0, 4, 0) if mode_config.get("include_kia") else 0
		view.waves = bounded_int("waves", 1, 20, 10) if mode_config.get("include_waves") else 10
		gene_status = submission.get("gene_seed_status", "unknown")
		if gene_status not in {"unknown", "lost", "carried"}:
			raise ValueError("Gene-Seed status must be unknown, lost, or carried")
		view.gene_seed_status = gene_status if not mode_config.get("pvp_only") else "unknown"
		carrier_id = submission.get("gene_seed_carrier_id")
		view.gene_seed_carrier_id = str(carrier_id) if carrier_id is not None else None
		if view.gene_seed_status == "carried" and view.gene_seed_carrier_id not in brother_ids:
			raise ValueError("Gene-Seed carrier must be one of the selected participants")
		view.pvp_map = submission.get("pvp_map") or "Cathedrum"
		view.pvp_game_mode = submission.get("pvp_game_mode") or "Seize Ground"
		view.pvp_result = submission.get("pvp_result") or "W"
		if mode_config.get("pvp_only"):
			if view.pvp_map not in aar_ops._PVP_MAP_OPTIONS:
				raise ValueError("Select a valid PvP map")
			if view.pvp_game_mode not in aar_ops._PVP_GAME_MODE_OPTIONS:
				raise ValueError("Select a valid PvP game mode")
			if view.pvp_result not in aar_ops._PVP_RESULT_OPTIONS:
				raise ValueError("Select Win or Loss")
		view._sync_gene_seed_carrier_with_brothers()
		report = view._compose_report()
		role_ids = {int(value) for value in re.findall(r"<@&(\d+)>", report)}
		roles = [guild.get_role(role_id) for role_id in role_ids]
		if any(role is None for role in roles):
			raise ValueError("A required report role is not available in the configured guild")
		message = SimpleNamespace(
			id=message_id,
			content=report,
			mentions=participants,
			role_mentions=roles,
			created_at=created_at,
			edited_at=None,
			author=submitter,
			guild=guild,
			channel=channel,
		)
		record = aar_ops.parse_aar(message)
		if not record:
			raise ValueError("The selected report could not be parsed")
		errors = aar_ops.validate_aar(record)
		if errors:
			raise ValueError(" ".join(errors[:5]))
		return record, view, participants

	def _web_aar_embeds(self, view: Any, participants: list[discord.Member], files: list[dict[str, Any]], key: str) -> tuple[list[discord.Embed], list[discord.File]]:
		from . import aar_ops

		mission_label = view.mission or "Siege Operations"
		team_label = ", ".join(member.display_name for member in participants)
		tag_labels = [label for tag, label, _role_id in aar_ops._AAR_SUBMISSION_TAG_OPTIONS if tag in view.tags]
		discord_files = []
		embeds = []
		for index, item in enumerate(files, start=1):
			extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[item["content_type"]]
			filename = f"jericho-aar-{key[:8]}-{index:02d}.{extension}"
			discord_files.append(discord.File(io.BytesIO(item["data"]), filename=filename))
			embed = discord.Embed(
				title="Mission Chronicle" if index == 1 else f"Mission Evidence // {index:02d}",
				color=0x4F8A62,
			)
			if index == 1:
				embed.description = "A mission chronicle has been entered into the Watch archive with its accompanying field evidence."
				embed.add_field(name="Operation", value=mission_label, inline=True)
				embed.add_field(name="Threat", value=view.difficulty.replace("@", ""), inline=True)
				embed.add_field(name="Mission Rank", value=view.rank, inline=True)
				embed.add_field(name="Battle-Brothers", value=team_label[:1024], inline=False)
				if view.aar_type == "pvp":
					embed.add_field(name="Map", value=view.pvp_map, inline=True)
					embed.add_field(name="Game Mode", value=view.pvp_game_mode, inline=True)
					embed.add_field(name="Result", value="Victory" if view.pvp_result == "W" else "Defeat", inline=True)
				else:
					embed.add_field(name="Armory Data", value=str(view.armory_data), inline=True)
					if view.mode_config.get("include_waves"):
						embed.add_field(name="Waves", value=str(view.waves), inline=True)
					if view.mode_config.get("include_kia"):
						embed.add_field(name="KIA", value=str(view.kia_count), inline=True)
					if view.gene_seed_status != "unknown":
						embed.add_field(name="Gene-Seed", value=view.gene_seed_status.title(), inline=True)
				if tag_labels:
					embed.add_field(name="Recorded Tags", value=", ".join(tag_labels)[:1024], inline=False)
				embed.set_footer(text=f"STRATEGIUM WEB AAR {key}")
			else:
				embed.description = f"Evidence image {index} of {len(files)}."
			embed.set_image(url=f"attachment://{filename}")
			embeds.append(embed)
		return embeds, discord_files

	async def _find_web_aar_receipt(self, channel: Any, key: str, message_id: Any = None) -> Any:
		marker = f"STRATEGIUM WEB AAR {key}"
		bot_user_id = getattr(getattr(self.bot, "user", None), "id", None)

		def is_receipt(message: Any) -> bool:
			return (
				bot_user_id is not None
				and getattr(getattr(message, "author", None), "id", None) == bot_user_id
				and any(getattr(embed.footer, "text", None) == marker for embed in getattr(message, "embeds", []))
			)

		if message_id:
			try:
				message = await channel.fetch_message(int(message_id))
				if is_receipt(message):
					return message
			except Exception:
				pass
		try:
			async for message in channel.history(limit=100):
				if is_receipt(message):
					return message
		except Exception:
			pass
		return None

	async def _complete_web_aar_entry(
		self,
		key: str,
		entry: dict[str, Any],
		message: Any,
		guild: discord.Guild,
		channel: Any,
		submitter: discord.Member,
		journal: dict[str, Any],
	) -> dict[str, Any]:
		from . import aar_ops

		record, _view, _participants = self._build_web_aar_record(
			entry["submission"], guild, channel, submitter, int(message.id), getattr(message, "created_at", _utcnow())
		)
		record["source"] = "strategium_web"
		record["message_url"] = str(message.jump_url)
		record["screenshots"] = [
			{"filename": attachment.filename, "url": attachment.url, "size": attachment.size}
			for attachment in getattr(message, "attachments", [])
		]
		record["receipt_url"] = str(message.jump_url)
		if entry.get("status") not in {"record_saved", "complete"}:
			await aar_ops.save_aar_record(record)
			flush = getattr(_g.DATASTORE, "flush", None)
			if callable(flush):
				await flush()
			entry.update({"status": "record_saved", "record_id": str(message.id)})
			journal[key] = entry
			_save_web_aar_submissions(journal)
		if entry.get("status") != "complete":
			challenge_notifications = await aar_ops._process_challenge_tracking(record, guild)
			if challenge_notifications:
				await aar_ops._send_challenge_eligibility_notifications(challenge_notifications, guild)
			bot_module = __import__("sys").modules.get("opscribe.bot")
			milestone_check = getattr(bot_module, "_check_award_milestones_for_members", None) if bot_module else None
			if callable(milestone_check):
				await milestone_check([str(uid) for uid in record.get("brother_ids", [])], guild)
			from . import loa_ops
			for brother_id in record.get("brother_ids", []):
				if loa_ops._get_active_loa(int(brother_id)):
					await loa_ops.clear_loa_on_aar(int(brother_id), guild)
			entry.update({"status": "complete", "record_id": str(message.id), "receipt_url": str(message.jump_url)})
			journal[key] = entry
			_save_web_aar_submissions(journal)
		return record

	async def _retry_pending_web_aar_submissions(self) -> None:
		if not bool((self.config.get("web_submission") or {}).get("enabled", False)) or _g.DATASTORE is None:
			return
		guild = self._resolve_guild()
		if guild is None:
			return
		from . import aar_ops

		channel = aar_ops._resolve_aar_submission_channel(guild)
		if channel is None:
			return
		async with self.web_aar_lock:
			journal = _load_web_aar_submissions()
			web_cfg = self.config.get("web_submission") or {}
			journal_changed, _pending_count = _prune_web_aar_submissions(
				journal,
				now=_utcnow(),
				retention_days=int(web_cfg.get("completed_retention_days") or 30),
				cooldown_seconds=max(0, int(web_cfg.get("cooldown_seconds", 60))),
				max_completed_entries=max(1, int(web_cfg.get("max_completed_entries") or 5000)),
			)
			if journal_changed:
				_save_web_aar_submissions(journal)
			for key, entry in list(journal.items()):
				if key.startswith("_") or not isinstance(entry, dict) or entry.get("status") == "complete":
					continue
				if not isinstance(entry.get("submission"), dict):
					continue
				message = await self._find_web_aar_receipt(channel, key, entry.get("message_id"))
				if message is None:
					continue
				submitter_id = entry.get("submitter_id")
				submitter = self._resolve_member(int(submitter_id)) if str(submitter_id).isdigit() else None
				if submitter is None:
					continue
				entry.update({"status": "posted", "message_id": str(message.id), "receipt_url": str(message.jump_url)})
				journal[key] = entry
				_save_web_aar_submissions(journal)
				try:
					await self._complete_web_aar_entry(key, entry, message, guild, channel, submitter, journal)
				except Exception:
					self.logger.exception("Pending web AAR recovery failed idempotency_key=%s", key)

	async def _web_aar_recovery_loop(self) -> None:
		while self.started:
			try:
				await asyncio.sleep(60)
				if self.started:
					await self._retry_pending_web_aar_submissions()
			except asyncio.CancelledError:
				raise
			except Exception:
				self.logger.exception("Web AAR recovery loop failed")

	async def _reserve_web_aar_upload(self, size: int) -> bool:
		async with self.web_aar_budget_lock:
			if (
				self.web_aar_inflight_requests >= MAX_WEB_AAR_INFLIGHT_REQUESTS
				or self.web_aar_inflight_bytes + size > MAX_WEB_AAR_INFLIGHT_BYTES
			):
				return False
			self.web_aar_inflight_requests += 1
			self.web_aar_inflight_bytes += size
			return True

	async def _release_web_aar_upload(self, size: int) -> None:
		async with self.web_aar_budget_lock:
			self.web_aar_inflight_requests = max(0, self.web_aar_inflight_requests - 1)
			self.web_aar_inflight_bytes = max(0, self.web_aar_inflight_bytes - size)

	async def handle_web_aar_submission(self, req: web.Request) -> web.Response:
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR submissions are disabled.", 503)
		if req.content_length is None or req.content_length <= 0 or req.content_length > MAX_WEB_AAR_REQUEST_BYTES:
			return _json_error("invalid_size", "Submission exceeds the allowed upload size.", 413)
		if not await self._reserve_web_aar_upload(req.content_length):
			return _json_error("upload_capacity", "The AAR upload queue is busy. Retry shortly.", 429)
		try:
			return await self._handle_web_aar_submission_reserved(req)
		finally:
			await self._release_web_aar_upload(req.content_length)

	async def _handle_web_aar_submission_reserved(self, req: web.Request) -> web.Response:
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR submissions are disabled.", 503)
		secret = _web_aar_shared_secret()
		if not secret:
			return _json_error("not_configured", "Website AAR intake secret is not configured.", 503)
		if not self.bot.is_ready() or _g.DATASTORE is None:
			return _json_error("not_ready", "AAR archive is not ready.", 503)
		if req.content_length is None or req.content_length <= 0 or req.content_length > MAX_WEB_AAR_REQUEST_BYTES:
			return _json_error("invalid_size", "Submission exceeds the allowed upload size.", 413)
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		key = req.headers.get("X-Strategium-AAR-Idempotency-Key", "")
		user_id = req.headers.get("X-Strategium-User-ID", "")
		if not re.fullmatch(r"[0-9]{1,20}", user_id) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
			return _json_error("invalid_request", "Submission identity or idempotency key is invalid.", 400)
		try:
			timestamp_seconds = int(timestamp)
		except ValueError:
			return _json_error("invalid_timestamp", "Submission timestamp is invalid.", 401)
		if abs(int(_utcnow().timestamp()) - timestamp_seconds) > 300:
			return _json_error("expired_request", "Submission authorization has expired.", 401)
		try:
			body = await req.read()
		except web.HTTPRequestEntityTooLarge:
			return _json_error("invalid_size", "Submission exceeds the allowed upload size.", 413)
		body_hash = hashlib.sha256(body).hexdigest()
		signed = f"{timestamp}\n{key}\n{user_id}\n{body_hash}".encode("utf-8")
		expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Submission signature is invalid.", 401)
		try:
			submission, screenshots = _parse_web_aar_multipart(body, req.headers.get("Content-Type", ""))
		except ValueError as error:
			return _json_error("invalid_submission", str(error), 422)
		submission_hash = _web_aar_submission_digest(submission, screenshots)
		max_total = min(MAX_WEB_AAR_TOTAL_BYTES, int(web_cfg.get("max_total_bytes") or MAX_WEB_AAR_TOTAL_BYTES))
		if sum(len(item["data"]) for item in screenshots) > max_total:
			return _json_error("invalid_size", "Screenshot upload exceeds the configured limits.", 413)
		guild = self._resolve_guild()
		try:
			submitter = await self._fresh_web_member(int(user_id))
		except discord.HTTPException:
			return _json_error("not_ready", "Role verification is unavailable.", 503)
		if guild is None or submitter is None or submitter.bot:
			return _json_error("unauthorized", "Submitter is not a current member of the configured guild.", 403)
		if not self._web_aar_member_allowed(submitter):
			return _json_error("aar_access_denied", "AAR submission is limited to High Command and Watch Techmarines.", 403)
		max_image = self._web_aar_max_file_bytes(guild)
		if any(len(item["data"]) > max_image for item in screenshots):
			return _json_error("invalid_size", "A screenshot exceeds the Discord upload limit for this guild.", 413)
		from . import aar_ops
		channel = aar_ops._resolve_aar_submission_channel(guild)
		if channel is None:
			return _json_error("not_configured", "AAR receipt channel is unavailable.", 503)
		try:
			candidate, view, participants = self._build_web_aar_record(submission, guild, channel, submitter, 1, _utcnow())
		except (ValueError, TypeError, KeyError) as error:
			return _json_error("invalid_submission", str(error), 422)

		async with self.web_aar_lock:
			journal = _load_web_aar_submissions()
			journal_changed, pending_count = _prune_web_aar_submissions(
				journal,
				now=_utcnow(),
				retention_days=int(web_cfg.get("completed_retention_days") or 30),
				cooldown_seconds=max(0, int(web_cfg.get("cooldown_seconds", 60))),
				max_completed_entries=max(1, int(web_cfg.get("max_completed_entries") or 5000)),
			)
			if journal_changed:
				_save_web_aar_submissions(journal)
			entry = journal.get(key)
			if entry and entry.get("body_hash") != submission_hash:
				return _json_error("idempotency_conflict", "This submission key was already used for different data.", 409)
			if entry and entry.get("status") == "complete":
				return _json_ok({"ok": True, "record_id": str(entry.get("record_id") or ""), "receipt_url": str(entry.get("receipt_url") or ""), "duplicate": True})
			if not entry:
				now_seconds = int(_utcnow().timestamp())
				last_by_user = journal.get("_last_submission_by_user", {})
				cooldown_seconds = max(0, int(web_cfg.get("cooldown_seconds", 60)))
				last_submission = int(last_by_user.get(user_id, 0)) if isinstance(last_by_user, dict) else 0
				if cooldown_seconds and now_seconds - last_submission < cooldown_seconds:
					return _json_error("submission_cooldown", "Please wait before submitting another AAR.", 429)
				max_pending = max(1, int(web_cfg.get("max_pending_submissions") or 100))
				if pending_count >= max_pending:
					return _json_error("submission_queue_full", "The archive is processing its pending submissions. Retry shortly.", 503)
				entry = {"body_hash": submission_hash, "status": "pending", "created_at": _iso_now(), "submitter_id": user_id, "submission": submission}
				journal[key] = entry
				last_by_user = dict(last_by_user) if isinstance(last_by_user, dict) else {}
				last_by_user[user_id] = now_seconds
				journal["_last_submission_by_user"] = last_by_user
				_save_web_aar_submissions(journal)

			message = await self._find_web_aar_receipt(channel, key, entry.get("message_id"))
			if message is None:
				if entry.get("status") != "pending":
					return _json_error("receipt_pending", "Receipt delivery is not yet confirmed. Retry this submission or ask a Watch Techmarine to reconcile it.", 503)
				entry["status"] = "posting"
				journal[key] = entry
				_save_web_aar_submissions(journal)
				embeds, discord_files = self._web_aar_embeds(view, participants, screenshots, key)
				try:
					message = await channel.send(
						embeds=embeds,
						files=discord_files,
						allowed_mentions=discord.AllowedMentions.none(),
					)
				except Exception as error:
					if isinstance(error, discord.HTTPException) and 400 <= error.status < 500 and error.status != 408:
						entry["status"] = "pending"
						journal[key] = entry
						_save_web_aar_submissions(journal)
					self.logger.exception("Web AAR receipt post failed idempotency_key=%s", key)
					return _json_error("receipt_pending", "The report could not reach Discord yet. Retry this submission safely.", 503)
				finally:
					for file in discord_files:
						file.close()
			entry.update({"status": "posted", "message_id": str(message.id), "receipt_url": str(message.jump_url)})
			journal[key] = entry
			_save_web_aar_submissions(journal)

			try:
				await self._complete_web_aar_entry(key, entry, message, guild, channel, submitter, journal)
			except Exception:
				self.logger.exception("Web AAR processing remains pending idempotency_key=%s", key)
				return _json_ok({
					"ok": True,
					"processing_pending": True,
					"record_id": str(message.id),
					"receipt_url": str(message.jump_url),
					"message": "The receipt was posted; archive processing will be retried safely.",
				}, status=202)
			return _json_ok({"ok": True, "record_id": str(message.id), "receipt_url": str(message.jump_url)}, status=201)

	async def handle_unlink(self, req: web.Request) -> web.Response:
		auth = await self._require_auth(req)
		if not auth:
			return _json_error("unauthorized", "Invalid or missing bearer token.", 401)
		await self.state.revoke_token(auth.token_id)
		return _json_ok({"ok": True, "unlinked": True})


API_BRIDGE_INSTANCE: Optional[JerichoAPIBridge] = None


def _api_config_from_root(root_cfg: dict[str, Any]) -> dict[str, Any]:
	api_cfg = dict(root_cfg.get("api") or {})
	if not api_cfg.get("guild_id") and root_cfg.get("guild_id"):
		api_cfg["guild_id"] = root_cfg.get("guild_id")
	api_cfg["web_submission"] = dict(root_cfg.get("web_submission") or api_cfg.get("web_submission") or {})
	return api_cfg


async def start_api_bridge(bot_client: discord.Client, root_cfg: dict[str, Any], logger: logging.Logger) -> Optional[JerichoAPIBridge]:
	global API_BRIDGE_INSTANCE
	cfg = _api_config_from_root(root_cfg)
	if not bool(cfg.get("enabled", False)):
		return None
	if API_BRIDGE_INSTANCE and API_BRIDGE_INSTANCE.started:
		return API_BRIDGE_INSTANCE
	bridge = JerichoAPIBridge(bot_client, cfg, logger)
	await bridge.start()
	API_BRIDGE_INSTANCE = bridge
	return bridge


async def stop_api_bridge() -> None:
	global API_BRIDGE_INSTANCE
	if API_BRIDGE_INSTANCE:
		try:
			await API_BRIDGE_INSTANCE.stop()
		finally:
			API_BRIDGE_INSTANCE = None
