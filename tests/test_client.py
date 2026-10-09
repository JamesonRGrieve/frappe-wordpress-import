# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the REST client against a scripted HTTP session (pagination, paging, errors, caps)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests

from wordpress_import import client as client_module
from wordpress_import.client import WordPressClient, WordPressError

SITE = "https://wp.example"


class FakeResponse:
	def __init__(
		self, status: int = 200, body: Any = None, headers: dict | None = None, content: bytes = b""
	):
		self.status_code = status
		self._body = body
		self.headers = headers or {}
		self._content = content

	def json(self) -> Any:
		return self._body

	def iter_content(self, size: int):
		for start in range(0, len(self._content), size):
			yield self._content[start : start + size]

	def __enter__(self):
		return self

	def __exit__(self, *_exc):
		return False


class FakeSession:
	"""Answers GETs from a route → handler table and records every call."""

	def __init__(self, routes: dict[str, Any]):
		self.routes = routes
		self.calls: list[tuple[str, Any, Any]] = []

	def get(self, url: str, params=None, auth=None, timeout=None, stream=False):
		assert timeout is not None, "every request must carry a timeout"
		self.calls.append((url, params, auth))
		route = urlsplit(url).path.removeprefix("/wp-json")
		handler = self.routes.get(route) or self.routes.get(url)
		if handler is None:
			return FakeResponse(404)
		if isinstance(handler, Exception):
			raise handler
		return handler(params) if callable(handler) else handler


def paged(items: list, page_size: int):
	def handler(params):
		page = dict(params)["page"]
		start = (page - 1) * page_size
		total_pages = max(1, -(-len(items) // page_size))
		return FakeResponse(
			body=items[start : start + page_size], headers={"X-WP-TotalPages": str(total_pages)}
		)

	return handler


def make(routes: dict[str, Any]) -> tuple[WordPressClient, FakeSession]:
	session = FakeSession(routes)
	return WordPressClient(SITE + "/", "importer", "app pass", session=session), session


def test_wp_collection_follows_every_page(monkeypatch):
	monkeypatch.setattr(client_module, "WP_PAGE_SIZE", 2)
	items = [{"id": i} for i in range(5)]
	client, session = make({"/wp/v2/pages": paged(items, 2)})
	assert client.pages() == items
	assert [dict(params)["page"] for _url, params, _auth in session.calls] == [1, 2, 3]
	assert all(dict(params)["status"] == client_module.CONTENT_STATUSES for _u, params, _a in session.calls)


def test_auth_and_trailing_slash(monkeypatch):
	client, session = make({"/wp/v2/media": paged([], 100)})
	assert client.media() == []
	url, _params, auth = session.calls[0]
	assert url == f"{SITE}/wp-json/wp/v2/media" and auth == ("importer", "app pass")


def test_front_page():
	client, _ = make({"/wp/v2/settings": FakeResponse(body={"show_on_front": "page", "page_on_front": 10})})
	assert client.front_page_id() == 10
	client, _ = make({"/wp/v2/settings": FakeResponse(body={"show_on_front": "posts", "page_on_front": 10})})
	assert client.front_page_id() is None


def test_forms_are_fetched_with_fields_in_id_order():
	client, _ = make(
		{
			"/gf/v2/forms": FakeResponse(body={"3": {"id": "3"}, "1": {"id": "1"}}),
			"/gf/v2/forms/1": FakeResponse(body={"id": 1, "fields": []}),
			"/gf/v2/forms/3": FakeResponse(body={"id": 3, "fields": []}),
		}
	)
	assert [form["id"] for form in client.forms()] == [1, 3]


def test_active_entries_follow_paging_and_ask_for_active_only(monkeypatch):
	monkeypatch.setattr(client_module, "GF_PAGE_SIZE", 2)
	entries = [{"id": i} for i in range(3)]

	def handler(params):
		args = dict(params)
		assert args["form_ids[]"] == 7 and json.loads(args["search"]) == {"status": "active"}
		page = args["paging[current_page]"]
		return FakeResponse(body={"total_count": 3, "entries": entries[(page - 1) * 2 : page * 2]})

	client, session = make({"/gf/v2/entries": handler})
	assert client.active_entries(7) == entries
	assert len(session.calls) == 2


def test_http_errors_and_transport_failures_raise_wordpress_error():
	client, _ = make({"/wp/v2/pages": FakeResponse(500)})
	with pytest.raises(WordPressError, match="HTTP 500"):
		client.pages()
	client, _ = make({"/wp/v2/pages": requests.Timeout()})
	with pytest.raises(WordPressError, match="Timeout"):
		client.pages()


def test_download_and_size_cap(monkeypatch):
	url = f"{SITE}/wp-content/uploads/a.jpg"
	client, _ = make({url: FakeResponse(content=b"x" * 10)})
	assert client.download(url) == b"x" * 10
	monkeypatch.setattr(client_module, "MEDIA_MAX_BYTES", 5)
	with pytest.raises(WordPressError, match="larger than"):
		client.download(url)
	client, _ = make({url: FakeResponse(404)})
	with pytest.raises(WordPressError, match="HTTP 404"):
		client.download(url)
