# frappe-wordpress-import — Agent Operating Guide

AGPL-3.0-or-later Frappe v16 app (`wordpress_import`). It imports a WordPress site into Frappe,
idempotently and continuously. See README.md for the user-facing contract.

## Layout

- `wp_model.py`: REST payloads → frozen dataclasses (validated at the boundary). No Frappe.
- `mapping.py`: records → Frappe document fields (Web Page, Blog Post, CRM Lead), page routes, default
  Gravity Forms field maps. No Frappe.
- `plan.py`: upsert planning by content hash (create / update / skip). No Frappe.
- `links.py`: rewrite WordPress upload URLs (every `-WxH` size, either scheme) to imported Files. No Frappe.
- `client.py`: WordPress + Gravity Forms REST client (timeouts, pagination, media size cap). No Frappe.
- `importer.py`: the Frappe side. Tracking custom fields (`wp_source`, `wp_id`, `wp_hash`; plus
  `wp_form_message` on CRM Lead), the per-source run (media → pages → posts → form entries), a per-source
  filelock, and the scheduler entry point.
- `api.py` (Import Now: enqueue, deduplicated), `commands.py` (`bench wordpress-import`), `hooks.py`.
- Doctypes: **WordPress Source** (connection, targets, what to import, overrides, last result) and its
  child **WordPress Form Field Map**.

## Rules

- The import writes only the fields it maps; never overwrite fields a person edits in Frappe (lead status,
  owner, notes). A change in WordPress updates only mapped fields.
- Idempotent by construction: the identity is (`wp_source`, `wp_id`), the change signal is `wp_hash`.
  Never key on titles, slugs or routes.
- One failing item is logged and counted, never fatal to the run. Transport errors are `WordPressError`.
- Media changes create a new File and delete the old one; pages that referenced it update via their hash.
- Credentials: the Application Password lives in a Password field, never in logs, errors or the summary.

## Testing

Every change adds or extends tests. Logic belongs in the pure modules, tested without a bench:
`uv run --no-project --with pytest --with requests python -I -m pytest`. The end-to-end importer tests run
on a bench (`bench --site <site> run-tests --app wordpress_import`) against a real HTTP server serving
recorded payloads. Lint: `uvx ruff format --check . && uvx ruff check .`
