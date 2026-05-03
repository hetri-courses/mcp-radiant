"""Generic entity CRUD on a MapFile. Identifiers are GUIDs (stable across
load/save); indices are only used for display/listing."""

from __future__ import annotations

import math
from typing import Iterable

from .mapfile import Entity, MapFile, new_guid


def list_entities(
    mf: MapFile,
    *,
    classname: str | None = None,
    layer: str | None = None,
) -> list[tuple[int, Entity]]:
    out: list[tuple[int, Entity]] = []
    for i, entity in enumerate(mf.entities):
        if classname is not None and entity.classname != classname:
            continue
        if layer is not None and entity.layer != layer:
            continue
        out.append((i, entity))
    return out


def find_by_guid(mf: MapFile, guid: str) -> Entity | None:
    for entity in mf.entities:
        if entity.guid == guid:
            return entity
    return None


def find_by_targetname(mf: MapFile, name: str) -> list[Entity]:
    return [e for e in mf.entities if e.kvps.get("targetname") == name]


def find_by_kvp(mf: MapFile, key: str, value: str) -> list[Entity]:
    return [e for e in mf.entities if e.kvps.get(key) == value]


def find_near(
    mf: MapFile,
    origin: tuple[float, float, float],
    max_distance: float,
    *,
    classname: str | None = None,
) -> list[tuple[float, Entity]]:
    ox, oy, oz = origin
    out: list[tuple[float, Entity]] = []
    for entity in mf.entities:
        if classname is not None and entity.classname != classname:
            continue
        eo = entity.origin
        if eo is None:
            continue
        ex, ey, ez = eo
        dist = math.sqrt((ex - ox) ** 2 + (ey - oy) ** 2 + (ez - oz) ** 2)
        if dist <= max_distance:
            out.append((dist, entity))
    out.sort(key=lambda pair: pair[0])
    return out


def add_entity(
    mf: MapFile,
    classname: str,
    *,
    origin: tuple[float, float, float] | None = None,
    angles: tuple[float, float, float] | None = None,
    layer: str | None = None,
    kvps: dict[str, str] | None = None,
) -> Entity:
    """Append a new entity. KVPs are written in this order: classname, angles,
    origin, then any extras from `kvps` in insertion order."""
    full: dict[str, str] = {"classname": classname}
    if angles is not None:
        ax, ay, az = angles
        full["angles"] = f"{ax:g} {ay:g} {az:g}"
    if origin is not None:
        ox, oy, oz = origin
        full["origin"] = f"{ox:g} {oy:g} {oz:g}"
    if kvps:
        for key, value in kvps.items():
            if key in ("classname", "guid", "layer"):
                continue
            full[key] = str(value)
    entity = Entity(guid=new_guid(), layer=layer, kvps=full, brushes=[])
    mf.entities.append(entity)
    return entity


def update_entity(entity: Entity, kvps: dict[str, str]) -> Entity:
    """Patch KVPs in place. None values delete the key."""
    for key, value in kvps.items():
        if key in ("guid", "layer"):
            raise ValueError(f"use the dedicated field, not kvps, for {key}")
        if value is None:
            entity.kvps.pop(key, None)
        else:
            entity.kvps[key] = str(value)
    return entity


def move_entity(entity: Entity, origin: tuple[float, float, float]) -> Entity:
    entity.origin = origin
    return entity


def delete_entity(mf: MapFile, guid: str) -> bool:
    for i, entity in enumerate(mf.entities):
        if entity.guid == guid:
            del mf.entities[i]
            return True
    return False


def summarize(entity: Entity, index: int | None = None) -> dict:
    """Compact dict for tool responses."""
    out: dict = {
        "guid": entity.guid,
        "classname": entity.classname,
        "origin": entity.origin,
        "layer": entity.layer,
    }
    if index is not None:
        out["index"] = index
    if "targetname" in entity.kvps:
        out["targetname"] = entity.kvps["targetname"]
    if "model" in entity.kvps:
        out["model"] = entity.kvps["model"]
    if "script_noteworthy" in entity.kvps:
        out["script_noteworthy"] = entity.kvps["script_noteworthy"]
    return out


def summarize_many(entities: Iterable[tuple[int, Entity]]) -> list[dict]:
    return [summarize(e, i) for i, e in entities]
