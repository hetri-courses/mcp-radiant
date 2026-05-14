"""Parser/writer for Treyarch's `iwmap 4` brush+entity text format.

Round-trips entity blocks structurally; brushes are kept as opaque text so we
never corrupt geometry. Format reference: zm_giant.map and zm_core/*.map.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# KVP line: optionally quoted key, quoted value. `guid`/`layer` use bareword keys.
_KVP_RE = re.compile(r'^\s*(?:"([^"]+)"|(\S+))\s+"((?:[^"\\]|\\.)*)"\s*$')


@dataclass(slots=True)
class Entity:
    guid: str
    layer: str | None
    kvps: dict[str, str] = field(default_factory=dict)
    brushes: list[str] = field(default_factory=list)  # opaque text blocks

    @property
    def classname(self) -> str:
        return self.kvps.get("classname", "")

    @property
    def origin(self) -> tuple[float, float, float] | None:
        v = self.kvps.get("origin")
        if not v:
            return None
        try:
            x, y, z = (float(p) for p in v.split())
            return x, y, z
        except ValueError:
            return None

    @origin.setter
    def origin(self, value: tuple[float, float, float]) -> None:
        x, y, z = value
        self.kvps["origin"] = f"{x:g} {y:g} {z:g}"

    def serialize(self, index: int) -> str:
        out: list[str] = [f"// entity {index}\n", "{\n"]
        out.append(f'guid "{self.guid}"\n')
        if self.layer is not None:
            out.append(f'layer "{self.layer}"\n')
        for key, value in self.kvps.items():
            out.append(f'"{key}" "{value}"\n')
        for i, brush in enumerate(self.brushes):
            # brush text already includes its `// brush N` header and braces;
            # we rewrite the header to the new index but preserve the body.
            body = _strip_brush_header(brush)
            out.append(f"// brush {i}\n")
            out.append(body)
        out.append("}\n")
        return "".join(out)


@dataclass(slots=True)
class MapFile:
    """Top-level container. Header is opaque (preserves layer/prefab decls)."""
    header: str
    entities: list[Entity] = field(default_factory=list)

    def serialize(self) -> str:
        parts = [self.header]
        for i, entity in enumerate(self.entities):
            parts.append(entity.serialize(i))
        return "".join(parts)

    def save(self, path: Path) -> None:
        path.write_text(self.serialize(), encoding="utf-8", newline="")

    @property
    def worldspawn(self) -> Entity | None:
        for entity in self.entities:
            if entity.classname == "worldspawn":
                return entity
        return None


def parse(text: str) -> MapFile:
    lines = text.splitlines(keepends=True)
    header_lines: list[str] = []
    entities: list[Entity] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("// entity"):
            break
        header_lines.append(line)
        i += 1

    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        if not stripped.startswith("// entity"):
            i += 1
            continue
        # Expect `{` on the next non-empty line
        i += 1
        while i < n and lines[i].strip() == "":
            i += 1
        if i >= n or lines[i].strip() != "{":
            raise ValueError(f"expected '{{' after entity comment at line {i + 1}")
        i += 1

        guid = ""
        layer: str | None = None
        kvps: dict[str, str] = {}
        brushes: list[str] = []

        while i < n:
            body = lines[i]
            stripped = body.strip()
            if stripped == "}":
                i += 1
                break
            if stripped.startswith("// brush"):
                brush_text, i = _consume_brush(lines, i)
                brushes.append(brush_text)
                continue
            if stripped == "":
                i += 1
                continue
            key, value = _parse_kvp(body)
            if key == "guid":
                guid = value
            elif key == "layer":
                layer = value
            else:
                kvps[key] = value
            i += 1

        entities.append(Entity(guid=guid, layer=layer, kvps=kvps, brushes=brushes))

    return MapFile(header="".join(header_lines), entities=entities)


def load(path: Path) -> MapFile:
    return parse(path.read_text(encoding="utf-8"))


def new_guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


# --- internals --------------------------------------------------------------


def _parse_kvp(line: str) -> tuple[str, str]:
    match = _KVP_RE.match(line)
    if not match:
        raise ValueError(f"unparseable KVP line: {line!r}")
    quoted_key, bare_key, value = match.groups()
    return (quoted_key or bare_key), value


def _consume_brush(lines: list[str], start: int) -> tuple[str, int]:
    """Capture a brush block as opaque text, including its `// brush N` header
    line and braces. Returns (text, next_index_after_close).

    Tracks brace depth so nested blocks (mesh/curve patches, which open
    their own inner `{...}` after the `mesh`/`curve` keyword) round-trip
    correctly. A plain 6-face brush has depth 1→0 on a single `}`; a
    mesh brush has 1→2→1→0 on inner-open/inner-close/outer-close."""
    out = [lines[start]]  # `// brush N`
    i = start + 1
    while i < len(lines) and lines[i].strip() == "":
        out.append(lines[i])
        i += 1
    if i >= len(lines) or lines[i].strip() != "{":
        raise ValueError(f"expected '{{' after brush header near line {start + 1}")
    out.append(lines[i])
    i += 1
    depth = 1  # we're inside the outer brace
    while i < len(lines):
        line = lines[i]
        out.append(line)
        stripped = line.strip()
        if stripped == "{":
            depth += 1
        elif stripped == "}":
            depth -= 1
            if depth == 0:
                i += 1
                return "".join(out), i
        i += 1
    raise ValueError(f"unterminated brush block starting at line {start + 1}")


def _strip_brush_header(brush_text: str) -> str:
    """Drop the `// brush N` comment line so a fresh one can be written."""
    nl = brush_text.find("\n")
    if nl == -1:
        return brush_text
    first_line = brush_text[:nl].lstrip()
    if first_line.startswith("// brush"):
        return brush_text[nl + 1 :]
    return brush_text
