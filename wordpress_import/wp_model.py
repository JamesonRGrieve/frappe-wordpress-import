# SPDX-License-Identifier: AGPL-3.0-or-later
"""WordPress and Gravity Forms REST payloads as typed records (pure: no Frappe, no network).

Every parser validates the untrusted JSON at the boundary and returns frozen dataclasses, so the
mapping and planning code works on narrow, known types.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")
# Gravity Forms field types this importer understands; everything else is carried as free text.
GF_NAME, GF_EMAIL, GF_PHONE, GF_WEBSITE = "name", "email", "phone", "website"


class PayloadError(ValueError):
	"""A REST payload is missing a field this importer needs."""


def plain_text(rendered: str) -> str:
	"""Rendered WordPress HTML (a title or excerpt) as plain text."""
	return WHITESPACE.sub(" ", html.unescape(TAG.sub(" ", rendered or ""))).strip()


def parse_gmt(value: str | None) -> datetime | None:
	"""A WordPress ``*_gmt`` / Gravity Forms UTC timestamp as an aware UTC datetime."""
	if not value:
		return None
	return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=UTC)


def _require(payload: dict[str, Any], key: str) -> Any:
	if key not in payload:
		raise PayloadError(f"payload has no {key!r}")
	return payload[key]


def _rendered(payload: dict[str, Any], key: str) -> str:
	value = payload.get(key) or {}
	return value.get("rendered", "") if isinstance(value, dict) else str(value)


@dataclass(frozen=True)
class WPContent:
	"""A WordPress page or post."""

	kind: str
	wp_id: int
	slug: str
	title: str
	html: str
	status: str
	published: datetime | None
	modified: datetime | None
	author_id: int
	parent_id: int
	excerpt: str
	seo_title: str
	seo_description: str

	@property
	def is_published(self) -> bool:
		return self.status == "publish"

	@classmethod
	def from_rest(cls, kind: str, payload: dict[str, Any]) -> WPContent:
		seo = payload.get("yoast_head_json") or {}
		return cls(
			kind=kind,
			wp_id=int(_require(payload, "id")),
			slug=str(_require(payload, "slug")),
			title=plain_text(_rendered(payload, "title")),
			html=_rendered(payload, "content"),
			status=str(payload.get("status", "")),
			published=parse_gmt(payload.get("date_gmt")),
			modified=parse_gmt(payload.get("modified_gmt")),
			author_id=int(payload.get("author") or 0),
			parent_id=int(payload.get("parent") or 0),
			excerpt=plain_text(_rendered(payload, "excerpt")),
			seo_title=str(seo.get("title") or ""),
			seo_description=str(seo.get("description") or ""),
		)


@dataclass(frozen=True)
class WPMedia:
	"""A WordPress media library item."""

	wp_id: int
	source_url: str
	title: str
	alt_text: str
	mime_type: str
	modified: datetime | None

	@property
	def file_name(self) -> str:
		return self.source_url.rstrip("/").rsplit("/", 1)[-1]

	@classmethod
	def from_rest(cls, payload: dict[str, Any]) -> WPMedia:
		return cls(
			wp_id=int(_require(payload, "id")),
			source_url=str(_require(payload, "source_url")),
			title=plain_text(_rendered(payload, "title")),
			alt_text=str(payload.get("alt_text") or ""),
			mime_type=str(payload.get("mime_type") or ""),
			modified=parse_gmt(payload.get("modified_gmt")),
		)


@dataclass(frozen=True)
class GFInput:
	"""A sub-input of a compound Gravity Forms field (e.g. a name field's first/last parts)."""

	input_id: str
	label: str


@dataclass(frozen=True)
class GFField:
	field_id: str
	type: str
	label: str
	inputs: tuple[GFInput, ...] = ()


@dataclass(frozen=True)
class GFForm:
	form_id: int
	title: str
	fields: tuple[GFField, ...]

	@classmethod
	def from_rest(cls, payload: dict[str, Any]) -> GFForm:
		fields = tuple(
			GFField(
				field_id=str(_require(f, "id")),
				type=str(f.get("type") or ""),
				label=str(f.get("label") or ""),
				inputs=tuple(
					GFInput(str(i["id"]), str(i.get("label") or "")) for i in (f.get("inputs") or [])
				),
			)
			for f in _require(payload, "fields")
		)
		return cls(form_id=int(_require(payload, "id")), title=str(payload.get("title") or ""), fields=fields)


@dataclass(frozen=True)
class GFEntry:
	"""A Gravity Forms entry: its values keyed by field or input id ("2", "1.3")."""

	entry_id: int
	form_id: int
	status: str
	created: datetime | None
	updated: datetime | None
	values: dict[str, str] = field(default_factory=dict)

	@property
	def is_active(self) -> bool:
		return self.status == "active"

	@classmethod
	def from_rest(cls, payload: dict[str, Any]) -> GFEntry:
		values = {
			key: str(value)
			for key, value in payload.items()
			if re.fullmatch(r"\d+(\.\d+)?", key) and value not in (None, "")
		}
		return cls(
			entry_id=int(_require(payload, "id")),
			form_id=int(_require(payload, "form_id")),
			status=str(payload.get("status") or ""),
			created=parse_gmt(payload.get("date_created")),
			updated=parse_gmt(payload.get("date_updated")),
			values=values,
		)
