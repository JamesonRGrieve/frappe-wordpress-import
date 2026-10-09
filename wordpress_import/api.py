# SPDX-License-Identifier: AGPL-3.0-or-later
"""The desk's "Import Now" button: queues one import of a source as a background job."""

import frappe

IMPORT_TIMEOUT_S = 3600


@frappe.whitelist(methods=["POST"])
def import_now(source: str) -> str:
	"""Queue an import of ``source`` (System Manager only). A source already queued is not queued twice."""
	frappe.only_for("System Manager")
	if not frappe.db.exists("WordPress Source", source):
		raise frappe.DoesNotExistError
	frappe.enqueue(
		"wordpress_import.importer.import_source",
		source_name=source,
		queue="long",
		timeout=IMPORT_TIMEOUT_S,
		job_id=f"wordpress_import::{source}",
		deduplicate=True,
	)
	return "queued"
