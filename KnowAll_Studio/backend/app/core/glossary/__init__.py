"""Glossary term extraction pipeline."""
from app.core.glossary.extractor import (  # noqa: F401
    parse_explanation_bullets,
    extract_aliases_from_title,
    canonicalize,
    strip_term_definition_prefix,
    build_term_candidates,
    merge_candidate,
    sync_glossary_from_db,
    export_glossary_snapshot,
    read_kgb_snapshot,
)
