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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from typing import Any, Optional
from urllib.parse import unquote, urlencode, urlsplit

import aiohttp
from aiohttp import web
import discord

from . import _bot_globals as _g
from .constants import DATA_DIR, FORGEMASTER_ROLE_NAME, HIGH_COMMAND_ROLE_ID, TECHMARINE_ROLE_NAME
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
from .role_aliases import canonicalize_role_name


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
WEB_AAR_MEMBER_CACHE_SECONDS = 60
WEB_AAR_MEMBER_SEARCH_LIMIT = 25
WEB_AAR_SUBMISSIONS_PATH = os.path.join(DATA_DIR, "web_aar_submissions.json")
WEB_AAR_MUTATIONS_PATH = os.path.join(DATA_DIR, "web_aar_mutations.json")


def _web_aar_shared_secret() -> str:
	return os.getenv("STRATEGIUM_BOT_SHARED_SECRET") or os.getenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "")


WEB_AAR_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
DISCORD_MEDIA_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})


def _component_media_urls(component: Any) -> list[str]:
	if isinstance(component, dict):
		media = component.get("media")
		urls = [str(media.get("url") or "")] if isinstance(media, dict) else []
		children = list(component.get("components") or []) + list(component.get("items") or [])
	else:
		media = getattr(component, "media", None)
		urls = [str(getattr(media, "url", "") or "")] if media is not None else []
		children = list(getattr(component, "children", None) or []) + list(getattr(component, "items", None) or [])
	for child in children:
		urls.extend(_component_media_urls(child))
	return urls


def _web_aar_receipt_screenshots(message: Any) -> list[dict[str, Any]]:
	# Components V2 receipts surface their images only in the MediaGallery, not message.attachments.
	screenshots = [
		{"filename": str(attachment.filename), "url": str(attachment.url), "size": int(getattr(attachment, "size", 0) or 0), "attachment": attachment}
		for attachment in getattr(message, "attachments", None) or []
	]
	if screenshots:
		return screenshots
	seen: set[str] = set()
	for url in _component_media_urls({"components": list(getattr(message, "components", None) or [])}):
		parsed = urlsplit(url)
		if parsed.scheme != "https" or parsed.hostname not in DISCORD_MEDIA_HOSTS or url in seen:
			continue
		seen.add(url)
		screenshots.append({"filename": unquote(parsed.path.rsplit("/", 1)[-1]), "url": url, "size": 0, "attachment": None})
	return screenshots


def _public_screenshots(screenshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [{"filename": item["filename"], "url": item["url"], "size": item["size"]} for item in screenshots]


async def _download_discord_media(url: str, limit: int) -> bytes:
	parsed = urlsplit(url)
	if parsed.scheme != "https" or parsed.hostname not in DISCORD_MEDIA_HOSTS:
		raise ValueError("evidence is not hosted by Discord")
	data = bytearray()
	async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
		async with session.get(url, allow_redirects=False) as response:
			if response.status != 200:
				raise ValueError(f"evidence download returned HTTP {response.status}")
			async for chunk in response.content.iter_chunked(64 * 1024):
				data.extend(chunk)
				if len(data) > limit:
					raise ValueError("evidence exceeds the upload limit")
	return bytes(data)


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


def _web_aar_request_size_error(actual_size: Any) -> str:
	if isinstance(actual_size, int) and not isinstance(actual_size, bool) and actual_size > 0:
		actual_mib = actual_size / (1024 * 1024)
		return f"Submission body is {actual_mib:.2f} MiB; maximum request size is {MAX_WEB_AAR_REQUEST_BYTES // (1024 * 1024)} MiB."
	return f"Submission body exceeds the {MAX_WEB_AAR_REQUEST_BYTES // (1024 * 1024)} MiB request limit."


@web.middleware
async def _web_aar_request_limit(req: web.Request, handler: Any) -> web.StreamResponse:
	return await handler(req.clone(client_max_size=MAX_WEB_AAR_REQUEST_BYTES))


def _split_multipart(body: bytes, content_type: str) -> list[tuple[Any, bytes]]:
	# email.parser copies the body several times (~8x peak for 30MB), enough to OOM a 1GB host.
	header_parser = BytesParser(policy=email.policy.default)
	outer = header_parser.parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"), headersonly=True)
	boundary = outer.get_param("boundary")
	if not isinstance(boundary, str) or not boundary:
		raise ValueError("invalid multipart body")
	try:
		delimiter = b"--" + boundary.encode("ascii")
	except UnicodeEncodeError as error:
		raise ValueError("invalid multipart body") from error
	if body.startswith(delimiter):
		position = len(delimiter)
	else:
		first = body.find(b"\r\n" + delimiter)
		if first < 0:
			raise ValueError("invalid multipart body")
		position = first + 2 + len(delimiter)
	parts: list[tuple[Any, bytes]] = []
	while not body.startswith(b"--", position):
		if not body.startswith(b"\r\n", position):
			raise ValueError("invalid multipart body")
		start = position + 2
		end = body.find(b"\r\n" + delimiter, start)
		header_end = body.find(b"\r\n\r\n", start, end)
		if end < 0 or header_end < 0:
			raise ValueError("invalid multipart body")
		headers = header_parser.parsebytes(body[start:header_end] + b"\r\n\r\n", headersonly=True)
		parts.append((headers, body[header_end + 4:end]))
		position = end + 2 + len(delimiter)
	return parts


def _parse_web_aar_multipart(
	body: bytes, content_type: str, *, allow_kept_screenshots: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	if not content_type.lower().startswith("multipart/form-data;"):
		raise ValueError("multipart/form-data is required")
	submission = None
	keep_screenshots = False
	files: list[dict[str, Any]] = []
	for part, payload in _split_multipart(body, content_type):
		name = part.get_param("name", header="content-disposition")
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
		elif name == "keep_screenshots" and filename is None and payload.strip() == b"true":
			keep_screenshots = True
		else:
			raise ValueError("unexpected multipart field")
	if not isinstance(submission, dict):
		raise ValueError("submission must contain a JSON object")
	if not (allow_kept_screenshots and keep_screenshots and not files) and not 1 <= len(files) <= MAX_WEB_AAR_FILES:
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


def _load_web_aar_mutations() -> list[dict[str, Any]]:
	try:
		with open(WEB_AAR_MUTATIONS_PATH, "r", encoding="utf-8") as file:
			payload = json.load(file)
		return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
	except (OSError, json.JSONDecodeError):
		return []


def _save_web_aar_mutations(payload: list[dict[str, Any]]) -> None:
	os.makedirs(os.path.dirname(WEB_AAR_MUTATIONS_PATH), exist_ok=True)
	temporary_path = WEB_AAR_MUTATIONS_PATH + ".tmp"
	with open(temporary_path, "w", encoding="utf-8") as file:
		pending_requests = [item for item in payload if item.get("operation") == "edit_request" and item.get("status") == "pending"]
		other_mutations = [item for item in payload if not (item.get("operation") == "edit_request" and item.get("status") == "pending")]
		json.dump(pending_requests + other_mutations[-5000:], file, ensure_ascii=False, indent=2)
		file.flush()
		os.fsync(file.fileno())
	os.replace(temporary_path, WEB_AAR_MUTATIONS_PATH)


def _finish_web_aar_edit_proposal(mutation: dict[str, Any]) -> None:
	identity = mutation.get("proposal_id")
	if not identity:
		return
	mutations = _load_web_aar_mutations()
	proposal = next((item for item in mutations if item.get("request_id") == identity and item.get("operation") == "edit_request"), None)
	if proposal is not None and proposal.get("status") == "pending":
		proposal.update(status="resolved", reviewed_by=str(mutation["actor_id"]), reviewed_at=_iso_now(), editor_note=mutation.get("proposal_note") or "Proposed edit approved.")
		_save_web_aar_mutations(mutations)


def _upsert_web_aar_mutation(entry: dict[str, Any]) -> None:
	mutations = _load_web_aar_mutations()
	request_id = str(entry.get("request_id") or "")
	for index, existing in enumerate(mutations):
		if str(existing.get("request_id") or "") == request_id:
			mutations[index] = entry
			_save_web_aar_mutations(mutations)
			return
	mutations.append(entry)
	_save_web_aar_mutations(mutations)


def _reconciliation_review_id(mutation: dict[str, Any], review: dict[str, Any]) -> str:
	identity = "\n".join((
		str(mutation.get("operation") or ""),
		str(mutation.get("record_id") or ""),
		str(review.get("user_id") or ""),
		str(review.get("challenge") or ""),
	))
	return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


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
		self.web_aar_app = web.Application(
			client_max_size=MAX_WEB_AAR_REQUEST_BYTES, middlewares=[_web_aar_request_limit]
		)
		self.runner: Optional[web.AppRunner] = None
		self.site: Optional[web.TCPSite] = None
		self.started = False
		self.web_aar_lock = asyncio.Lock()
		self.web_aar_budget_lock = asyncio.Lock()
		self.web_aar_inflight_requests = 0
		self.web_aar_inflight_bytes = 0
		self.web_aar_recovery_task: Optional[asyncio.Task] = None
		self.web_aar_member_cache: list[dict[str, str]] = []
		self.web_aar_member_cache_expires = 0.0
		self.web_aar_member_cache_guild_id: Optional[int] = None
		self.web_aar_member_cache_lock = asyncio.Lock()

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
		self.web_aar_app.router.add_post("/members", self.handle_web_aar_member_search)
		self.web_aar_app.router.add_post("/edit-requests", self.handle_web_aar_edit_request)
		self.web_aar_app.router.add_post("/own/records/{record_id}", self.handle_web_aar_member_record)
		self.web_aar_app.router.add_post("/own/records/{record_id}/delete-request", self.handle_web_aar_member_delete_request)
		self.web_aar_app.router.add_post("/management/edit-requests/{request_id}/{decision:resolve|reject}", self.handle_web_aar_edit_request_decision)
		self.web_aar_app.router.add_post("/war", self.handle_web_war)
		self.web_aar_app.router.add_post("/management/list", self.handle_web_aar_management_list)
		self.web_aar_app.router.add_post("/management/records/{record_id}", self.handle_web_aar_management_detail)
		self.web_aar_app.router.add_patch("/management/records/{record_id}", self.handle_web_aar_management_edit)
		self.web_aar_app.router.add_post("/management/records/{record_id}/delete-request", self.handle_web_aar_delete_request)
		self.web_aar_app.router.add_post("/management/records/{record_id}/delete-confirm", self.handle_web_aar_delete_confirm)
		self.web_aar_app.router.add_post("/management/records/{record_id}/delete-reject", self.handle_web_aar_delete_reject)
		self.web_aar_app.router.add_post("/management/records/{record_id}/edit-resume", self.handle_web_aar_management_edit_resume)
		self.web_aar_app.router.add_post("/management/records/{record_id}/reviews/{review_id}/acknowledge", self.handle_web_aar_reconciliation_acknowledge)
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
		return member is not None and not getattr(member, "bot", False)

	def _web_aar_staff_capabilities(self, member: Any) -> dict[str, bool]:
		if not self._web_aar_member_allowed(member):
			return {"can_edit": False, "can_delete": False}
		roles = getattr(member, "roles", []) or []
		role_ids = {getattr(role, "id", None) for role in roles}
		role_names = {canonicalize_role_name(str(getattr(role, "name", "")), role_aliases=_g.CONFIG.get("role_aliases") or {}).casefold() for role in roles}
		is_forgemaster = FORGEMASTER_ROLE_NAME.casefold() in role_names
		can_edit = HIGH_COMMAND_ROLE_ID in role_ids or TECHMARINE_ROLE_NAME.casefold() in role_names or is_forgemaster
		return {"can_edit": can_edit, "can_delete": is_forgemaster}

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
		# Members intent keeps the cache current; a REST fetch per web request burns the bot's rate limit.
		cached = guild.get_member(user_id)
		if cached is not None:
			return cached
		fetch_member = getattr(guild, "fetch_member", None)
		if callable(fetch_member):
			try:
				return await fetch_member(user_id)
			except discord.NotFound:
				return None
		return None

	async def handle_web_war(self, req: web.Request) -> web.Response:
		from . import galactic_war_runtime as war

		secret = _web_aar_shared_secret()
		if not secret or not self.bot.is_ready():
			return _json_error("not_ready", "Galactic War is unavailable.", 503)
		user_id = req.headers.get("X-Strategium-User-ID", "")
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		if not user_id.isdigit() or len(user_id) > 20:
			return _json_error("unauthorized", "Invalid account.", 401)
		try:
			if abs(int(_utcnow().timestamp()) - int(timestamp)) > 300:
				return _json_error("unauthorized", "Expired authorization.", 401)
		except ValueError:
			return _json_error("unauthorized", "Invalid authorization.", 401)
		if req.content_length is None or not 0 < req.content_length <= DEFAULT_MAX_BODY_BYTES:
			return _json_error("invalid_size", "Invalid war request size.", 413)
		body = await req.read()
		signed = f"{timestamp}\nwar-{user_id}\n{user_id}\n{hashlib.sha256(body).hexdigest()}".encode()
		expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Invalid authorization.", 401)
		try:
			payload = json.loads(body)
			if not isinstance(payload, dict):
				raise ValueError("A JSON object is required")
			member = await self._fresh_web_member(int(user_id))
			if member is None or getattr(member, "bot", False):
				return _json_error("forbidden", "Guild membership is required.", 403)
			guild = self._resolve_guild()
			action = payload.get("action", "snapshot")
			if action != "snapshot":
				members, _rosters, _companies, _teams = war.directory(guild)
				actor = members.get(user_id)
				if actor is None:
					return _json_error("forbidden", "Guild membership is required.", 403)
				await asyncio.to_thread(war.get_store().execute, action, actor, payload, members, _utcnow())
			result = await asyncio.to_thread(war.web_snapshot, guild, user_id, str(payload.get("planet_id") or ""))
			return _json_ok(result)
		except PermissionError as error:
			return _json_error("forbidden", str(error), 403)
		except (ValueError, KeyError, TypeError) as error:
			return _json_error("invalid_war_action", str(error), 409)
		except (OSError, discord.HTTPException):
			self.logger.exception("Galactic War request failed")
			return _json_error("not_ready", "Galactic War is unavailable.", 503)

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
			"allowed": is_member,
			"guild_member": is_member,
			"display_name": str(member.display_name) if is_member else "",
			"max_file_bytes": self._web_aar_max_file_bytes(guild) if is_member else 0,
			"aar_management": self._web_aar_staff_capabilities(member),
		})

	async def _web_aar_member_directory(self, guild: discord.Guild) -> list[dict[str, str]]:
		guild_id = int(guild.id)
		now = time.monotonic()
		if self.web_aar_member_cache_guild_id == guild_id and self.web_aar_member_cache_expires > now:
			return self.web_aar_member_cache
		async with self.web_aar_member_cache_lock:
			now = time.monotonic()
			if self.web_aar_member_cache_guild_id == guild_id and self.web_aar_member_cache_expires > now:
				return self.web_aar_member_cache
			members = []
			if getattr(guild, "chunked", False):
				for member in guild.members:
					if self._web_aar_member_allowed(member):
						members.append({"id": str(member.id), "name": str(member.display_name)})
			else:
				async for member in guild.fetch_members(limit=None):
					if self._web_aar_member_allowed(member):
						members.append({"id": str(member.id), "name": str(member.display_name)})
			members.sort(key=lambda item: (item["name"].casefold(), item["id"]))
			self.web_aar_member_cache = members
			self.web_aar_member_cache_guild_id = guild_id
			self.web_aar_member_cache_expires = time.monotonic() + WEB_AAR_MEMBER_CACHE_SECONDS
			return members

	async def handle_web_aar_member_search(self, req: web.Request) -> web.Response:
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR submissions are disabled.", 503)
		secret = _web_aar_shared_secret()
		if not secret or not self.bot.is_ready() or _g.DATASTORE is None:
			return _json_error("not_ready", "Guild member search is unavailable.", 503)
		if req.content_length is None or not 0 < req.content_length <= 2048:
			return _json_error("invalid_size", "Invalid member search request size.", 413)
		user_id = req.headers.get("X-Strategium-User-ID", "")
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		if not re.fullmatch(r"[0-9]{1,20}", user_id):
			return _json_error("unauthorized", "Invalid account.", 401)
		try:
			if abs(int(_utcnow().timestamp()) - int(timestamp)) > 300:
				return _json_error("unauthorized", "Expired authorization.", 401)
		except ValueError:
			return _json_error("unauthorized", "Invalid authorization.", 401)
		body = await req.read()
		key = f"members-{user_id}"
		signed = f"{timestamp}\n{key}\n{user_id}\n{hashlib.sha256(body).hexdigest()}".encode()
		expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Invalid authorization.", 401)
		try:
			payload = json.loads(body)
		except (UnicodeDecodeError, json.JSONDecodeError):
			return _json_error("invalid_query", "Search query is invalid.", 400)
		query = str(payload.get("query") or "").strip().casefold() if isinstance(payload, dict) else ""
		if not 2 <= len(query) <= 64:
			return _json_error("invalid_query", "Search with 2 to 64 characters.", 400)
		try:
			member = await self._fresh_web_member(int(user_id))
		except discord.HTTPException:
			return _json_error("not_ready", "Guild membership verification is unavailable.", 503)
		if not self._web_aar_member_allowed(member):
			return _json_error("unauthorized", "A current guild member account is required.", 403)
		guild = self._resolve_guild()
		if guild is None:
			return _json_error("not_ready", "The configured guild is unavailable.", 503)
		try:
			directory = await self._web_aar_member_directory(guild)
		except discord.HTTPException:
			return _json_error("not_ready", "Guild member search is unavailable.", 503)
		matches = [item for item in directory if query in item["name"].casefold()]
		matches.sort(key=lambda item: (not item["name"].casefold().startswith(query), item["name"].casefold(), item["id"]))
		return _json_ok({"members": matches[:WEB_AAR_MEMBER_SEARCH_LIMIT], "has_more": len(matches) > WEB_AAR_MEMBER_SEARCH_LIMIT})

	async def _authorize_web_aar_management(
		self, req: web.Request, action: str, record_id: str = "", capability: str | None = "can_edit"
	) -> tuple[Any, dict[str, Any]] | web.Response:
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR management is disabled.", 503)
		secret = _web_aar_shared_secret()
		if not secret or not self.bot.is_ready() or _g.DATASTORE is None:
			return _json_error("not_ready", "AAR management is unavailable.", 503)
		if req.content_length is None or not 0 < req.content_length <= 8192:
			return _json_error("invalid_size", "Invalid AAR management request size.", 413)
		user_id = req.headers.get("X-Strategium-User-ID", "")
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		if not re.fullmatch(r"[0-9]{1,20}", user_id):
			return _json_error("unauthorized", "Invalid account.", 401)
		try:
			if abs(int(_utcnow().timestamp()) - int(timestamp)) > 300:
				return _json_error("unauthorized", "Expired authorization.", 401)
		except ValueError:
			return _json_error("unauthorized", "Invalid authorization.", 401)
		body = await req.read()
		key = f"management-{action}-{record_id or 'list'}"
		signed = f"{timestamp}\n{key}\n{user_id}\n{hashlib.sha256(body).hexdigest()}".encode()
		expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Invalid authorization.", 401)
		try:
			payload = json.loads(body)
		except (UnicodeDecodeError, json.JSONDecodeError):
			return _json_error("invalid_request", "AAR management request is invalid.", 400)
		if not isinstance(payload, dict):
			return _json_error("invalid_request", "AAR management request is invalid.", 400)
		try:
			member = await self._fresh_web_member(int(user_id))
		except discord.HTTPException:
			return _json_error("not_ready", "Role verification is unavailable.", 503)
		if not self._web_aar_member_allowed(member):
			return _json_error("unauthorized", "A current guild member account is required.", 403)
		capabilities = self._web_aar_staff_capabilities(member)
		if capability is not None and capabilities.get(capability) is not True:
			return _json_error("forbidden", "AAR management permission is required.", 403)
		return member, payload

	def _web_aar_submission_for_record(self, record_id: str, record: dict[str, Any]) -> Optional[dict[str, Any]]:
		submission = record.get("web_submission")
		if isinstance(submission, dict):
			return submission
		journal = _load_web_aar_submissions()
		key = str(record.get("web_submission_key") or "")
		entry = journal.get(key) if key else None
		if not isinstance(entry, dict) or str(entry.get("record_id") or "") != record_id:
			entry = next((value for value in journal.values()
				if isinstance(value, dict) and str(value.get("record_id") or "") == record_id), None)
		return entry.get("submission") if isinstance(entry, dict) and isinstance(entry.get("submission"), dict) else None

	async def handle_web_aar_edit_request(self, req: web.Request) -> web.Response:
		authorized = await self._authorize_web_aar_management(req, "request-edit", capability=None)
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		record_id = str(payload.get("record_id") or "")
		submission = payload.get("submission")
		if not re.fullmatch(r"[0-9]{1,20}", record_id) or not isinstance(submission, dict):
			return _json_error("invalid_edit_request", "Provide a website AAR ID and the proposed report fields.", 400)
		async with self.web_aar_lock:
			record = _g.DATASTORE.get_record(record_id)
			if not isinstance(record, dict) or record.get("source") != "strategium_web":
				return _json_error("not_found", "AAR not found.", 404)
			owners = {str(identity) for identity in record.get("brother_ids", [])}
			owners.add(str(record.get("submitter_id") or ""))
			if str(actor.id) not in owners:
				return _json_error("forbidden", "You can edit only website AARs you submitted or participated in.", 403)
			fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
			if payload.get("original_record_hash") != fingerprint:
				return _json_error("stale_record", "This AAR changed. Reload it before proposing an edit.", 409)
			original = self._web_aar_submission_for_record(record_id, record)
			if original is None:
				return _json_error("record_unavailable", "This AAR has no editable submission data.", 409)
			allowed = {"mode", "mission", "difficulty", "rank", "brother_ids", "inductee_ids", "tags", "armory_data", "gene_seed_status", "gene_seed_carrier_id", "waves", "kia", "pvp_map", "pvp_game_mode", "pvp_result", "strike_id", "strike_success"}
			if set(submission) - allowed:
				return _json_error("invalid_submission", "Unexpected report fields.", 422)
			if all(submission.get(key) == original.get(key) for key in set(submission) | set(original)):
				return _json_error("no_changes", "Change the report before submitting an edit.", 422)
			guild = self._resolve_guild()
			if guild is None:
				return _json_error("not_ready", "The configured guild is unavailable.", 503)
			from . import aar_ops
			channel = aar_ops._resolve_aar_submission_channel(guild)
			if channel is None:
				return _json_error("not_configured", "AAR receipt channel is unavailable.", 503)
			try:
				self._build_web_aar_record(submission, guild, channel, actor, int(record_id), _utcnow(), require_submitter=False)
			except (ValueError, TypeError, KeyError) as error:
				return _json_error("invalid_submission", str(error), 422)
			mutations = _load_web_aar_mutations()
			if any(item.get("record_id") == record_id and item.get("operation") == "delete" and item.get("status") in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"} for item in mutations):
				return _json_error("delete_pending", "This AAR is locked while deletion is pending.", 409)
			existing = next((item for item in mutations if item.get("operation") == "edit_request" and item.get("record_id") == record_id and item.get("requester_id") == str(actor.id) and item.get("status") == "pending"), None)
			if existing:
				if existing.get("submission") == submission:
					return _json_ok({"ok": True, "request_id": existing["request_id"], "status": "pending", "duplicate": True})
				return _json_error("edit_request_pending", "You already have a pending request for this AAR.", 409)
			pending = [item for item in mutations if item.get("operation") == "edit_request" and item.get("requester_id") == str(actor.id) and item.get("status") == "pending"]
			if len(pending) >= 10:
				return _json_error("edit_request_limit", "You already have ten pending AAR correction requests.", 429)
			if sum(item.get("operation") == "edit_request" and item.get("status") == "pending" for item in mutations) >= 1000:
				return _json_error("edit_queue_full", "The correction queue is full; please contact an AAR editor.", 503)
			identity = secrets.token_hex(12)
			mutations.append({
				"request_id": identity, "operation": "edit_request", "record_id": record_id,
				"requester_id": str(actor.id), "requester_name": str(actor.display_name),
				"submission": submission, "original_submission": original,
				"requested_changes": ", ".join(key.replace("_", " ") for key in sorted(submission) if submission.get(key) != original.get(key)),
				"status": "pending", "requested_at": _iso_now(), "original_record_hash": fingerprint,
			})
			_save_web_aar_mutations(mutations)
		return _json_ok({"ok": True, "request_id": identity, "status": "pending"}, 201)

	async def handle_web_aar_edit_request_decision(self, req: web.Request) -> web.Response:
		identity = req.match_info.get("request_id", "")
		decision = req.match_info.get("decision", "")
		if not re.fullmatch(r"[a-f0-9]{24}", identity) or decision not in {"resolve", "reject"}:
			return _json_error("not_found", "Edit request not found.", 404)
		authorized = await self._authorize_web_aar_management(req, f"edit-requests/{identity}/{decision}")
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		note = str(payload.get("note") or "").strip()
		if not 4 <= len(note) <= 500:
			return _json_error("invalid_note", "Provide an editor note between 4 and 500 characters.", 400)
		async with self.web_aar_lock:
			mutations = _load_web_aar_mutations()
			request = next((item for item in mutations if item.get("operation") == "edit_request" and item.get("request_id") == identity), None)
			if request is None:
				return _json_error("not_found", "Edit request not found.", 404)
			status = "resolved" if decision == "resolve" else "rejected"
			if request.get("status") != "pending":
				if request.get("status") == status:
					return _json_ok({"ok": True, "request_id": identity, "status": status, "duplicate": True})
				return _json_error("edit_request_closed", "This edit request is already closed.", 409)
			if any(item.get("proposal_id") == identity and item.get("operation") == "edit" and item.get("status") in {"pending", "receipt_updated", "archive_saved", "complete"} for item in mutations):
				return _json_error("approval_processing", "Approval has started. Resume the pending archive update instead of changing the decision.", 409)
			if decision == "resolve":
				if isinstance(request.get("submission"), dict):
					return _json_error("approval_required", "Approve the proposed report through the AAR editor.", 409)
				record = _g.DATASTORE.get_record(request["record_id"])
				fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
				if not isinstance(record, dict) or fingerprint == request["original_record_hash"]:
					return _json_error("edit_not_saved", "Save the requested AAR correction before resolving this request.", 409)
			request.update(status=status, reviewed_by=str(actor.id), reviewed_at=_iso_now(), editor_note=note)
			_save_web_aar_mutations(mutations)
		return _json_ok({"ok": True, "request_id": identity, "status": status})

	async def handle_web_aar_management_list(self, req: web.Request) -> web.Response:
		authorized = await self._authorize_web_aar_management(req, "list")
		if isinstance(authorized, web.Response):
			return authorized
		_, payload = authorized
		query = str(payload.get("query") or "").strip().casefold()[:64]
		queue_kind = payload.get("queue_kind") or "all"
		queue_groups = {"all": ("edit_requests", "pending_deletions", "pending_edits", "reconciliation_reviews"), "requests": ("edit_requests", "pending_deletions"), "edit": ("edit_requests",), "delete": ("pending_deletions",), "recovery": ("pending_edits", "reconciliation_reviews")}
		if not isinstance(queue_kind, str) or queue_kind not in queue_groups:
			return _json_error("invalid_queue_kind", "Select a valid review queue.", 400)
		try:
			offset = max(0, min(int(payload.get("offset") or 0), 100000))
			queue_offset = max(0, min(int(payload.get("queue_offset") or 0), 100000))
		except (TypeError, ValueError):
			offset = 0
			queue_offset = 0
		all_records = _g.DATASTORE.get_all_records()
		records = [
			(str(record_id), record) for record_id, record in all_records.items()
			if isinstance(record, dict) and record.get("source") == "strategium_web"
		]
		records.sort(key=lambda item: str(item[1].get("timestamp") or ""), reverse=True)
		if query:
			records = [item for item in records if query in " ".join([
				str(item[1].get("mission") or ""), str(item[1].get("difficulty") or ""),
				str(item[1].get("submitter_id") or ""), str(item[1].get("timestamp") or ""),
				*(str(name) for name in item[1].get("brother_names") or []),
			]).casefold()]
		items = []
		for record_id, record in records[offset:offset + 25]:
			submission = self._web_aar_submission_for_record(record_id, record)
			items.append({
				"id": record_id,
				"mission": str(record.get("mission") or "AAR"),
				"difficulty": str(record.get("difficulty") or ""),
				"timestamp": str(record.get("timestamp") or ""),
				"brother_names": [str(name) for name in record.get("brother_names") or []],
				"receipt_url": str(record.get("receipt_url") or record.get("message_url") or ""),
				"editable": submission is not None,
			})
		pending_deletions = [
			{
				"id": str(item.get("record_id") or ""),
				"status": str(item.get("status") or ""),
				"reason": str(item.get("reason") or ""),
				"request_token": str(item.get("request_token") or ""),
				"requester_id": str(item.get("request_actor_id") or ""),
				"mission": str((all_records.get(str(item.get("record_id"))) or {}).get("mission") or "AAR"),
				"difficulty": str((all_records.get(str(item.get("record_id"))) or {}).get("difficulty") or ""),
				"requested_at": str(item.get("requested_at") or ""),
				"receipt_url": str((all_records.get(str(item.get("record_id"))) or {}).get("receipt_url")
					or (all_records.get(str(item.get("record_id"))) or {}).get("message_url") or item.get("receipt_url") or ""),
			}
			for item in _load_web_aar_mutations()
			if item.get("operation") == "delete"
			and item.get("status") in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"}
		]
		pending_edits = [
			{
				"id": str(item.get("record_id") or ""),
				"request_id": str(item.get("request_id") or ""),
				"status": str(item.get("status") or ""),
				"mission": str((item.get("submission") or {}).get("mission") or "AAR"),
				"receipt_url": str(item.get("receipt_url") or ""),
			}
			for item in _load_web_aar_mutations()
			if item.get("operation") == "edit" and item.get("status") in {"receipt_updated", "archive_saved"}
		]
		reconciliation_reviews = [
			{
				"record_id": str(mutation.get("record_id") or ""),
				"operation": str(mutation.get("operation") or ""),
				"user_id": str(review.get("user_id") or ""),
				"challenge": str(review.get("challenge") or ""),
				"review_id": _reconciliation_review_id(mutation, review),
			}
			for mutation in _load_web_aar_mutations()
			if isinstance(mutation.get("reconciliation"), dict)
			for review in mutation["reconciliation"].get("review_required") or []
			if isinstance(review, dict) and not review.get("acknowledged_at")
		]
		edit_requests = [item for item in _load_web_aar_mutations() if item.get("operation") == "edit_request" and item.get("status") == "pending" and (all_records.get(item.get("record_id")) or {}).get("source") == "strategium_web"]
		result = {
			"records": items,
			"total": len(records),
			"offset": offset,
			"has_more": offset + 25 < len(records),
			"pending_deletions": pending_deletions,
			"pending_edits": pending_edits,
			"edit_requests": [{
				"request_id": item["request_id"], "record_id": item["record_id"],
				"requester_id": item["requester_id"], "requester_name": item.get("requester_name", ""),
				"requested_changes": item["requested_changes"], "requested_at": item["requested_at"],
				"submission": item.get("submission"), "original_submission": item.get("original_submission"),
				"participant_names": {
					str(identity): str(getattr(self._resolve_guild().get_member(int(identity)), "display_name", identity))
					for identity in set((item.get("submission") or {}).get("brother_ids") or []) | set((item.get("original_submission") or {}).get("brother_ids") or [])
					if str(identity).isdigit() and self._resolve_guild() is not None
				},
				"editable": (all_records.get(item["record_id"]) or {}).get("source") == "strategium_web",
				"receipt_url": str((all_records.get(item["record_id"]) or {}).get("receipt_url") or (all_records.get(item["record_id"]) or {}).get("message_url") or ""),
			} for item in edit_requests],
			"edit_request_count": len(edit_requests),
			"reconciliation_reviews": reconciliation_reviews,
		}
		result["queue_counts"] = {kind: sum(len(result[key]) for key in keys) for kind, keys in queue_groups.items()}
		queue = [(key, item) for key in queue_groups[queue_kind] for item in result[key]]
		for key in queue_groups["all"]:
			result[key] = []
		result.update(queue_total=len(queue), queue_offset=queue_offset, queue_next_offset=queue_offset, queue_has_more=False)
		for key, item in queue[queue_offset:queue_offset + 10]:
			result[key].append(item)
			if len(json.dumps(result).encode("utf-8")) > 480 * 1024:
				result[key].pop()
				break
			result["queue_next_offset"] += 1
		result["queue_has_more"] = result["queue_next_offset"] < len(queue)
		return _json_ok(result)

	async def handle_web_aar_member_record(self, req: web.Request) -> web.Response:
		return await self.handle_web_aar_management_detail(req, member_access=True)

	async def handle_web_aar_management_detail(self, req: web.Request, *, member_access: bool = False) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		authorized = await self._authorize_web_aar_management(
			req, "own-record" if member_access else "records", record_id,
			capability=None if member_access else "can_edit",
		)
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		mutations = _load_web_aar_mutations()
		review = None
		if not member_access and payload.get("review_type"):
			review_type = payload.get("review_type")
			review_id = payload.get("review_id")
			operations = {"edit": "edit_request", "delete": "delete", "recovery": "edit", "award": "award"}
			if not isinstance(review_type, str) or review_type not in operations or not isinstance(review_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", review_id):
				return _json_error("invalid_review", "Select a valid AAR review.", 400)
			mutation = next((item for item in mutations if str(item.get("record_id")) == record_id and item.get("operation") == operations[review_type] and review_id in {item.get("request_id"), item.get("request_token")}), None)
			if review_type == "award":
				match = next(((item, entry) for item in mutations if str(item.get("record_id")) == record_id for entry in (item.get("reconciliation") or {}).get("review_required", []) if _reconciliation_review_id(item, entry) == review_id), None)
				if match:
					mutation = {**match[1], "status": "complete" if match[1].get("acknowledged_at") else "pending", "review_id": review_id}
			if mutation is None:
				return _json_error("not_found", "AAR review not found.", 404)
			review = {key: mutation.get(key) for key in ("request_id", "request_token", "status", "reason", "requested_at", "requester_id", "requester_name", "request_actor_id", "submission", "original_submission", "requested_changes", "decline_reason", "editor_note", "challenge", "user_id", "review_id")}
			review.update(kind=review_type, record_id=record_id)
			processing = next((item for item in mutations if review_type == "edit" and item.get("proposal_id") == review_id and item.get("status") in {"pending", "receipt_updated", "archive_saved"}), None)
			if processing:
				review.update(processing_request_id=processing["request_id"], processing_status=processing["status"])
		record = _g.DATASTORE.get_record(record_id)
		if not isinstance(record, dict) or record.get("source") != "strategium_web":
			if record is None and review and (review["kind"] == "award" or review["kind"] == "delete" and review["status"] in {"receipt_deleted", "record_deleted", "reconciling", "complete"}):
				return _json_ok({"id": record_id, "record_removed": True, "review": review, "delete_request": review if review["kind"] == "delete" else None})
			return _json_error("not_found", "AAR not found.", 404)
		if member_access:
			actor, _payload = authorized
			owners = {str(identity) for identity in record.get("brother_ids") or []}
			owners.add(str(record.get("submitter_id") or ""))
			if str(actor.id) not in owners:
				return _json_error("forbidden", "You can edit only website AARs you submitted or participated in.", 403)
		submission = self._web_aar_submission_for_record(record_id, record)
		if submission is None:
			return _json_error("record_unavailable", "This AAR has no editable submission data.", 409)
		delete_request = next((item for item in _load_web_aar_mutations()
			if item.get("request_id") == f"delete-{record_id}" and item.get("status") in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"}), None)
		screenshots = record.get("screenshots") if isinstance(record.get("screenshots"), list) else []
		web_key = str(record.get("web_submission_key") or "")
		guild = self._resolve_guild()
		if web_key and guild is not None:
			from . import aar_ops
			channel = aar_ops._resolve_aar_submission_channel(guild)
			if channel is not None:
				message = await self._find_web_aar_receipt(channel, web_key, record_id)
				if message is not None:
					screenshots = _public_screenshots(_web_aar_receipt_screenshots(message)) or screenshots
		return _json_ok({
			"id": record_id,
			"timestamp": str(record.get("timestamp") or ""),
			"submitter_id": str(record.get("submitter_id") or ""),
			"submitter_name": str(getattr(self._resolve_guild().get_member(int(record["submitter_id"])), "display_name", record["submitter_id"])) if str(record.get("submitter_id") or "").isdigit() and self._resolve_guild() is not None else "",
			**({"review": review} if not member_access else {}),
			"original_record_hash": hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest(),
			"submission": submission,
			"brothers": [
				{"id": str(brother_id), "name": str(name)}
				for brother_id, name in zip(record.get("brother_ids") or [], record.get("brother_names") or [])
			],
			"screenshots": screenshots,
			"receipt_url": str(record.get("receipt_url") or record.get("message_url") or ""),
			"delete_request": {"status": str(delete_request.get("status") or ""), "requested_at": str(delete_request.get("requested_at") or ""), "reason": str(delete_request.get("reason") or ""), "request_token": str(delete_request.get("request_token") or "")} if isinstance(delete_request, dict) else None,
		})

	async def handle_web_aar_reconciliation_acknowledge(self, req: web.Request) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		review_id = req.match_info.get("review_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id) or not re.fullmatch(r"[a-f0-9]{24}", review_id):
			return _json_error("not_found", "Award review item not found.", 404)
		authorized = await self._authorize_web_aar_management(
			req, f"reviews/{review_id}/acknowledge", record_id, capability="can_delete"
		)
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		note = str(payload.get("note") or "").strip()[:400]
		mutations = _load_web_aar_mutations()
		for mutation in mutations:
			if str(mutation.get("record_id") or "") != record_id:
				continue
			reconciliation = mutation.get("reconciliation")
			if not isinstance(reconciliation, dict):
				continue
			for review in reconciliation.get("review_required") or []:
				if not isinstance(review, dict) or _reconciliation_review_id(mutation, review) != review_id:
					continue
				if review.get("acknowledged_at"):
					return _json_ok({"ok": True, "review_id": review_id, "duplicate": True})
				review.update({"acknowledged_at": _iso_now(), "acknowledged_by": str(actor.id), "acknowledgement_note": note})
				_upsert_web_aar_mutation(mutation)
				return _json_ok({"ok": True, "review_id": review_id})
		return _json_error("not_found", "Award review item not found.", 404)

	async def handle_web_aar_management_edit(self, req: web.Request) -> web.Response:
		if req.content_length is None or req.content_length <= 0 or req.content_length > MAX_WEB_AAR_REQUEST_BYTES:
			return _json_error("invalid_size", _web_aar_request_size_error(req.content_length), 413)
		if not await self._reserve_web_aar_upload(req.content_length):
			return _json_error("upload_capacity", "The AAR upload queue is busy. Retry shortly.", 429)
		try:
			async with self.web_aar_lock:
				return await self._handle_web_aar_management_edit_reserved(req)
		finally:
			await self._release_web_aar_upload(req.content_length)

	async def _handle_web_aar_management_edit_reserved(self, req: web.Request) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR management is disabled.", 503)
		secret = _web_aar_shared_secret()
		if not secret or not self.bot.is_ready() or _g.DATASTORE is None:
			return _json_error("not_ready", "AAR management is unavailable.", 503)
		if req.content_length is None or not 0 < req.content_length <= MAX_WEB_AAR_REQUEST_BYTES:
			return _json_error("invalid_size", _web_aar_request_size_error(req.content_length), 413)
		if not (req.headers.get("Content-Type") or "").lower().startswith("multipart/form-data;"):
			return _json_error("multipart_required", "multipart/form-data is required.", 400)
		user_id = req.headers.get("X-Strategium-User-ID", "")
		timestamp = req.headers.get("X-Strategium-AAR-Timestamp", "")
		request_id = req.headers.get("X-AAR-Management-Request-ID", "")
		if not re.fullmatch(r"[0-9]{1,20}", user_id) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_id):
			return _json_error("invalid_request", "Editor identity or request ID is invalid.", 400)
		try:
			if abs(int(_utcnow().timestamp()) - int(timestamp)) > 300:
				return _json_error("expired_request", "Edit authorization has expired.", 401)
		except ValueError:
			return _json_error("invalid_timestamp", "Edit authorization is invalid.", 401)
		try:
			body = await req.read()
		except web.HTTPRequestEntityTooLarge:
			return _json_error("invalid_size", _web_aar_request_size_error(req.content_length), 413)
		body_hash = hashlib.sha256(body).hexdigest()
		key = f"edit-{record_id}-{request_id}"
		signed = f"{timestamp}\n{key}\n{user_id}\n{body_hash}".encode("utf-8")
		expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(req.headers.get("X-Strategium-AAR-Signature", ""), expected):
			return _json_error("unauthorized", "Edit signature is invalid.", 401)
		try:
			submission, screenshots = _parse_web_aar_multipart(body, req.headers.get("Content-Type", ""), allow_kept_screenshots=True)
		except ValueError as error:
			return _json_error("invalid_submission", str(error), 422)
		proposal_id = submission.pop("edit_proposal_id", None)
		proposal_note = submission.pop("edit_proposal_note", None)
		submission_digest = _web_aar_submission_digest(submission, screenshots)
		mutations = _load_web_aar_mutations()
		previous_mutation = next((item for item in mutations if item.get("request_id") == request_id), None)
		if previous_mutation:
			if previous_mutation.get("body_hash") != submission_digest or str(previous_mutation.get("record_id")) != record_id:
				return _json_error("idempotency_conflict", "This edit request ID was already used for different data.", 409)
		try:
			editor = await self._fresh_web_member(int(user_id))
		except discord.HTTPException:
			return _json_error("not_ready", "Role verification is unavailable.", 503)
		if not self._web_aar_member_allowed(editor) or self._web_aar_staff_capabilities(editor).get("can_edit") is not True:
			return _json_error("forbidden", "AAR editor permission is required.", 403)
		if previous_mutation and previous_mutation.get("status") == "complete":
			_finish_web_aar_edit_proposal(previous_mutation)
			return _json_ok({"ok": True, "record_id": record_id, "receipt_url": str(previous_mutation.get("receipt_url") or ""), "duplicate": True})
		old_record = _g.DATASTORE.get_record(record_id)
		if not isinstance(old_record, dict) or old_record.get("source") != "strategium_web":
			return _json_error("not_found", "AAR not found.", 404)
		if proposal_id:
			proposal = next((item for item in mutations if item.get("operation") == "edit_request" and item.get("request_id") == proposal_id and item.get("record_id") == record_id), None)
			if not proposal or proposal.get("status") != "pending" or proposal.get("submission") != submission or screenshots:
				return _json_error("proposal_mismatch", "The pending proposal must match the approved fields and retain its evidence.", 409)
			if proposal.get("original_record_hash") != hashlib.sha256(json.dumps(old_record, sort_keys=True, default=str).encode()).hexdigest():
				return _json_error("stale_proposal", "This AAR changed after the proposal. Decline it and request a fresh edit, or resume its pending archive update.", 409)
			if not isinstance(proposal_note, str) or not 4 <= len(proposal_note.strip()) <= 500:
				return _json_error("invalid_note", "Provide an approval note between 4 and 500 characters.", 422)
		pending_delete = next((item for item in _load_web_aar_mutations()
			if item.get("request_id") == f"delete-{record_id}" and item.get("status") in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"}), None)
		if pending_delete:
			return _json_error("delete_pending", "This AAR is locked while deletion is pending.", 409)
		web_journal = _load_web_aar_submissions()
		web_key = str(old_record.get("web_submission_key") or "")
		entry = web_journal.get(web_key) if web_key else None
		if not isinstance(entry, dict) or str(entry.get("record_id") or "") != record_id:
			web_key, entry = next((key, value) for key, value in web_journal.items()
				if isinstance(value, dict) and str(value.get("record_id") or "") == record_id), ("", None)
		if not web_key or not isinstance(entry, dict):
			return _json_error("record_unavailable", "The AAR receipt journal is unavailable.", 409)
		guild = self._resolve_guild()
		if guild is None:
			return _json_error("not_ready", "The configured guild is unavailable.", 503)
		from . import aar_ops
		channel = aar_ops._resolve_aar_submission_channel(guild)
		if channel is None:
			return _json_error("not_configured", "AAR receipt channel is unavailable.", 503)
		message = await self._find_web_aar_receipt(channel, web_key, record_id)
		if message is None:
			return _json_error("receipt_missing", "The Discord receipt could not be verified.", 409)
		if not screenshots:
			for existing in _web_aar_receipt_screenshots(message):
				attachment = existing["attachment"]
				content_type = str(getattr(attachment, "content_type", "") or "").lower()
				if content_type not in WEB_AAR_IMAGE_TYPES.values():
					content_type = WEB_AAR_IMAGE_TYPES.get(existing["filename"].rsplit(".", 1)[-1].lower(), "")
				if not content_type:
					return _json_error("invalid_existing_evidence", "An existing attachment is not a supported image.", 422)
				try:
					if attachment is not None:
						data = await attachment.read()
					else:
						data = await _download_discord_media(existing["url"], MAX_WEB_AAR_FILE_BYTES)
				except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
					return _json_error("evidence_unavailable", "Existing evidence could not be downloaded for receipt synchronization.", 503)
				except ValueError:
					return _json_error("invalid_existing_evidence", "An existing screenshot could not be retained.", 422)
				if len(data) > MAX_WEB_AAR_FILE_BYTES:
					return _json_error("invalid_existing_evidence", "An existing screenshot exceeds the current upload limit.", 422)
				screenshots.append({"filename": existing["filename"], "content_type": content_type, "data": data})
			if not screenshots or len(screenshots) > MAX_WEB_AAR_FILES or sum(len(item["data"]) for item in screenshots) > MAX_WEB_AAR_TOTAL_BYTES:
				return _json_error("invalid_existing_evidence", "Existing screenshots exceed current archive limits.", 422)
		try:
			candidate, view, participants = self._build_web_aar_record(
				submission, guild, channel, editor, int(record_id), getattr(message, "created_at", _utcnow()),
				require_submitter=False,
			)
		except (ValueError, TypeError, KeyError) as error:
			return _json_error("invalid_submission", str(error), 422)
		candidate["source"] = "strategium_web"
		candidate["web_submission_key"] = web_key
		candidate["web_submission"] = dict(submission)
		if proposal_id and "strike_evidence_hashes" in old_record:
			candidate["strike_evidence_hashes"] = list(old_record["strike_evidence_hashes"])
		candidate["submitter_id"] = old_record.get("submitter_id")
		candidate["timestamp"] = old_record.get("timestamp")
		candidate["edited_at"] = _iso_now()
		candidate["message_url"] = str(message.jump_url)
		candidate["receipt_url"] = str(message.jump_url)
		mutation = {
			"request_id": request_id, "operation": "edit", "record_id": record_id,
			"proposal_id": proposal_id, "proposal_note": proposal_note,
			"actor_id": user_id, "body_hash": submission_digest, "created_at": _iso_now(), "status": "pending",
			"submission": dict(submission),
			"edited_at": candidate["edited_at"],
			"previous_brother_ids": [str(value) for value in old_record.get("brother_ids") or []],
			"new_brother_ids": [str(value) for value in candidate.get("brother_ids") or []],
		}
		_upsert_web_aar_mutation(mutation)
		layout, discord_files = await self._web_aar_layout(view, participants, screenshots, web_key)
		try:
			updated_message = await message.edit(
				view=layout, attachments=discord_files, allowed_mentions=discord.AllowedMentions.none()
			)
		except discord.HTTPException:
			mutation.update({"status": "receipt_failed", "updated_at": _iso_now()})
			_upsert_web_aar_mutation(mutation)
			return _json_error("receipt_update_failed", "Discord could not update this receipt; no archive change was committed.", 503)
		finally:
			for file in discord_files:
				file.close()
		updated_message = updated_message or message
		candidate["screenshots"] = _public_screenshots(_web_aar_receipt_screenshots(updated_message))
		mutation.update({"status": "receipt_updated", "receipt_url": str(updated_message.jump_url), "updated_at": _iso_now()})
		_upsert_web_aar_mutation(mutation)
		try:
			await aar_ops.save_aar_record(candidate)
			flush = getattr(_g.DATASTORE, "flush", None)
			if callable(flush):
				await flush()
		except Exception:
			self.logger.exception("Web AAR edit datastore commit pending record_id=%s request_id=%s", record_id, request_id)
			return _json_ok({"ok": True, "processing_pending": True, "record_id": record_id, "receipt_url": str(updated_message.jump_url)}, status=202)
		try:
			affected_users = {str(value) for value in old_record.get("brother_ids") or []}
			affected_users.update(str(value) for value in candidate.get("brother_ids") or [])
			mutation["reconciliation"] = await aar_ops.rebuild_challenge_progress_for_users(affected_users, guild)
		except Exception:
			self.logger.exception("Web AAR edit challenge reconciliation pending record_id=%s request_id=%s", record_id, request_id)
			mutation.update({"status": "archive_saved", "updated_at": _iso_now()})
			_upsert_web_aar_mutation(mutation)
			return _json_ok({"ok": True, "processing_pending": True, "record_id": record_id, "receipt_url": str(updated_message.jump_url)}, status=202)
		entry.update({"submission": submission, "receipt_url": str(updated_message.jump_url), "status": "complete"})
		web_journal[web_key] = entry
		_save_web_aar_submissions(web_journal)
		mutation.update({"status": "complete", "updated_at": _iso_now()})
		_upsert_web_aar_mutation(mutation)
		_finish_web_aar_edit_proposal(mutation)
		return _json_ok({"ok": True, "record_id": record_id, "receipt_url": str(updated_message.jump_url)})

	async def handle_web_aar_management_edit_resume(self, req: web.Request) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		authorized = await self._authorize_web_aar_management(req, "edit-resume", record_id)
		if isinstance(authorized, web.Response):
			return authorized
		editor, payload = authorized
		request_id = str(payload.get("request_id") or "")
		mutations = _load_web_aar_mutations()
		mutation = next((item for item in mutations if item.get("request_id") == request_id), None)
		if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_id) or not isinstance(mutation, dict):
			return _json_error("edit_request_missing", "Pending edit recovery entry was not found.", 404)
		if str(mutation.get("record_id") or "") != record_id or mutation.get("operation") != "edit":
			return _json_error("edit_request_mismatch", "Pending edit does not match this record.", 409)
		if mutation.get("status") not in {"receipt_updated", "archive_saved"}:
			return _json_error("edit_not_resumable", "This edit has not reached a resumable phase.", 409)
		record = _g.DATASTORE.get_record(record_id)
		if not isinstance(record, dict) or record.get("source") != "strategium_web":
			return _json_error("not_found", "AAR not found.", 404)
		submission = mutation.get("submission")
		if not isinstance(submission, dict):
			return _json_error("edit_data_missing", "The pending edit has no recoverable submission data.", 409)
		web_key = str(record.get("web_submission_key") or "")
		if not web_key:
			return _json_error("receipt_missing", "The AAR receipt key could not be verified.", 409)
		guild = self._resolve_guild()
		if guild is None:
			return _json_error("not_ready", "The configured guild is unavailable.", 503)
		from . import aar_ops
		channel = aar_ops._resolve_aar_submission_channel(guild)
		if channel is None:
			return _json_error("not_configured", "AAR receipt channel is unavailable.", 503)
		message = await self._find_web_aar_receipt(channel, web_key, record_id)
		if message is None:
			return _json_error("receipt_missing", "The updated Discord receipt could not be verified.", 409)
		try:
			candidate, _view, _participants = self._build_web_aar_record(
				submission, guild, channel, editor, int(record_id), getattr(message, "created_at", _utcnow()),
				require_submitter=False,
			)
		except (ValueError, TypeError, KeyError) as error:
			return _json_error("invalid_submission", str(error), 422)
		candidate.update({
			"source": "strategium_web",
			"web_submission_key": web_key,
			"web_submission": dict(submission),
			"submitter_id": record.get("submitter_id"),
			"timestamp": record.get("timestamp"),
			"edited_at": str(mutation.get("edited_at") or _iso_now()),
			"message_url": str(message.jump_url),
			"receipt_url": str(message.jump_url),
			"screenshots": _public_screenshots(_web_aar_receipt_screenshots(message)),
		})
		if mutation.get("proposal_id") and "strike_evidence_hashes" in record:
			candidate["strike_evidence_hashes"] = list(record["strike_evidence_hashes"])
		try:
			await aar_ops.save_aar_record(candidate)
			flush = getattr(_g.DATASTORE, "flush", None)
			if callable(flush):
				await flush()
			participants = {str(value) for value in mutation.get("previous_brother_ids") or []}
			participants.update(str(value) for value in mutation.get("new_brother_ids") or candidate.get("brother_ids") or [])
			mutation["reconciliation"] = await aar_ops.rebuild_challenge_progress_for_users(participants, guild)
		except Exception:
			self.logger.exception("Web AAR edit resume pending record_id=%s request_id=%s", record_id, request_id)
			mutation.update({"status": "archive_saved", "updated_at": _iso_now()})
			_upsert_web_aar_mutation(mutation)
			return _json_ok({"ok": True, "processing_pending": True, "record_id": record_id, "receipt_url": str(message.jump_url)}, status=202)
		web_journal = _load_web_aar_submissions()
		web_entry = web_journal.get(web_key)
		if isinstance(web_entry, dict):
			web_entry.update({"submission": submission, "receipt_url": str(message.jump_url), "status": "complete"})
			web_journal[web_key] = web_entry
			_save_web_aar_submissions(web_journal)
		mutation.update({"status": "complete", "updated_at": _iso_now()})
		_upsert_web_aar_mutation(mutation)
		_finish_web_aar_edit_proposal(mutation)
		return _json_ok({"ok": True, "record_id": record_id, "receipt_url": str(message.jump_url), "resumed": True})

	async def handle_web_aar_member_delete_request(self, req: web.Request) -> web.Response:
		async with self.web_aar_lock:
			return await self.handle_web_aar_delete_request(req, member_access=True)

	async def handle_web_aar_delete_request(self, req: web.Request, *, member_access: bool = False) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		authorized = await self._authorize_web_aar_management(req, "own-delete-request" if member_access else "delete-request", record_id, capability=None if member_access else "can_edit")
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		record = _g.DATASTORE.get_record(record_id)
		if not isinstance(record, dict) or record.get("source") != "strategium_web":
			return _json_error("not_found", "AAR not found.", 404)
		if member_access and str(actor.id) not in {str(value) for value in [record.get("submitter_id"), *(record.get("brother_ids") or [])]}:
			return _json_error("forbidden", "You can request deletion only of website AARs you submitted or participated in.", 403)
		reason = str(payload.get("reason") or "").strip()
		if not 8 <= len(reason) <= 400:
			return _json_error("invalid_reason", "Provide a deletion reason between 8 and 400 characters.", 422)
		request_id = f"delete-{record_id}"
		mutations = _load_web_aar_mutations()
		existing = next((item for item in mutations if item.get("request_id") == request_id), None)
		if existing and existing.get("status") not in {"cancelled"}:
			return _json_ok({"ok": True, "record_id": record_id, "status": existing.get("status"), "reason": existing.get("reason"), "requested_at": existing.get("requested_at"), "request_token": existing.get("request_token")})
		pending = [item for item in mutations if item.get("operation") == "delete" and item.get("status") in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"}]
		if member_access and sum(str(item.get("request_actor_id")) == str(actor.id) for item in pending) >= 10:
			return _json_error("delete_request_limit", "You already have ten pending deletion requests.", 429)
		if len(pending) >= 1000:
			return _json_error("delete_queue_full", "The deletion queue is full; contact a Forgemaster.", 503)
		if existing:
			_upsert_web_aar_mutation({**existing, "request_id": f"delete-{record_id}-closed-{secrets.token_hex(12)}"})
		entry = {
			"request_id": request_id, "operation": "delete", "record_id": record_id,
			"request_token": secrets.token_hex(12),
			"request_actor_id": str(actor.id), "reason": reason, "requested_at": _iso_now(), "status": "requested",
			"web_key": str(record.get("web_submission_key") or ""),
			"receipt_url": str(record.get("receipt_url") or record.get("message_url") or ""),
			"affected_user_ids": [str(value) for value in record.get("brother_ids") or []],
		}
		_upsert_web_aar_mutation(entry)
		return _json_ok({"ok": True, "record_id": record_id, "status": "requested", "reason": reason, "requested_at": entry["requested_at"], "request_token": entry["request_token"]}, status=202)

	async def handle_web_aar_delete_reject(self, req: web.Request) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		async with self.web_aar_lock:
			authorized = await self._authorize_web_aar_management(req, "delete-reject", record_id)
			if isinstance(authorized, web.Response):
				return authorized
			actor, payload = authorized
			note = payload.get("note")
			if not isinstance(note, str) or not 8 <= len(note.strip()) <= 400:
				return _json_error("invalid_note", "Provide a decline reason between 8 and 400 characters.", 422)
			entry = next((item for item in _load_web_aar_mutations() if item.get("request_id") == f"delete-{record_id}" and item.get("operation") == "delete"), None)
			if not entry:
				return _json_error("delete_request_missing", "Deletion request not found.", 404)
			if entry.get("request_token") and payload.get("request_token") != entry["request_token"]:
				return _json_error("stale_deletion_request", "The deletion request changed. Reload it before deciding.", 409)
			if entry.get("status") == "cancelled":
				return _json_ok({"ok": True, "record_id": record_id, "status": "cancelled", "duplicate": True})
			if entry.get("status") != "requested":
				return _json_error("delete_started", "Deletion has started and cannot be declined. Resume it with a Forgemaster.", 409)
			record = _g.DATASTORE.get_record(record_id)
			if not isinstance(record, dict) or record.get("source") != "strategium_web":
				return _json_error("not_found", "AAR not found.", 404)
			entry.update(status="cancelled", declined_by=str(actor.id), declined_at=_iso_now(), decline_reason=note.strip())
			_upsert_web_aar_mutation(entry)
			return _json_ok({"ok": True, "record_id": record_id, "status": "cancelled"})

	async def handle_web_aar_delete_confirm(self, req: web.Request) -> web.Response:
		async with self.web_aar_lock:
			return await self._handle_web_aar_delete_confirm_locked(req)

	async def _handle_web_aar_delete_confirm_locked(self, req: web.Request) -> web.Response:
		record_id = req.match_info.get("record_id", "")
		if not re.fullmatch(r"[0-9]{1,20}", record_id):
			return _json_error("not_found", "AAR not found.", 404)
		authorized = await self._authorize_web_aar_management(req, "delete-confirm", record_id, capability="can_delete")
		if isinstance(authorized, web.Response):
			return authorized
		actor, payload = authorized
		mutations = _load_web_aar_mutations()
		request_id = f"delete-{record_id}"
		entry = next((item for item in mutations if item.get("request_id") == request_id), None)
		if not isinstance(entry, dict) or entry.get("status") not in {"requested", "deleting", "receipt_deleted", "record_deleted", "reconciling"}:
			return _json_error("delete_request_missing", "A pending Forgemaster deletion request is required.", 409)
		if entry.get("request_token") and payload.get("request_token") != entry["request_token"]:
			return _json_error("stale_deletion_request", "The deletion request changed. Reload it before confirming.", 409)
		record = _g.DATASTORE.get_record(record_id)
		if isinstance(record, dict) and record.get("source") != "strategium_web":
			return _json_error("not_found", "AAR not found.", 404)
		if record is None and entry.get("status") not in {"receipt_deleted", "record_deleted", "reconciling"}:
			return _json_error("not_found", "AAR not found.", 404)
		if isinstance(record, dict) and record.get("strike_id"):
			from . import galactic_war_runtime as war
			strike_result = war.get_store().snapshot(_utcnow())["settlements"].get(record["strike_id"])
			if strike_result and not strike_result.get("reversed"):
				return _json_error("strike_credit_active", "Reverse the linked strike credit with an independent reviewer and recorded reason before deleting this AAR.", 409)
		web_journal = _load_web_aar_submissions()
		web_key = str((record or {}).get("web_submission_key") or entry.get("web_key") or "")
		if not web_key and record is not None:
			web_key, _submission_entry = next((key, value) for key, value in web_journal.items()
				if isinstance(value, dict) and str(value.get("record_id") or "") == record_id), ("", None)
		if not web_key:
			return _json_error("receipt_missing", "The AAR receipt journal could not be verified.", 409)
		guild = self._resolve_guild()
		if guild is None:
			return _json_error("not_ready", "The configured guild is unavailable.", 503)
		from . import aar_ops
		channel = aar_ops._resolve_aar_submission_channel(guild)
		if channel is None:
			return _json_error("not_configured", "AAR receipt channel is unavailable.", 503)
		entry.update({"status": entry.get("status") if entry.get("status") in {"receipt_deleted", "record_deleted", "reconciling"} else "deleting", "confirm_actor_id": str(actor.id), "confirmed_at": entry.get("confirmed_at") or _iso_now()})
		_upsert_web_aar_mutation(entry)
		if entry.get("status") not in {"receipt_deleted", "record_deleted", "reconciling"}:
			message = await self._find_web_aar_receipt(channel, web_key, record_id)
			if message is None:
				return _json_error("receipt_missing", "The Discord receipt could not be verified; the archive was not changed.", 409)
			try:
				await message.delete()
			except discord.NotFound:
				pass
			entry["status"] = "receipt_deleted"
			entry["receipt_deleted_at"] = _iso_now()
			_upsert_web_aar_mutation(entry)
		if record is not None:
			delete_record = getattr(_g.DATASTORE, "delete_record_and_processed_id", None)
			if not callable(delete_record):
				return _json_error("not_ready", "The archive cannot safely remove records.", 503)
			try:
				await delete_record(record_id)
				entry.update({"status": "record_deleted", "record_deleted_at": _iso_now()})
				_upsert_web_aar_mutation(entry)
				flush = getattr(_g.DATASTORE, "flush", None)
				if callable(flush):
					await flush()
			except Exception:
				self.logger.exception("Web AAR delete datastore commit pending record_id=%s", record_id)
				return _json_ok({"ok": True, "record_id": record_id, "status": "processing_pending", "resume_status": "record_deleted"}, status=202)
		for key, value in list(web_journal.items()):
			if key == web_key or (isinstance(value, dict) and str(value.get("record_id") or "") == record_id):
				web_journal.pop(key, None)
		_save_web_aar_submissions(web_journal)
		try:
			entry["status"] = "reconciling"
			_upsert_web_aar_mutation(entry)
			entry["reconciliation"] = await aar_ops.rebuild_challenge_progress_for_users(
				set(str(value) for value in entry.get("affected_user_ids") or []), guild
			)
		except Exception:
			self.logger.exception("Web AAR delete challenge reconciliation pending record_id=%s", record_id)
			return _json_ok({"ok": True, "record_id": record_id, "status": "processing_pending", "resume_status": "reconciling"}, status=202)
		entry.update({"status": "complete", "completed_at": _iso_now()})
		_upsert_web_aar_mutation(entry)
		return _json_ok({"ok": True, "record_id": record_id, "status": "deleted"})

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
		*,
		require_submitter: bool = True,
	) -> tuple[dict[str, Any], Any, list[discord.Member]]:
		from types import SimpleNamespace
		from . import aar_ops

		get_record = getattr(_g.DATASTORE, "get_record", None)
		previous = get_record(str(message_id)) if message_id != 1 and callable(get_record) else None
		if isinstance(previous, dict) and previous.get("strike_id") and previous["strike_id"] != submission.get("strike_id"):
			raise ValueError("A linked strike cannot be removed or changed by an AAR edit")
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
		if require_submitter and str(submitter.id) not in brother_ids:
			raise ValueError("The signed-in submitter must be included among the participants")
		inductee_ids = None
		if mode_config.get("include_induction"):
			raw_inductee_ids = submission.get("inductee_ids")
			if not isinstance(raw_inductee_ids, list):
				raise ValueError("Select one or two inductees from the report participants")
			inductee_ids = [str(value) for value in raw_inductee_ids if str(value).isdigit()]
			if len(inductee_ids) != len(raw_inductee_ids) or len(set(inductee_ids)) != len(inductee_ids):
				raise ValueError("Inductee IDs must be unique Discord member IDs")
			if not 1 <= len(inductee_ids) <= 2:
				raise ValueError("Select one or two inductees from the report participants")
			if not set(inductee_ids).issubset(brother_ids):
				raise ValueError("Inductees must be selected report participants")
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
		view.inductee_ids = inductee_ids
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
		strike_id = submission.get("strike_id")
		if strike_id:
			from . import galactic_war_runtime as war
			if not isinstance(strike_id, str) or not re.fullmatch(r"GW-[a-f0-9]{32}", strike_id):
				raise ValueError("Invalid Galactic War strike ID")
			record["strike_id"] = strike_id
			record["strike_success"] = submission.get("strike_success")
			view.strike_id = strike_id
			war.get_store().validate_report(strike_id, record, _utcnow(), check_identity=message_id != 1)
		return record, view, participants

	async def _web_aar_layout(self, view: Any, participants: list[discord.Member], files: list[dict[str, Any]], key: str) -> tuple[discord.ui.LayoutView, list[discord.File]]:
		from . import aar_ops

		mission_label = view.mission or "Siege Operations"
		team_label = ", ".join(member.display_name for member in participants)
		tag_labels = [label for tag, label, _role_id in aar_ops._AAR_SUBMISSION_TAG_OPTIONS if tag in view.tags]
		discord_files = []
		gallery_items = []
		for index, item in enumerate(files, start=1):
			extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[item["content_type"]]
			filename = f"jericho-aar-{key[:8]}-{index:02d}.{extension}"
			discord_files.append(discord.File(io.BytesIO(item["data"]), filename=filename))
			gallery_items.append(discord.MediaGalleryItem(
				media=f"attachment://{filename}", description=f"Mission evidence {index} of {len(files)}"
			))

		lines = [
			"# /// 𝐌𝐢𝐬𝐬𝐢𝐨𝐧 𝐂𝐡𝐫𝐨𝐧𝐢𝐜𝐥𝐞",
			"-# ⌮ Watch Fortress Jericho ⌮",
			"",
			f"**`ᴏᴘᴇʀᴀᴛɪᴏɴ`** {mission_label}",
			f"**`ᴛʜʀᴇᴀᴛ`** {view.difficulty.replace('@', '')}",
			f"**`ᴍɪssɪᴏɴ ʀᴀɴᴋ`** {view.rank}",
			f"**`ʙᴀᴛᴛʟᴇ-ʙʀᴏᴛʜᴇʀs`** {team_label}",
		]
		if getattr(view, "strike_id", ""):
			lines.append(f"**Strike ID** `{view.strike_id}`")
		if view.aar_type == "pvp":
			lines.extend([
				f"**`ᴍᴀᴘ`** {view.pvp_map}",
				f"**`ɢᴀᴍᴇ ᴍᴏᴅᴇ`** {view.pvp_game_mode}",
				f"**`ʀᴇsᴜʟᴛ`** {'Victory' if view.pvp_result == 'W' else 'Defeat'}",
			])
		else:
			lines.append(f"**`ᴀʀᴍᴏʀʏ ᴅᴀᴛᴀ`** {view.armory_data}")
			if view.mode_config.get("include_waves"):
				lines.append(f"**`ᴡᴀᴠᴇs`** {view.waves}")
			if view.mode_config.get("include_kia"):
				lines.append(f"**`ᴋɪᴀ`** {view.kia_count}")
			if view.gene_seed_status == "carried":
				carrier_name = next(
					(member.display_name for member in participants if str(member.id) == str(view.gene_seed_carrier_id)),
					None,
				)
				if carrier_name:
					lines.append(f"**`ɢᴇɴᴇ-sᴇᴇᴅ`** Carried by {carrier_name}")
			elif view.gene_seed_status != "unknown":
				lines.append(f"**`ɢᴇɴᴇ-sᴇᴇᴅ`** {view.gene_seed_status.title()}")
		if tag_labels:
			lines.append(f"**`ʀᴇᴄᴏʀᴅᴇᴅ ᴛᴀɢs`** {', '.join(tag_labels)}")
		lines.append(f"-# STRATEGIUM WEB AAR {key}")

		children = [discord.ui.TextDisplay("\n".join(lines))]
		if gallery_items:
			children.extend([discord.ui.Separator(), discord.ui.MediaGallery(*gallery_items)])
		layout = discord.ui.LayoutView(timeout=None)
		layout.add_item(discord.ui.Container(*children, accent_color=0x4F8A62))
		return layout, discord_files

	async def _find_web_aar_receipt(self, channel: Any, key: str, message_id: Any = None) -> Any:
		marker = f"STRATEGIUM WEB AAR {key}"
		bot_user_id = getattr(getattr(self.bot, "user", None), "id", None)

		def is_receipt(message: Any) -> bool:
			def has_exact_marker(components: Any) -> bool:
				pending = list(components or [])
				while pending:
					component = pending.pop()
					if isinstance(component, dict):
						if component.get("type") == 10 and any(
							line.strip() == f"-# {marker}" for line in str(component.get("content") or "").splitlines()
						):
							return True
						pending.extend(component.get("components") or [])
						continue
					content = getattr(component, "content", None)
					if isinstance(content, str) and any(line.strip() == f"-# {marker}" for line in content.splitlines()):
						return True
					pending.extend(getattr(component, "children", None) or [])
				return False

			def has_legacy_embed_marker(embeds: Any) -> bool:
				return any(
					getattr(getattr(embed, "footer", None), "text", None) == marker
					for embed in embeds or []
				)

			return (
				bot_user_id is not None
				and getattr(getattr(message, "author", None), "id", None) == bot_user_id
				and (
					has_exact_marker(getattr(message, "components", []))
					or has_legacy_embed_marker(getattr(message, "embeds", []))
				)
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
		record["web_submission_key"] = key
		record["web_submission"] = dict(entry["submission"])
		record["message_url"] = str(message.jump_url)
		record["screenshots"] = _public_screenshots(_web_aar_receipt_screenshots(message))
		record["receipt_url"] = str(message.jump_url)
		if record.get("strike_id"):
			record["strike_evidence_hashes"] = list(entry.get("evidence_hashes") or [])
		if entry.get("status") not in {"record_saved", "complete"}:
			from .galactic_war import WarReportPending
			try:
				await aar_ops.save_aar_record(record)
			except WarReportPending:
				entry.update(status="review_pending", record_id=str(message.id))
				journal[key] = entry
				_save_web_aar_submissions(journal)
				raise
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
				if entry["submission"].get("strike_id"):
					from . import galactic_war_runtime as war
					review = war.get_store().snapshot(_utcnow())["reviews"].get(entry["submission"]["strike_id"])
					if review and review["status"] == "pending":
						continue
					if review and review["status"] == "rejected":
						entry.update(status="complete", rejected=True)
						_save_web_aar_submissions(journal)
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
			return _json_error("invalid_size", _web_aar_request_size_error(req.content_length), 413)
		if not await self._reserve_web_aar_upload(req.content_length):
			return _json_error("upload_capacity", "The AAR upload queue is busy. Retry shortly.", 429)
		try:
			return await self._handle_web_aar_submission_reserved(req)
		finally:
			await self._release_web_aar_upload(req.content_length)

	async def _handle_web_aar_submission_reserved(self, req: web.Request) -> web.Response:
		received_at = _utcnow()
		web_cfg = self.config.get("web_submission") or {}
		if not bool(web_cfg.get("enabled", False)):
			return _json_error("not_configured", "Website AAR submissions are disabled.", 503)
		secret = _web_aar_shared_secret()
		if not secret:
			return _json_error("not_configured", "Website AAR intake secret is not configured.", 503)
		if not self.bot.is_ready() or _g.DATASTORE is None:
			return _json_error("not_ready", "AAR archive is not ready.", 503)
		if req.content_length is None or req.content_length <= 0 or req.content_length > MAX_WEB_AAR_REQUEST_BYTES:
			return _json_error("invalid_size", _web_aar_request_size_error(req.content_length), 413)
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
		except web.HTTPRequestEntityTooLarge as error:
			return _json_error("invalid_size", _web_aar_request_size_error(getattr(error, "actual_size", req.content_length)), 413)
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
		if guild is None or not self._web_aar_member_allowed(submitter):
			return _json_error("unauthorized", "Submitter is not a current member of the configured guild.", 403)
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
				if submission.get("strike_id"):
					from . import galactic_war_runtime as war
					try:
						await asyncio.to_thread(war.get_store().reserve_report, submission["strike_id"], key, received_at)
					except ValueError as error:
						return _json_error("strike_already_reported", str(error), 409)
				entry = {"body_hash": submission_hash, "status": "pending", "created_at": received_at.isoformat(), "submitter_id": user_id, "submission": submission, "evidence_hashes": [hashlib.sha256(item["data"]).hexdigest() for item in screenshots]}
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
				layout, discord_files = await self._web_aar_layout(view, participants, screenshots, key)
				try:
					message = await channel.send(
						view=layout,
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

			from .galactic_war import WarReportPending
			try:
				await self._complete_web_aar_entry(key, entry, message, guild, channel, submitter, journal)
			except WarReportPending as error:
				return _json_ok({"ok": True, "review_pending": True, "record_id": str(message.id), "receipt_url": str(message.jump_url), "message": str(error)}, status=202)
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
