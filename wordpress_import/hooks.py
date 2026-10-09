# SPDX-License-Identifier: AGPL-3.0-or-later
from . import __version__ as _version

app_name = "wordpress_import"
app_title = "WordPress Import"
app_publisher = "Zephyrex Technologies Limited"
app_description = "Import a WordPress site into Frappe, idempotently and continuously: pages, posts, media and Gravity Forms entries"
app_email = "jameson@zephyrex.ca"
app_license = "AGPL-3.0-or-later"
app_version = _version

required_apps = ["frappe"]

# Tracking fields (wp_source, wp_id, wp_hash) on every target doctype installed on the site.
after_migrate = ["wordpress_import.importer.ensure_custom_fields"]

# Re-import every enabled source hourly: new and changed content lands, unchanged content is skipped.
scheduler_events = {"hourly_long": ["wordpress_import.importer.import_enabled_sources"]}
