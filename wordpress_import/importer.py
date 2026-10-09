# SPDX-License-Identifier: AGPL-3.0-or-later
"""Frappe side of an import (pure mapping and planning live in ``mapping`` / ``plan`` / ``links``).

Every imported document carries ``wp_source`` + ``wp_id`` (which WordPress object it is) and ``wp_hash``
(the fields it was last written with), so a re-run creates what is new, updates what changed and skips
the rest. Order matters: media first (their file URLs replace WordPress URLs in pages and posts), then
pages, posts and form entries. One failing item is logged and counted; it never aborts the import.
Edits made in Frappe to fields the import does not write (a lead's status, owner, notes) are kept.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import now_datetime
from frappe.utils.synchronization import filelock

from wordpress_import import links, mapping, plan
from wordpress_import.client import WordPressClient, WordPressError
from wordpress_import.wp_model import GFEntry, GFForm, PayloadError, WPContent, WPMedia

SOURCE_DOCTYPE = "WordPress Source"
TRACKED_DOCTYPES = ("Web Page", "Blog Post", "File", "CRM Lead")
# One import per source at a time; a scheduled run waits this long for a manual one to finish.
LOCK_TIMEOUT_S = 600
FAILED = "failed"
ITEM_ERRORS = (frappe.ValidationError, WordPressError, PayloadError)


def tracking_fields() -> list[dict[str, Any]]:
	return [
		{
			"fieldname": "wp_source",
			"label": "WordPress Source",
			"fieldtype": "Link",
			"options": SOURCE_DOCTYPE,
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
		},
		{"fieldname": "wp_id", "label": "WordPress ID", "fieldtype": "Data", "read_only": 1, "no_copy": 1},
		{
			"fieldname": "wp_hash",
			"label": "WordPress Import Hash",
			"fieldtype": "Data",
			"hidden": 1,
			"no_copy": 1,
		},
	]


def ensure_custom_fields() -> None:
	"""after_migrate hook: tracking fields on every target doctype installed here (idempotent)."""
	fields: dict[str, list[dict[str, Any]]] = {
		doctype: tracking_fields() for doctype in TRACKED_DOCTYPES if frappe.db.table_exists(doctype)
	}
	if "CRM Lead" in fields:
		fields["CRM Lead"].append(
			{
				"fieldname": "wp_form_message",
				"label": "Form Message",
				"fieldtype": "Small Text",
				"read_only": 1,
				"no_copy": 1,
			}
		)
	create_custom_fields(fields, update=True)


def existing(doctype: str, source: str) -> dict[str, plan.Existing]:
	rows = frappe.get_all(doctype, filters={"wp_source": source}, fields=["name", "wp_id", "wp_hash"])
	return {row.wp_id: plan.Existing(row.name, row.wp_hash or "") for row in rows}


class SourceImport:
	"""One run of one WordPress Source."""

	def __init__(self, source: Any, client: WordPressClient) -> None:
		self.source = source
		self.client = client
		self.summary: dict[str, dict[str, int]] = {}

	def run(self) -> dict[str, dict[str, int]]:
		needs_media = self.source.import_media or self.source.import_pages or self.source.import_posts
		media = [WPMedia.from_rest(item) for item in self.client.media()] if needs_media else []
		if self.source.import_media:
			self.import_media(media)
		url_map = self.media_url_map(media)
		if self.source.import_pages:
			self.import_pages(url_map)
		if self.source.import_posts:
			self.import_posts(url_map)
		if self.source.import_form_entries:
			self.import_form_entries()
		return self.summary

	def apply(
		self, kind: str, doctype: str, steps: list[plan.Step], write: Callable[[plan.Step, dict], None]
	) -> None:
		counts = plan.summarize(steps) | {FAILED: 0}
		for step in steps:
			if step.action is plan.Action.SKIP:
				continue
			tracking = {"wp_source": self.source.name, "wp_id": step.wp_id, "wp_hash": step.content_hash}
			try:
				write(step, tracking)
				frappe.db.commit()
			except ITEM_ERRORS:
				frappe.db.rollback()
				frappe.log_error(
					title=f"WordPress import: {doctype} {step.wp_id}",
					reference_doctype=SOURCE_DOCTYPE,
					reference_name=self.source.name,
				)
				counts[step.action.value] -= 1
				counts[FAILED] += 1
		self.summary[kind] = counts

	def write_document(self, doctype: str) -> Callable[[plan.Step, dict], None]:
		def write(step: plan.Step, tracking: dict) -> None:
			if step.action is plan.Action.CREATE:
				frappe.get_doc({"doctype": doctype, **step.fields, **tracking}).insert(
					ignore_permissions=True
				)
			else:
				doc = frappe.get_doc(doctype, step.name)
				doc.update({**step.fields, **tracking})
				doc.save(ignore_permissions=True)

		return write

	def media_url_map(self, media: list[WPMedia]) -> dict[str, str]:
		"""WordPress upload URL → the Frappe file imported for it, for every upload imported so far."""
		by_id = {str(item.wp_id): item for item in media}
		files = frappe.get_all("File", filters={"wp_source": self.source.name}, fields=["wp_id", "file_url"])
		return {by_id[row.wp_id].source_url: row.file_url for row in files if row.wp_id in by_id}

	def import_media(self, media: list[WPMedia]) -> None:
		by_id = {str(item.wp_id): item for item in media}
		desired = [
			plan.Desired(
				str(item.wp_id),
				{
					"source_url": item.source_url,
					"file_name": item.file_name,
					"alt_text": item.alt_text,
					"modified": item.modified.isoformat() if item.modified else None,
				},
			)
			for item in media
		]

		def write(step: plan.Step, tracking: dict) -> None:
			content = self.client.download(by_id[step.wp_id].source_url)
			if step.action is plan.Action.UPDATE:
				# A changed upload is a new file: Frappe file content is not edited in place.
				frappe.delete_doc("File", step.name, ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": step.fields["file_name"],
					"is_private": 0,
					"content": content,
					**tracking,
				}
			).insert(ignore_permissions=True)

		self.apply("media", "File", plan.plan(existing("File", self.source.name), desired), write)

	def import_pages(self, url_map: dict[str, str]) -> None:
		pages = [WPContent.from_rest("page", item) for item in self.client.pages()]
		front_page = self.source.front_page_id or self.client.front_page_id()
		routes = mapping.page_routes(self.source.route_prefix, pages, front_page)
		desired = [
			plan.Desired(
				str(page.wp_id),
				mapping.web_page_fields(
					page, routes[page.wp_id], links.rewrite_media_urls(page.html, url_map)
				),
			)
			for page in pages
		]
		self.apply(
			"pages",
			"Web Page",
			plan.plan(existing("Web Page", self.source.name), desired),
			self.write_document("Web Page"),
		)

	def import_posts(self, url_map: dict[str, str]) -> None:
		posts = [WPContent.from_rest("post", item) for item in self.client.posts()]
		desired = [
			plan.Desired(
				str(post.wp_id),
				mapping.blog_post_fields(
					post,
					links.rewrite_media_urls(post.html, url_map),
					self.source.blog_category,
					self.source.blogger,
				),
			)
			for post in posts
		]
		self.apply(
			"posts",
			"Blog Post",
			plan.plan(existing("Blog Post", self.source.name), desired),
			self.write_document("Blog Post"),
		)

	def field_map(self, form: GFForm) -> dict[str, str]:
		overrides = {
			row.gf_input: row.lead_field for row in self.source.form_field_maps if row.form_id == form.form_id
		}
		return mapping.default_field_map(form) | overrides

	def import_form_entries(self) -> None:
		desired: list[plan.Desired] = []
		for form in (GFForm.from_rest(item) for item in self.client.forms()):
			field_map = self.field_map(form)
			for entry in (GFEntry.from_rest(item) for item in self.client.active_entries(form.form_id)):
				fields = mapping.lead_fields(entry, form, field_map, self.source.lead_source or None)
				desired.append(plan.Desired(f"gf-{entry.entry_id}", fields))
		self.apply(
			"form_entries",
			"CRM Lead",
			plan.plan(existing("CRM Lead", self.source.name), desired),
			self.write_document("CRM Lead"),
		)


def import_source(source_name: str) -> dict[str, dict[str, int]]:
	"""Import one WordPress Source now, holding its lock; records the result on the source."""
	source = frappe.get_doc(SOURCE_DOCTYPE, source_name)
	client = WordPressClient(source.site_url, source.username, source.get_password("application_password"))
	with filelock(f"wordpress_import_{frappe.scrub(source_name)}", timeout=LOCK_TIMEOUT_S):
		summary = SourceImport(source, client).run()
		frappe.db.set_value(
			SOURCE_DOCTYPE,
			source_name,
			{"last_synced_at": now_datetime(), "last_result": json.dumps(summary, sort_keys=True)},
		)
		frappe.db.commit()
	return summary


def import_enabled_sources() -> None:
	"""Scheduler hook: import every enabled source; one source's failure never blocks the others."""
	for name in frappe.get_all(SOURCE_DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			import_source(name)
		except WordPressError, PayloadError, frappe.ValidationError:
			frappe.db.rollback()
			frappe.log_error(
				title=f"WordPress import: source {name}",
				reference_doctype=SOURCE_DOCTYPE,
				reference_name=name,
			)
