# SPDX-License-Identifier: AGPL-3.0-or-later
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document

from wordpress_import.mapping import LEAD_FIELDS


class WordPressSource(Document):
	def validate(self):
		self.site_url = (self.site_url or "").strip().rstrip("/")
		url = urlsplit(self.site_url)
		if url.scheme != "https" or not url.netloc:
			frappe.throw(_("Site URL must be an https:// address."))
		self.route_prefix = (self.route_prefix or "").strip("/")
		if self.import_pages and not self.route_prefix:
			frappe.throw(_("Importing pages needs a Route Prefix."))
		if self.import_posts:
			self.validate_post_targets()
		if self.import_form_entries:
			self.validate_lead_targets()

	def validate_post_targets(self):
		if not frappe.db.table_exists("Blog Post"):
			frappe.throw(_("Importing posts needs the blog app."))
		for doctype, value in (("Blog Category", self.blog_category), ("Blogger", self.blogger)):
			if not value or not frappe.db.exists(doctype, value):
				frappe.throw(_("Importing posts needs an existing {0}.").format(_(doctype)))

	def validate_lead_targets(self):
		if not frappe.db.table_exists("CRM Lead"):
			frappe.throw(_("Importing form entries needs Frappe CRM."))
		if self.lead_source and not frappe.db.exists("CRM Lead Source", self.lead_source):
			frappe.throw(_("CRM Lead Source {0} does not exist.").format(self.lead_source))
		for row in self.form_field_maps:
			if row.lead_field not in LEAD_FIELDS:
				frappe.throw(_("Row {0}: unknown lead field {1}.").format(row.idx, row.lead_field))
