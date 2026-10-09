# SPDX-License-Identifier: AGPL-3.0-or-later
"""REST payloads shaped like a real WordPress 6 + Gravity Forms 2.10 site."""

from __future__ import annotations

from typing import Any

import pytest

SITE = "https://obsessedmediagroup.ca"


def page_payload(
	wp_id: int, slug: str, title: str, parent: int = 0, status: str = "publish", **extra: Any
) -> dict:
	return {
		"id": wp_id,
		"slug": slug,
		"status": status,
		"parent": parent,
		"author": 1,
		"date_gmt": "2025-03-04T15:06:07",
		"modified_gmt": "2025-06-01T10:00:00",
		"title": {"rendered": title},
		"content": {
			"rendered": f'<p>{title} body <img src="{SITE}/wp-content/uploads/2025/03/hero-1024x683.jpg"></p>'
		},
		"excerpt": {"rendered": f"<p>{title} &amp; more</p>\n"},
		**extra,
	}


@pytest.fixture
def pages() -> list[dict]:
	return [
		page_payload(10, "home", "Home"),
		page_payload(11, "services", "Services"),
		page_payload(12, "video", "Video &#8211; Production", parent=11),
		page_payload(13, "draft-page", "Draft", status="draft"),
	]


@pytest.fixture
def post_payload() -> dict:
	return page_payload(
		40,
		"launch-news",
		"Launch News",
		yoast_head_json={"title": "Launch News | OMG", "description": "We launched."},
	)


@pytest.fixture
def media_payload() -> dict:
	return {
		"id": 70,
		"source_url": f"{SITE}/wp-content/uploads/2025/03/hero.jpg",
		"title": {"rendered": "Hero"},
		"alt_text": "Studio at night",
		"mime_type": "image/jpeg",
		"modified_gmt": "2025-03-04T15:06:07",
	}


@pytest.fixture
def form_payload() -> dict:
	return {
		"id": 1,
		"title": "Contact",
		"fields": [
			{
				"id": 1,
				"type": "name",
				"label": "Name",
				"inputs": [
					{"id": "1.2", "label": "Prefix"},
					{"id": "1.3", "label": "First"},
					{"id": "1.6", "label": "Last"},
				],
			},
			{"id": 2, "type": "email", "label": "Email"},
			{"id": 3, "type": "phone", "label": "Phone"},
			{"id": 4, "type": "text", "label": "Company Name"},
			{"id": 5, "type": "textarea", "label": "How can we help?"},
			{"id": 6, "type": "website", "label": "Website"},
		],
	}


@pytest.fixture
def entry_payload() -> dict:
	return {
		"id": 501,
		"form_id": 1,
		"status": "active",
		"date_created": "2025-07-08 09:10:11",
		"date_updated": "2025-07-08 09:10:11",
		"1.3": "Rachel",
		"1.6": "Lee",
		"2": "rachel@example.com",
		"3": "780-555-0100",
		"4": "Lee Media",
		"5": "We need a video.",
		"6": "",
		"source_url": f"{SITE}/contact",
	}
