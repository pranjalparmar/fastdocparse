"""Schema definitions for document extraction."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator


class Field(BaseModel):
    """Defines a single field to extract from a document."""
    name: str
    description: str
    type: str = "text"  # "text", "number", "date", "currency", "list"
    required: bool = False
    pattern: str | None = None  # regex the extracted string value must fully match, e.g. HS/container codes
    enum: list[str] | None = None  # allowed values, e.g. shipment status, incoterms
    sub_fields: list[Field] | None = None

    @property
    def is_numeric(self) -> bool:
        """True for field types that should be grounded/merged by parsed numeric value
        rather than exact string match (see grounding.check_substring's numeric= flag)."""
        return self.type in ("number", "currency")

    @property
    def is_date(self) -> bool:
        """True for date fields — grounded by parsed calendar date rather than exact
        string match (see grounding.check_substring's date= flag), since a normalized
        "2021-04-22" won't literally appear in a source document that says "22Apr2021"."""
        return self.type == "date"


class Schema(BaseModel):
    """Defines a complete document schema."""
    name: str = "DocumentSchema"
    fields: list[Field]
    examples: list[tuple[str, dict[str, Any]]] | None = None

    @field_validator('fields')
    @classmethod
    def validate_field_names(cls, v):
        if any(f.name == "_meta" for f in v):
            raise ValueError(
                "'_meta' is a reserved field name (used for truncation metadata in the "
                "extraction result) and cannot be used as a schema field name."
            )
        return v

    @field_validator('examples')
    @classmethod
    def validate_examples(cls, v):
        if v is not None:
            for ex in v:
                if not isinstance(ex, tuple) or len(ex) != 2:
                    raise ValueError("Each example must be a tuple of (document_snippet, expected_json)")
                if not isinstance(ex[0], str) or not isinstance(ex[1], dict):
                    raise TypeError("Each example must be a tuple of (str, dict)")
        return v

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schema:
        """Build a Schema from a plain dict (as loaded from JSON/YAML) — no Python code needed."""
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, path: str | Path) -> Schema:
        """Load a Schema from a .json file."""
        with open(path, "r") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_yaml(cls, path: str | Path) -> Schema:
        """Load a Schema from a .yaml/.yml file."""
        import yaml  # optional dependency; only needed for this path

        with open(path, "r") as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def from_file(cls, path: str | Path) -> Schema:
        """Load a Schema from a .json or .yaml/.yml file, dispatching on extension."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            return cls.from_yaml(path)
        if suffix == ".json":
            return cls.from_json(path)
        raise ValueError(f"Unsupported schema file extension {suffix!r} (expected .json, .yaml, or .yml)")

    def get_field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None
