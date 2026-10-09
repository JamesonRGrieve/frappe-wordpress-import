# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rewrite WordPress media URLs in imported HTML to the Frappe files that replace them (pure).

WordPress serves resized copies of an upload as ``name-<w>x<h>.ext`` (in ``src`` and ``srcset``); none
of them is a separate media item, so every size of an upload is pointed at its one imported file. The
scheme is ignored, so ``http://`` and ``https://`` references both match.
"""

from __future__ import annotations

import re

SIZE_SUFFIX = r"(?:-\d+x\d+)?"


def _pattern(source_url: str) -> str:
	without_scheme = re.sub(r"^https?:", "", source_url)
	stem, dot, extension = without_scheme.rpartition(".")
	if not dot:
		return r"https?:" + re.escape(without_scheme)
	return r"https?:" + re.escape(stem) + SIZE_SUFFIX + re.escape(dot + extension)


def rewrite_media_urls(body_html: str, url_map: dict[str, str]) -> str:
	"""``body_html`` with every reference to a mapped WordPress upload (any size) replaced by its file URL."""
	if not url_map or not body_html:
		return body_html
	compiled = [(re.compile(_pattern(source)), target) for source, target in url_map.items()]
	for pattern, target in compiled:
		body_html = pattern.sub(lambda _match, target=target: target, body_html)
	return body_html
