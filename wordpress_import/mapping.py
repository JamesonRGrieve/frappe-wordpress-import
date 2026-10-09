# SPDX-License-Identifier: AGPL-3.0-or-later
"""WordPress records → Frappe document fields (pure: no Frappe, no network).

Pages become Web Pages under the target site's route prefix, posts become Blog Posts (blog app), and
Gravity Forms entries become CRM Leads (Frappe CRM). Field maps for leads are derived from each form's
own fields, and whatever a form collects that has no lead field is kept, labelled, in the lead's
``wp_form_message`` so nothing a visitor wrote is dropped.
"""

from __future__ import annotations

import re
from typing import Any

from wordpress_import.wp_model import GF_EMAIL, GF_NAME, GF_PHONE, GF_WEBSITE, GFEntry, GFForm, WPContent

HOME_ROUTE = "home"
META_DESCRIPTION_MAX = 160
UNKNOWN_FIRST_NAME = "Website visitor"
LEAD_FIELDS = ("first_name", "last_name", "email", "mobile_no", "organization", "website")
ORGANIZATION_LABEL = re.compile(r"\b(company|organi[sz]ation|business)\b", re.IGNORECASE)
FIRST_LABEL = re.compile(r"\bfirst\b", re.IGNORECASE)
LAST_LABEL = re.compile(r"\blast\b", re.IGNORECASE)
# Gravity Forms' own sub-input ids for a name field's first and last parts.
GF_FIRST_SUFFIX, GF_LAST_SUFFIX = ".3", ".6"


def truncate_words(text: str, limit: int) -> str:
	"""``text`` cut at a word boundary to at most ``limit`` characters."""
	if len(text) <= limit:
		return text
	cut = text[: limit + 1].rsplit(" ", 1)[0]
	return cut.rstrip(",.;: ")


def page_routes(prefix: str, pages: list[WPContent], front_page_id: int | None) -> dict[int, str]:
	"""Each page's Web Page route: ``<prefix>/<parent slugs>/<slug>``; the front page is ``<prefix>/home``.

	A page whose parent is missing or forms a cycle is routed from its own slug."""
	by_id = {page.wp_id: page for page in pages}
	routes: dict[int, str] = {}
	for page in pages:
		if page.wp_id == front_page_id:
			routes[page.wp_id] = f"{prefix}/{HOME_ROUTE}"
			continue
		slugs, seen, current = [], set(), page
		while current is not None and current.wp_id not in seen:
			seen.add(current.wp_id)
			slugs.append(current.slug)
			current = by_id.get(current.parent_id)
		routes[page.wp_id] = "/".join([prefix, *reversed(slugs)])
	return routes


def _meta(content: WPContent) -> dict[str, str]:
	return {
		"meta_title": content.seo_title or content.title,
		"meta_description": content.seo_description or truncate_words(content.excerpt, META_DESCRIPTION_MAX),
	}


def web_page_fields(page: WPContent, route: str, body_html: str) -> dict[str, Any]:
	return {
		"title": page.title,
		"route": route,
		"published": int(page.is_published),
		"content_type": "HTML",
		"main_section_html": body_html,
		**_meta(page),
	}


def blog_post_fields(post: WPContent, body_html: str, blog_category: str, blogger: str) -> dict[str, Any]:
	return {
		"title": post.title,
		"route": post.slug,
		"published": int(post.is_published),
		"published_on": post.published.date().isoformat() if post.published else None,
		"content_type": "HTML",
		"content_html": body_html,
		"blog_intro": truncate_words(post.excerpt, META_DESCRIPTION_MAX),
		"blog_category": blog_category,
		"blogger": blogger,
		**_meta(post),
	}


def default_field_map(form: GFForm) -> dict[str, str]:
	"""Gravity Forms field/input id → CRM Lead field, from each field's type and label."""
	mapping: dict[str, str] = {}
	for gf_field in form.fields:
		if gf_field.type == GF_NAME:
			for gf_input in gf_field.inputs:
				if FIRST_LABEL.search(gf_input.label) or gf_input.input_id.endswith(GF_FIRST_SUFFIX):
					mapping[gf_input.input_id] = "first_name"
				elif LAST_LABEL.search(gf_input.label) or gf_input.input_id.endswith(GF_LAST_SUFFIX):
					mapping[gf_input.input_id] = "last_name"
		elif gf_field.type == GF_EMAIL:
			mapping[gf_field.field_id] = "email"
		elif gf_field.type == GF_PHONE:
			mapping[gf_field.field_id] = "mobile_no"
		elif gf_field.type == GF_WEBSITE:
			mapping[gf_field.field_id] = "website"
		elif ORGANIZATION_LABEL.search(gf_field.label):
			mapping[gf_field.field_id] = "organization"
	return mapping


def _labels(form: GFForm) -> dict[str, str]:
	labels: dict[str, str] = {}
	for gf_field in form.fields:
		labels[gf_field.field_id] = gf_field.label
		for gf_input in gf_field.inputs:
			labels[gf_input.input_id] = (
				f"{gf_field.label} ({gf_input.label})" if gf_input.label else gf_field.label
			)
	return labels


def _id_order(key: str) -> tuple[int, ...]:
	return tuple(int(part) for part in key.split("."))


def lead_fields(
	entry: GFEntry, form: GFForm, field_map: dict[str, str], lead_source: str | None
) -> dict[str, Any]:
	"""A CRM Lead's fields for one entry. Mapped values fill lead fields (the first value wins when two
	inputs map to one field); every other value is kept, labelled, in ``wp_form_message``."""
	fields: dict[str, Any] = {}
	labels = _labels(form)
	extra: list[str] = []
	for key in sorted(entry.values, key=_id_order):
		value = entry.values[key].strip()
		target = field_map.get(key)
		if target in LEAD_FIELDS and target not in fields:
			fields[target] = value
		else:
			extra.append(f"{labels.get(key, f'Field {key}')}: {value}")
	fields.setdefault("first_name", UNKNOWN_FIRST_NAME)
	message = [f"Form: {form.title}", *extra] if form.title else extra
	fields["wp_form_message"] = "\n".join(message)
	if lead_source:
		fields["source"] = lead_source
	return fields
