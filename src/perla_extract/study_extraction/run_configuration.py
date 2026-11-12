"""Bind an extraction run to its source, method, prompts and implementation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .configuration import ExtractionConfig, implementation_sha256, runtime_versions
from .extraction_prompts import prompt_sha256
from .models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    study_schema_prose_sha256,
    study_schema_sha256,
)
from .spans import build_evidence_spans, evidence_spans_sha256


def build_run_configuration(
    config: ExtractionConfig,
    mode: str,
    source_events: list[dict],
    evidence_blocks: list[EvidenceBlock],
) -> dict:
    """Persist a secret-free fingerprint of every scientifically relevant setting."""

    value = asdict(config)
    value.update(
        {
            "pdf": str(config.pdf),
            "supplement": str(config.supplement) if config.supplement else None,
            "output_dir": str(config.output_dir),
            "document_cache_dir": str(config.document_cache_dir),
            "model_cache_dir": str(config.model_cache_dir),
            "effective_mode": mode,
            "resolved_stage_policies": {
                stage: policy.identity()
                for stage, policy in config.stage_policies(mode).items()
            },
            "schema_version": STUDY_SCHEMA_VERSION,
            "schema_sha256": study_schema_sha256(),
            "schema_prose_sha256": study_schema_prose_sha256(),
            "pipeline_implementation_sha256": implementation_sha256(),
            "runtime_versions": runtime_versions(),
            "scientific_settings": config.scientific_settings(),
            "effective_models": config.scientific_settings()["effective_models"],
            "parser_dependencies": next(
                (
                    event.get("dependency_versions", {})
                    for event in source_events
                    if event.get("dependency_versions")
                ),
                {},
            ),
            "parser_implementation_sha256": next(
                (
                    event.get("parser_implementation_sha256")
                    for event in source_events
                    if event.get("parser_implementation_sha256")
                ),
                None,
            ),
            "source_parsing": source_events,
            "prompt_sha256": prompt_sha256(),
            "evidence_spans_sha256": evidence_spans_sha256(
                build_evidence_spans(evidence_blocks)
            ),
            "source_sha256": [
                event.get("source_sha256")
                for event in source_events
                if event.get("source_sha256")
            ],
        }
    )
    identity = {
        name: value[name]
        for name in (
            "scientific_settings",
            "effective_mode",
            "resolved_stage_policies",
            "schema_sha256",
            "schema_prose_sha256",
            "prompt_sha256",
            "evidence_spans_sha256",
            "source_sha256",
            "parser_dependencies",
            "parser_implementation_sha256",
            "pipeline_implementation_sha256",
            "runtime_versions",
        )
    }
    encoded = json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    value["configuration_sha256"] = hashlib.sha256(encoded).hexdigest()
    return value
