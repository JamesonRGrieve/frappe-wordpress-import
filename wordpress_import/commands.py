# SPDX-License-Identifier: AGPL-3.0-or-later
"""`bench --site <site> wordpress-import [--source NAME ...]`: import WordPress Sources now, in the foreground.

With no --source, every enabled source is imported. Prints each source's create/update/skip/failed counts.
"""

import json

import click
from frappe.commands import get_site, pass_context


@click.command("wordpress-import")
@click.option("--source", "sources", multiple=True, help="WordPress Source to import (repeatable).")
@pass_context
def wordpress_import(context, sources):
	"""Import WordPress Sources now."""
	import frappe

	from wordpress_import.importer import SOURCE_DOCTYPE, import_source

	frappe.init(site=get_site(context))
	frappe.connect()
	try:
		names = list(sources) or frappe.get_all(SOURCE_DOCTYPE, filters={"enabled": 1}, pluck="name")
		for name in names:
			click.echo(f"{name}: {json.dumps(import_source(name), sort_keys=True)}")
	finally:
		frappe.destroy()


commands = [wordpress_import]
