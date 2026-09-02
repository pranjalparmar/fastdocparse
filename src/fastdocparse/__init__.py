"""fastdocparse — extract structured data from semi-structured documents using any
OpenAI-compatible LLM, with per-field grounding and confidence.

    from fastdocparse import Schema, Field, LLMClient, DocumentParser

    schema = Schema(name="Invoice", fields=[Field(name="total", description="Grand total", type="number")])
    client = LLMClient(model="gpt-4o-mini", api_key="sk-...")
    result = DocumentParser(client).extract(document_bytes, schema)
"""

from .cache import Cache, InMemoryCache
from .config import ExtractionConfig
from .grounding import (
    Issue,
    check_substring,
    cross_check,
    date_parseable_rule,
    numeric_sum_rule,
    validate_field_constraints,
)
from .llm_client import LLMClient, LLMClientError
from .parser import (
    DocumentParser,
    EmptyDocumentError,
    UnknownIngestionKindError,
    register_default_ingestion_handler,
)
from .result import ExtractionMeta, ExtractionResult, FieldResult
from .schema import Field, Schema
from .schema_compiler import compile_schema_from_description

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    # Looks up by the PyPI *distribution* name (pyproject.toml's [project] name) — keep
    # this string in sync with that if the distribution is ever renamed again.
    __version__ = _pkg_version("fastdocparse")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "Cache",
    "DocumentParser",
    "EmptyDocumentError",
    "ExtractionConfig",
    "ExtractionMeta",
    "ExtractionResult",
    "Field",
    "FieldResult",
    "InMemoryCache",
    "Issue",
    "LLMClient",
    "LLMClientError",
    "Schema",
    "UnknownIngestionKindError",
    "check_substring",
    "compile_schema_from_description",
    "cross_check",
    "date_parseable_rule",
    "numeric_sum_rule",
    "register_default_ingestion_handler",
    "validate_field_constraints",
]
