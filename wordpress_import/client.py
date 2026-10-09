# SPDX-License-Identifier: AGPL-3.0-or-later
"""WordPress and Gravity Forms REST client (no Frappe).

One WordPress Application Password authenticates both APIs: Gravity Forms' REST v2 endpoints check
ordinary WordPress capabilities. Every request is bounded by a timeout, and media downloads by a size cap.
"""

from __future__ import annotations

import json
from typing import Any

import requests

REQUEST_TIMEOUT_S = 30
WP_PAGE_SIZE = 100
GF_PAGE_SIZE = 100
MEDIA_MAX_BYTES = 50 * 1024 * 1024
# Pages and posts in every state the importer mirrors (drafts are imported unpublished).
CONTENT_STATUSES = "publish,draft,private,future,pending"
DOWNLOAD_CHUNK_BYTES = 64 * 1024


class WordPressError(RuntimeError):
	"""A WordPress or Gravity Forms request failed (status, timeout or oversized media)."""


class WordPressClient:
	def __init__(
		self,
		site_url: str,
		username: str,
		password: str,
		session: requests.Session | None = None,
		timeout: float = REQUEST_TIMEOUT_S,
	) -> None:
		self.site_url = site_url.rstrip("/")
		self._auth = (username, password)
		self._session = session or requests.Session()
		self._timeout = timeout

	def _get(
		self, route: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None
	) -> requests.Response:
		url = f"{self.site_url}/wp-json{route}"
		try:
			response = self._session.get(url, params=params, auth=self._auth, timeout=self._timeout)
		except requests.RequestException as e:
			raise WordPressError(f"GET {route}: {type(e).__name__}") from e
		if response.status_code >= 400:
			raise WordPressError(f"GET {route}: HTTP {response.status_code}")
		return response

	def wp_collection(self, route: str, **params: Any) -> list[dict[str, Any]]:
		"""Every item of a WordPress REST collection, following its pagination."""
		items: list[dict[str, Any]] = []
		page = 1
		while True:
			response = self._get(route, {**params, "per_page": WP_PAGE_SIZE, "page": page})
			items.extend(response.json())
			total_pages = int(response.headers.get("X-WP-TotalPages", "1") or 1)
			if page >= total_pages:
				return items
			page += 1

	def pages(self) -> list[dict[str, Any]]:
		return self.wp_collection("/wp/v2/pages", status=CONTENT_STATUSES)

	def posts(self) -> list[dict[str, Any]]:
		return self.wp_collection("/wp/v2/posts", status=CONTENT_STATUSES)

	def media(self) -> list[dict[str, Any]]:
		return self.wp_collection("/wp/v2/media")

	def front_page_id(self) -> int | None:
		"""The page WordPress shows at its root, when it shows a page rather than the latest posts."""
		settings = self._get("/wp/v2/settings").json()
		if settings.get("show_on_front") == "page" and settings.get("page_on_front"):
			return int(settings["page_on_front"])
		return None

	def forms(self) -> list[dict[str, Any]]:
		"""Every Gravity Forms form, with its fields."""
		summaries = self._get("/gf/v2/forms").json()
		form_ids = sorted(int(form_id) for form_id in summaries)
		return [self._get(f"/gf/v2/forms/{form_id}").json() for form_id in form_ids]

	def active_entries(self, form_id: int) -> list[dict[str, Any]]:
		"""Every active (not spam, not trashed) entry of one form, following its paging."""
		entries: list[dict[str, Any]] = []
		page = 1
		while True:
			params = [
				("form_ids[]", form_id),
				("search", json.dumps({"status": "active"})),
				("paging[page_size]", GF_PAGE_SIZE),
				("paging[current_page]", page),
			]
			body = self._get("/gf/v2/entries", params).json()
			batch = body.get("entries") or []
			entries.extend(batch)
			if len(entries) >= int(body.get("total_count") or 0) or not batch:
				return entries
			page += 1

	def download(self, url: str) -> bytes:
		"""A media file's bytes, refusing anything over ``MEDIA_MAX_BYTES``."""
		try:
			with self._session.get(url, auth=self._auth, timeout=self._timeout, stream=True) as response:
				if response.status_code >= 400:
					raise WordPressError(f"download: HTTP {response.status_code}")
				chunks, size = [], 0
				for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
					size += len(chunk)
					if size > MEDIA_MAX_BYTES:
						raise WordPressError(f"download: larger than {MEDIA_MAX_BYTES} bytes")
					chunks.append(chunk)
				return b"".join(chunks)
		except requests.RequestException as e:
			raise WordPressError(f"download: {type(e).__name__}") from e
