# SPDX-License-Identifier: AGPL-3.0-or-later
"""The importer end to end against a real test site and a real HTTP server serving recorded WordPress 6
and Gravity Forms 2.10 REST payloads (`bench --site <site> run-tests --app wordpress_import`).

Covers the contract: a first import creates, a re-run changes nothing, a changed page updates, trashed
or spam entries never import, and edits made in Frappe to fields the import does not write survive."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import frappe
from frappe.tests.utils import FrappeTestCase

from wordpress_import.importer import ensure_custom_fields, import_source

SOURCE = "_Test WordPress Source"
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")


class FakeWordPress:
	"""Recorded REST payloads, editable per test."""

	def __init__(self, base: str):
		self.base = base
		self.pages = [
			{
				"id": 10,
				"slug": "home",
				"status": "publish",
				"parent": 0,
				"title": {"rendered": "Home"},
				"content": {"rendered": f'<img src="{base}/wp-content/uploads/hero-300x200.png">'},
				"excerpt": {"rendered": "Welcome"},
				"date_gmt": "2025-01-01T00:00:00",
				"modified_gmt": "2025-01-01T00:00:00",
			},
			{
				"id": 11,
				"slug": "about",
				"status": "publish",
				"parent": 0,
				"title": {"rendered": "About"},
				"content": {"rendered": "<p>About us</p>"},
				"excerpt": {"rendered": "About"},
				"date_gmt": "2025-01-01T00:00:00",
				"modified_gmt": "2025-01-01T00:00:00",
			},
		]
		self.media = [
			{
				"id": 70,
				"source_url": f"{base}/wp-content/uploads/hero.png",
				"title": {"rendered": "Hero"},
				"alt_text": "",
				"mime_type": "image/png",
				"modified_gmt": "2025-01-01T00:00:00",
			}
		]
		self.form = {
			"id": 1,
			"title": "Contact",
			"fields": [
				{
					"id": 1,
					"type": "name",
					"label": "Name",
					"inputs": [{"id": "1.3", "label": "First"}, {"id": "1.6", "label": "Last"}],
				},
				{"id": 2, "type": "email", "label": "Email"},
				{"id": 5, "type": "textarea", "label": "Message"},
			],
		}
		self.entries = [
			{
				"id": 501,
				"form_id": 1,
				"status": "active",
				"1.3": "Rachel",
				"1.6": "Lee",
				"2": "rachel@example.test",
				"5": "Hi",
			},
			{"id": 502, "form_id": 1, "status": "spam", "1.3": "Spam", "2": "spam@example.test"},
		]

	def route(self, path: str, query: dict) -> tuple[int, object, dict]:
		if path == "/wp-json/wp/v2/pages":
			return 200, self.pages, {"X-WP-TotalPages": "1"}
		if path in ("/wp-json/wp/v2/posts",):
			return 200, [], {"X-WP-TotalPages": "1"}
		if path == "/wp-json/wp/v2/media":
			return 200, self.media, {"X-WP-TotalPages": "1"}
		if path == "/wp-json/wp/v2/settings":
			return 200, {"show_on_front": "page", "page_on_front": 10}, {}
		if path == "/wp-json/gf/v2/forms":
			return 200, {"1": {"id": "1"}}, {}
		if path == "/wp-json/gf/v2/forms/1":
			return 200, self.form, {}
		if path == "/wp-json/gf/v2/entries":
			status = json.loads(query["search"][0])["status"]
			matching = [e for e in self.entries if e["status"] == status]
			return 200, {"total_count": len(matching), "entries": matching}, {}
		return 404, {}, {}


def serve(fake_holder: dict) -> ThreadingHTTPServer:
	class Handler(BaseHTTPRequestHandler):
		def do_GET(self):
			url = urlsplit(self.path)
			if url.path.startswith("/wp-content/uploads/"):
				self.send_response(200)
				self.end_headers()
				self.wfile.write(PIXEL)
				return
			status, body, headers = fake_holder["wp"].route(url.path, parse_qs(url.query))
			payload = json.dumps(body).encode()
			self.send_response(status)
			for key, value in headers.items():
				self.send_header(key, value)
			self.send_header("Content-Type", "application/json")
			self.end_headers()
			self.wfile.write(payload)

		def log_message(self, *_args):
			pass

	server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	return server


@unittest.skipUnless(frappe.db.table_exists("CRM Lead"), "Frappe CRM not installed")
class TestWordPressImport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.holder = {}
		cls.server = serve(cls.holder)
		cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_custom_fields()
		self.holder["wp"] = FakeWordPress(self.base)
		if frappe.db.exists("WordPress Source", SOURCE):
			frappe.delete_doc("WordPress Source", SOURCE, force=True)
		source = frappe.get_doc(
			{
				"doctype": "WordPress Source",
				"source_name": SOURCE,
				"site_url": self.base,
				"username": "importer",
				"application_password": "test",
				"route_prefix": "omg-test",
				"import_posts": 0,
			}
		)
		# The fake serves plain http on loopback; production validation requires https.
		source.flags.ignore_validate = True
		source.insert()

	def tearDown(self):
		frappe.db.rollback()

	def imported(self, doctype):
		return frappe.get_all(
			doctype, filters={"wp_source": SOURCE}, fields=["name", "wp_id"], order_by="wp_id"
		)

	def test_first_import_creates_and_rewrites_media(self):
		summary = import_source(SOURCE)
		self.assertEqual(summary["media"]["create"], 1)
		self.assertEqual(summary["pages"]["create"], 2)
		self.assertEqual(summary["form_entries"]["create"], 1)
		home = frappe.get_doc("Web Page", {"wp_source": SOURCE, "wp_id": "10"})
		self.assertEqual(home.route, "omg-test/home")
		file_url = frappe.db.get_value("File", {"wp_source": SOURCE, "wp_id": "70"}, "file_url")
		self.assertIn(file_url, home.main_section_html)
		self.assertNotIn("/wp-content/uploads/", home.main_section_html)

	def test_rerun_changes_nothing(self):
		import_source(SOURCE)
		summary = import_source(SOURCE)
		for kind in ("media", "pages", "form_entries"):
			self.assertEqual(summary[kind]["create"] + summary[kind]["update"], 0, kind)
		self.assertEqual(len(self.imported("Web Page")), 2)
		self.assertEqual(len(self.imported("CRM Lead")), 1)

	def test_changed_page_updates_in_place(self):
		import_source(SOURCE)
		name = frappe.db.get_value("Web Page", {"wp_source": SOURCE, "wp_id": "11"})
		self.holder["wp"].pages[1]["content"]["rendered"] = "<p>About us, revised</p>"
		summary = import_source(SOURCE)
		self.assertEqual(summary["pages"]["update"], 1)
		self.assertIn("revised", frappe.db.get_value("Web Page", name, "main_section_html"))

	def test_spam_is_never_imported_and_lead_has_the_form_message(self):
		import_source(SOURCE)
		leads = self.imported("CRM Lead")
		self.assertEqual([lead.wp_id for lead in leads], ["gf-501"])
		lead = frappe.get_doc("CRM Lead", leads[0].name)
		self.assertEqual(
			(lead.first_name, lead.last_name, lead.email), ("Rachel", "Lee", "rachel@example.test")
		)
		self.assertEqual(lead.wp_form_message, "Form: Contact\nMessage: Hi")

	def test_crm_edits_survive_a_reimport(self):
		import_source(SOURCE)
		name = self.imported("CRM Lead")[0].name
		statuses = frappe.get_all("CRM Lead Status", pluck="name")
		new_status = next(s for s in statuses if s != frappe.db.get_value("CRM Lead", name, "status"))
		frappe.db.set_value("CRM Lead", name, "status", new_status)
		import_source(SOURCE)
		self.assertEqual(frappe.db.get_value("CRM Lead", name, "status"), new_status)

	def test_result_is_recorded_on_the_source(self):
		import_source(SOURCE)
		source = frappe.get_doc("WordPress Source", SOURCE)
		self.assertTrue(source.last_synced_at)
		self.assertEqual(json.loads(source.last_result)["pages"]["create"], 2)


class TestWordPressSourceValidation(FrappeTestCase):
	def test_requires_https_and_a_route_prefix_for_pages(self):
		doc = frappe.get_doc(
			{
				"doctype": "WordPress Source",
				"source_name": "_Test Invalid",
				"site_url": "http://x.test",
				"username": "u",
				"application_password": "p",
				"import_posts": 0,
				"import_form_entries": 0,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()
		doc.site_url = "https://x.test/"
		with self.assertRaises(frappe.ValidationError):
			doc.validate()
		doc.route_prefix = "/x-test/"
		doc.validate()
		self.assertEqual((doc.site_url, doc.route_prefix), ("https://x.test", "x-test"))
