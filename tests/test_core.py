# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the pure import core: parsing REST payloads, mapping to Frappe fields, planning
idempotent upserts and rewriting media URLs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.conftest import SITE
from wordpress_import import links, mapping, plan
from wordpress_import.wp_model import GFEntry, GFForm, PayloadError, WPContent, WPMedia, parse_gmt, plain_text


class TestModel:
	def test_plain_text_strips_tags_and_entities(self):
		assert plain_text("<p>Video &#8211; Production &amp; more</p>\n") == "Video – Production & more"

	def test_parse_gmt_is_utc_and_accepts_both_separators(self):
		assert parse_gmt("2025-07-08 09:10:11") == datetime(2025, 7, 8, 9, 10, 11, tzinfo=UTC)
		assert parse_gmt("2025-07-08T09:10:11") == datetime(2025, 7, 8, 9, 10, 11, tzinfo=UTC)
		assert parse_gmt(None) is None and parse_gmt("") is None

	def test_content(self, pages, post_payload):
		page = WPContent.from_rest("page", pages[2])
		assert (page.wp_id, page.slug, page.title, page.parent_id) == (12, "video", "Video – Production", 11)
		assert page.is_published and page.excerpt == "Video – Production & more"
		post = WPContent.from_rest("post", post_payload)
		assert (post.seo_title, post.seo_description) == ("Launch News | OMG", "We launched.")
		assert not WPContent.from_rest("page", pages[3]).is_published

	def test_missing_required_field_is_a_payload_error(self):
		with pytest.raises(PayloadError):
			WPContent.from_rest("page", {"slug": "x"})

	def test_media(self, media_payload):
		media = WPMedia.from_rest(media_payload)
		assert (media.wp_id, media.file_name, media.alt_text) == (70, "hero.jpg", "Studio at night")

	def test_form_and_entry(self, form_payload, entry_payload):
		form = GFForm.from_rest(form_payload)
		assert [f.field_id for f in form.fields] == ["1", "2", "3", "4", "5", "6"]
		assert [i.input_id for i in form.fields[0].inputs] == ["1.2", "1.3", "1.6"]
		entry = GFEntry.from_rest(entry_payload)
		assert entry.is_active and entry.form_id == 1
		# blanks and non-field keys (source_url, status, …) are not values
		assert entry.values == {
			"1.3": "Rachel",
			"1.6": "Lee",
			"2": "rachel@example.com",
			"3": "780-555-0100",
			"4": "Lee Media",
			"5": "We need a video.",
		}


class TestRoutes:
	def contents(self, payloads):
		return [WPContent.from_rest("page", p) for p in payloads]

	def test_hierarchy_and_front_page(self, pages):
		routes = mapping.page_routes("omg-ca", self.contents(pages), front_page_id=10)
		assert routes == {
			10: "omg-ca/home",
			11: "omg-ca/services",
			12: "omg-ca/services/video",
			13: "omg-ca/draft-page",
		}

	def test_missing_parent_and_cycles_fall_back_to_own_slug(self):
		from tests.conftest import page_payload

		orphan = page_payload(20, "orphan", "Orphan", parent=999)
		a, b = page_payload(21, "a", "A", parent=22), page_payload(22, "b", "B", parent=21)
		routes = mapping.page_routes("p", self.contents([orphan, a, b]), front_page_id=None)
		assert routes[20] == "p/orphan"
		assert routes[21] == "p/b/a" and routes[22] == "p/a/b"


class TestFields:
	def test_web_page(self, pages):
		page = WPContent.from_rest("page", pages[1])
		fields = mapping.web_page_fields(page, "omg-ca/services", "<p>x</p>")
		assert fields["route"] == "omg-ca/services" and fields["published"] == 1
		assert fields["content_type"] == "HTML" and fields["main_section_html"] == "<p>x</p>"
		assert fields["meta_title"] == "Services" and fields["meta_description"] == "Services & more"

	def test_blog_post_prefers_seo_meta(self, post_payload):
		post = WPContent.from_rest("post", post_payload)
		fields = mapping.blog_post_fields(post, "<p>x</p>", "omg-news", "omg")
		assert fields["route"] == "launch-news" and fields["published_on"] == "2025-03-04"
		assert (fields["blog_category"], fields["blogger"]) == ("omg-news", "omg")
		assert (fields["meta_title"], fields["meta_description"]) == ("Launch News | OMG", "We launched.")

	def test_truncate_words(self):
		assert mapping.truncate_words("short", 10) == "short"
		assert mapping.truncate_words("one two three four", 10) == "one two"
		assert len(mapping.truncate_words("word " * 100, 160)) <= 160


class TestLeads:
	def test_default_field_map(self, form_payload):
		field_map = mapping.default_field_map(GFForm.from_rest(form_payload))
		assert field_map == {
			"1.3": "first_name",
			"1.6": "last_name",
			"2": "email",
			"3": "mobile_no",
			"4": "organization",
			"6": "website",
		}

	def test_lead_fields_keep_unmapped_values_labelled(self, form_payload, entry_payload):
		form, entry = GFForm.from_rest(form_payload), GFEntry.from_rest(entry_payload)
		fields = mapping.lead_fields(entry, form, mapping.default_field_map(form), "Website")
		assert fields["first_name"] == "Rachel" and fields["last_name"] == "Lee"
		assert fields["email"] == "rachel@example.com" and fields["mobile_no"] == "780-555-0100"
		assert fields["organization"] == "Lee Media" and fields["source"] == "Website"
		assert fields["wp_form_message"] == "Form: Contact\nHow can we help?: We need a video."

	def test_entry_without_a_name_still_makes_a_lead(self, form_payload):
		form = GFForm.from_rest(form_payload)
		entry = GFEntry.from_rest({"id": 1, "form_id": 1, "status": "active", "2": "a@b.ca"})
		fields = mapping.lead_fields(entry, form, mapping.default_field_map(form), None)
		assert fields["first_name"] == mapping.UNKNOWN_FIRST_NAME and "source" not in fields

	def test_first_mapped_value_wins(self, form_payload):
		form = GFForm.from_rest(form_payload)
		entry = GFEntry.from_rest({"id": 2, "form_id": 1, "status": "active", "2": "a@b.ca", "7": "c@d.ca"})
		fields = mapping.lead_fields(entry, form, {"2": "email", "7": "email"}, None)
		assert fields["email"] == "a@b.ca"
		assert "Field 7: c@d.ca" in fields["wp_form_message"]


class TestPlan:
	def test_create_update_skip(self):
		same = {"title": "Same"}
		existing = {"1": plan.Existing("WP-1", plan.content_hash(same)), "2": plan.Existing("WP-2", "stale")}
		steps = plan.plan(
			existing,
			[
				plan.Desired("1", same),
				plan.Desired("2", {"title": "New"}),
				plan.Desired("3", {"title": "Fresh"}),
			],
		)
		assert [(s.action, s.wp_id, s.name) for s in steps] == [
			(plan.Action.SKIP, "1", "WP-1"),
			(plan.Action.UPDATE, "2", "WP-2"),
			(plan.Action.CREATE, "3", None),
		]
		assert plan.summarize(steps) == {"create": 1, "update": 1, "skip": 1}

	def test_hash_ignores_key_order_and_is_stable(self):
		assert plan.content_hash({"a": 1, "b": "x"}) == plan.content_hash({"b": "x", "a": 1})
		assert plan.content_hash({"a": 1}) != plan.content_hash({"a": 2})

	def test_a_rerun_with_no_changes_is_all_skips(self):
		desired = [plan.Desired(str(i), {"n": i}) for i in range(5)]
		first = plan.plan({}, desired)
		existing = {s.wp_id: plan.Existing(f"D-{s.wp_id}", s.content_hash) for s in first}
		assert plan.summarize(plan.plan(existing, desired)) == {"create": 0, "update": 0, "skip": 5}

	def test_duplicate_wp_id_keeps_the_last(self):
		steps = plan.plan({}, [plan.Desired("1", {"v": 1}), plan.Desired("1", {"v": 2})])
		assert len(steps) == 1 and steps[0].fields == {"v": 2}


class TestLinks:
	URL_MAP = {f"{SITE}/wp-content/uploads/2025/03/hero.jpg": "/files/hero.jpg"}

	def test_rewrites_every_size_and_scheme(self):
		body = (
			f'<img src="{SITE}/wp-content/uploads/2025/03/hero-1024x683.jpg" '
			f'srcset="http://obsessedmediagroup.ca/wp-content/uploads/2025/03/hero.jpg 2048w">'
		)
		assert (
			links.rewrite_media_urls(body, self.URL_MAP)
			== '<img src="/files/hero.jpg" srcset="/files/hero.jpg 2048w">'
		)

	def test_leaves_other_uploads_and_lookalikes(self):
		body = f'<img src="{SITE}/wp-content/uploads/2025/03/hero-banner.jpg"><a href="{SITE}/hero.jpg">'
		assert links.rewrite_media_urls(body, self.URL_MAP) == body

	def test_empty_inputs(self):
		assert links.rewrite_media_urls("", self.URL_MAP) == ""
		assert links.rewrite_media_urls("<p>x</p>", {}) == "<p>x</p>"

	def test_target_is_inserted_verbatim(self):
		url_map = {f"{SITE}/wp-content/uploads/a.png": r"/files/a\1.png"}
		assert links.rewrite_media_urls(f"{SITE}/wp-content/uploads/a.png", url_map) == r"/files/a\1.png"
