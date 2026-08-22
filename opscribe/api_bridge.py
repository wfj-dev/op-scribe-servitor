import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import discord

from . import _bot_globals as _g
from .constants import DATA_DIR
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
				return {"links": {}, "tokens": {}, "issued_for_user": {}}
			with open(self.path, "r", encoding="utf-8") as f:
				data = json.load(f) or {}
			data.setdefault("links", {})
			data.setdefault("tokens", {})
			data.setdefault("issued_for_user", {})
			return data
		except Exception:
			return {"links": {}, "tokens": {}, "issued_for_user": {}}

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

	async def issue_token_for_user(self, user_id: int, display_name: str) -> str:
		raw_token = secrets.token_urlsafe(48)
		token_hash = _sha256_text(raw_token)
		now = _iso_now()

		async with self.lock:
			data = self._load_unsafe()
			prev_token_id = data["issued_for_user"].get(str(user_id))
			if prev_token_id and prev_token_id in data["tokens"]:
				del data["tokens"][prev_token_id]

			token_id = secrets.token_urlsafe(18)
			data["tokens"][token_id] = {
				"token_hash": token_hash,
				"user_id": int(user_id),
				"display_name": str(display_name or ""),
				"created_at": now,
				"revoked": False,
			}
			data["issued_for_user"][str(user_id)] = token_id
			self._save_unsafe(data)

		return raw_token

	async def resolve_token(self, raw_token: str) -> Optional[AuthContext]:
		expected_hash = _sha256_text(raw_token)
		async with self.lock:
			data = self._load_unsafe()
			for token_id, row in data["tokens"].items():
				if row.get("revoked"):
					continue
				stored_hash = row.get("token_hash") or ""
				if hmac.compare_digest(stored_hash, expected_hash):
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
			self._save_unsafe(data)


class JerichoAPIBridge:
	def __init__(self, bot_client: discord.Client, config: dict[str, Any], logger: logging.Logger):
		self.bot = bot_client
		self.config = config
		self.logger = logger
		self.state = APIStateStore()
		self.app = web.Application(client_max_size=int(config.get("max_body_bytes") or DEFAULT_MAX_BODY_BYTES))
		self.runner: Optional[web.AppRunner] = None
		self.site: Optional[web.TCPSite] = None
		self.started = False

	def _public_base_url(self) -> str:
		return str(self.config.get("public_base_url") or "").strip()

	def _oauth_client_id(self) -> str:
		return str(os.getenv("DISCORD_OAUTH_CLIENT_ID") or self.config.get("oauth_client_id") or "").strip()

	def _oauth_client_secret(self) -> str:
		return str(os.getenv("DISCORD_OAUTH_CLIENT_SECRET") or self.config.get("oauth_client_secret") or "").strip()

	def _oauth_redirect_path(self) -> str:
		return str(self.config.get("oauth_redirect_path") or "/v1/link/callback").strip()

	def _oauth_redirect_uri(self) -> str:
		return f"{self._public_base_url().rstrip('/')}{self._oauth_redirect_path()}"

	def _link_ttl_seconds(self) -> int:
		return int(self.config.get("link_ttl_seconds") or DEFAULT_LINK_TTL_SECONDS)

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
		self.app.router.add_post("/v1/unlink", self.handle_unlink)

		self.runner = web.AppRunner(self.app, access_log=None)
		await self.runner.setup()
		host = str(self.config.get("host") or DEFAULT_API_HOST)
		port = int(self.config.get("port") or DEFAULT_API_PORT)
		self.site = web.TCPSite(self.runner, host=host, port=port)
		await self.site.start()
		self.started = True
		self.logger.info("API bridge started on %s:%s", host, port)

	async def stop(self) -> None:
		if not self.started:
			return
		try:
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
		if req.content_type != "application/json":
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
		players: list[dict[str, Any]] = []
		for p in queue_data.get("players") or []:
			uid = int(p.get("user_id") or 0)
			if uid <= 0:
				continue
			players.append({"user_id": str(uid), "platform": str(p.get("platform") or "unknown")})
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

		expires_at = _parse_iso(str(link_row.get("expires_at") or ""))
		if not expires_at or expires_at < _utcnow():
			return web.Response(status=410, text="Link session expired.")

		await self.state.update_link(link_id, {"oauth_consumed": True})

		oauth_client_id = self._oauth_client_id()
		oauth_client_secret = self._oauth_client_secret()
		redirect_uri = self._oauth_redirect_uri()
		if not oauth_client_id or not oauth_client_secret or not redirect_uri:
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
						return web.Response(status=401, text="OAuth token exchange failed.")
					token_json = await resp.json()
				access_token = str(token_json.get("access_token") or "")
				if not access_token:
					return web.Response(status=401, text="OAuth token exchange failed.")
				async with session.get(
					"https://discord.com/api/users/@me",
					headers={"Authorization": f"Bearer {access_token}"},
				) as resp:
					if resp.status != 200:
						return web.Response(status=401, text="Unable to fetch Discord account.")
					user_json = await resp.json()
			user_id = int(user_json.get("id") or 0)
			display_name = str(user_json.get("global_name") or user_json.get("username") or "Unknown")
		except Exception:
			return web.Response(status=401, text="OAuth exchange failed.")

		member = self._resolve_member(user_id)
		if not member:
			return web.Response(status=403, text="Discord user is not a member of the configured guild.")
		display_name = member.display_name

		issued = await self.state.issue_token_for_user(user_id, display_name)
		await self.state.update_link(
			link_id,
			{
				"status": "linked",
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

		msg = await channel.send(
			content=" ".join(pings) if pings else None,
			embed=embed,
			allowed_mentions=discord.AllowedMentions(roles=True) if pings else discord.AllowedMentions.none(),
		)
		queue_id = int(msg.id)
		queue_data["channel_id"] = int(channel.id)
		queue_data["message_id"] = queue_id

		async with _g.LFG_QUEUE_LOCK:
			_g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
			all_queues = _load_lfg_queues()
			all_queues[str(queue_id)] = queue_data
			_save_lfg_queues(all_queues)

		await msg.edit(view=LFGQueueView(queue_id))
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
