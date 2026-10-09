# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idempotent upsert planning (pure: no Frappe).

Every imported document records the WordPress id it came from and a hash of the fields it was last
written with. Re-running an import compares hashes: a new id is created, a changed one is updated, an
unchanged one is skipped, so an import can run on a schedule forever without duplicating anything.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Action(StrEnum):
	CREATE = "create"
	UPDATE = "update"
	SKIP = "skip"


@dataclass(frozen=True)
class Existing:
	"""An already-imported document: its Frappe name and the hash it was last written with."""

	name: str
	content_hash: str


@dataclass(frozen=True)
class Desired:
	wp_id: str
	fields: dict[str, Any]


@dataclass(frozen=True)
class Step:
	action: Action
	wp_id: str
	fields: dict[str, Any]
	content_hash: str
	name: str | None = None


def content_hash(fields: dict[str, Any]) -> str:
	"""A stable hash of a document's fields (key order and value types normalised)."""
	canonical = json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))
	return hashlib.sha256(canonical.encode()).hexdigest()


def plan(existing: dict[str, Existing], desired: list[Desired]) -> list[Step]:
	"""One step per desired document, in input order. A wp id listed twice keeps its last fields."""
	latest: dict[str, Desired] = {item.wp_id: item for item in desired}
	steps = []
	for wp_id, item in latest.items():
		digest = content_hash(item.fields)
		current = existing.get(wp_id)
		if current is None:
			steps.append(Step(Action.CREATE, wp_id, item.fields, digest))
		elif current.content_hash != digest:
			steps.append(Step(Action.UPDATE, wp_id, item.fields, digest, current.name))
		else:
			steps.append(Step(Action.SKIP, wp_id, item.fields, digest, current.name))
	return steps


def summarize(steps: list[Step]) -> dict[str, int]:
	counts = {action.value: 0 for action in Action}
	for step in steps:
		counts[step.action.value] += 1
	return counts
