<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# frappe-wordpress-import

Import a WordPress site into Frappe, and keep importing it. Pages, posts, media and Gravity Forms entries
land as native Frappe documents. The import can run every hour forever: what's new is created, what
changed is updated, and what didn't change is skipped.

| WordPress | Frappe |
|---|---|
| Page | Web Page, routed `<route prefix>/<parent slugs>/<slug>`; the front page is `<route prefix>/home` |
| Post | Blog Post (blog app), in the source's Blog Category, by its Blogger |
| Media library item | public File; page and post HTML is rewritten to point at it (every resized copy too) |
| Gravity Forms entry (active) | CRM Lead (Frappe CRM) |

Drafts and other unpublished pages and posts are imported unpublished. Spam and trashed form entries are
never imported.

## How it stays idempotent

Every imported document records which WordPress object it is (`wp_source`, `wp_id`) and a hash of the
fields the import last wrote (`wp_hash`). An import compares hashes, so it never duplicates and never
rewrites an unchanged document. It only writes the fields it maps, so edits made in Frappe to anything else
are kept: a lead's status, owner, notes and deals.

Form entries map to lead fields from each form's own fields: name (first/last), email, phone, website, and
any field labelled company, organization or business. Per-form overrides go in the source's **Form Field
Maps**. Every value without a lead field is kept, labelled, in the lead's **Form Message**, so nothing a
visitor wrote is lost.

## Setup

1. Install: `bench get-app https://github.com/JamesonRGrieve/frappe-wordpress-import`, then
   `bench --site <site> install-app wordpress_import`. Install the blog app to import posts, and Frappe
   CRM to import form entries.
2. In WordPress, create an **Application Password** (Users → Profile) for a user who can read drafts,
   media and Gravity Forms entries. One password serves both APIs: Gravity Forms' REST API v2 uses
   WordPress's own authentication and capability checks.
3. In Frappe, create a **WordPress Source**: site URL (https), username, Application Password, the route
   prefix pages go under, and (for posts) a Blog Category and Blogger.

## Running

- **Hourly**, for every enabled source (`hourly_long` scheduler).
- **Import Now** on the source: queues a background job, and a source already queued isn't queued twice.
- **Command line**: `bench --site <site> wordpress-import [--source NAME ...]` runs in the foreground and
  prints each source's create / update / skip / failed counts.

The last run's time and counts are on the source. An item that fails (a route already taken, a download
error) is logged in the Error Log and counted as failed; the rest of the import carries on. Only one
import of a source runs at a time.

## Development

```sh
uv run --no-project --with pytest --with requests python -I -m pytest   # pure core + REST client
uvx ruff format --check . && uvx ruff check .
bench --site <site> run-tests --app wordpress_import                    # end to end, real HTTP server
```

The pure core (`wp_model`, `mapping`, `plan`, `links`) has no Frappe import and is unit-tested without a
bench. `client` is tested against a scripted HTTP session. `importer` is tested on a real site against a
real HTTP server serving recorded WordPress and Gravity Forms payloads.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
