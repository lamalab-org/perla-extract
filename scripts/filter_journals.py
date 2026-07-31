"""Filter the journal whitelist with an LLM, one call per journal.

``allowed_journals.csv`` is a Scopus CiteScore export and therefore contains many
journals from fields (oncology, clinical medicine, economics, ...) that would
never publish an experimental study on perovskite solar cells. This module asks
an LLM, journal by journal, whether the journal could plausibly publish such a
paper and writes a reduced whitelist.

This is a one-off maintenance tool rather than part of the pipeline, so it lives
outside the installed package. It reads the whitelist from the ``perla_extract``
package and writes its outputs next to this file.

The decision for every journal is cached in ``journal_decisions.jsonl``, so a run
can be interrupted and resumed without paying for the same calls twice, and
re-filtering at a different ``--min_confidence`` costs no API calls at all.

Usage::

    python scripts/filter_journals.py run
    python scripts/filter_journals.py run --min_confidence 0.8
    python scripts/filter_journals.py run --model_name gpt-4o-2024-08-06 --max_workers 16

The filtered whitelist is written to ``allowed_journals_filtered.csv``; copy it
over ``src/perla_extract/allowed_journals.csv`` once the removals look right.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.resources import files
from pathlib import Path
from typing import Optional

import fire
import instructor
import pandas as pd
from litellm import completion
from loguru import logger
from pydantic import BaseModel, Field

SYSTEM_PROMPT = """You are a scientific librarian with an encyclopedic knowledge of the scope of academic journals.

You are cleaning up a whitelist of journals for a literature-mining pipeline on perovskite solar cells.
For a single journal, decide whether it could plausibly publish an *experimental* research article on
perovskite solar cells, i.e. a paper that fabricates single-junction perovskite photovoltaic devices and
reports their photovoltaic performance (J-V curves, power conversion efficiency, stability).

Answer False only if the journal would essentially never publish such a paper, for example because:
- it covers an unrelated field (medicine, oncology, clinical science, psychology, economics, law, ecology, astronomy, ...),
- it is a pure review journal that does not publish primary research,
- it is restricted to theory, simulation or computation and does not publish device fabrication,
- its scope excludes photovoltaics even though it is a physical-science journal (e.g. a journal
  dedicated to catalysis, batteries, sensors, or ceramics processing only).

Answer True if the journal is a general-science, general-chemistry, general-physics, materials,
energy, nanoscience, optoelectronics or photovoltaics journal that has published or could publish such work.
Broad multidisciplinary journals (Nature, Science, Nature Communications, Advanced Materials, ...) are True.

If you are genuinely unsure about the journal's scope, answer True and say so in the reason.
Being too strict is worse than being too permissive: a wrongly removed journal silently loses papers,
while a wrongly kept journal is caught by later filters."""

USER_PROMPT = """Journal: {title}
Publisher: {publisher}
Scopus subject area: {subject}
CiteScore: {citescore}

Could this journal publish an experimental research article on perovskite solar cells?"""


class JournalVerdict(BaseModel):
    """Decision of the LLM for a single journal."""

    scope: str = Field(
        description="One sentence describing what this journal publishes."
    )
    reason: str = Field(
        description="One or two sentences justifying the decision, referring to the scope."
    )
    could_publish_perovskite_experiment: bool = Field(
        description=(
            "True if the journal could publish an experimental paper on perovskite "
            "solar cells, False if it essentially never would."
        )
    )
    confidence: float = Field(
        description="Confidence in the decision, between 0 (guess) and 1 (certain).",
        ge=0.0,
        le=1.0,
    )


_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _get_client():
    """Return a shared instructor client.

    ``instructor.from_litellm`` registers its mode handlers lazily, which is not
    thread safe; building the client once behind a lock avoids a ``RegistryError``
    when several worker threads start at the same time.
    """
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = instructor.from_litellm(completion)
    return _CLIENT


# Models that reject temperature != 1, learned at runtime. ``get_supported_openai_params``
# still advertises ``temperature`` for these, so the only reliable probe is the call itself.
_FIXED_TEMPERATURE: dict[str, float] = {}


def _default_whitelist() -> Path:
    return Path(str(files("perla_extract").joinpath("allowed_journals.csv")))


def _subject_area(highest_percentile: object) -> str:
    """Extract the subject area from the Scopus ``Highest percentile`` field.

    The field looks like ``"99.0%\\n1/415\\nOncology"``; the subject is the last line.
    """
    if not isinstance(highest_percentile, str):
        return "unknown"
    lines = [line.strip() for line in highest_percentile.splitlines() if line.strip()]
    return lines[-1] if lines else "unknown"


def _format_value(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "unknown"
    return str(value)


def classify_journal(
    title: str,
    publisher: object = None,
    subject: object = None,
    citescore: object = None,
    model_name: str = "claude-sonnet-5",
    temperature: float = 0.0,
    max_retries: int = 3,
) -> JournalVerdict:
    """Ask the LLM whether a single journal could publish perovskite solar cell experiments.

    Args:
        title: Journal name as it appears in the whitelist.
        publisher: Publisher name, used as extra context.
        subject: Scopus subject area, used as extra context.
        citescore: Scopus CiteScore, used as extra context.
        model_name: LiteLLM model identifier.
        temperature: Sampling temperature.
        max_retries: Retries for schema validation failures.

    Returns:
        The verdict of the LLM for this journal.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT.format(
                title=title,
                publisher=_format_value(publisher),
                subject=_format_value(subject),
                citescore=_format_value(citescore),
            ),
        },
    ]

    def _create(temp: float) -> JournalVerdict:
        return _get_client().chat.completions.create(
            model=model_name,
            messages=messages,
            response_model=JournalVerdict,
            temperature=temp,
            max_retries=max_retries,
        )

    try:
        return _create(_FIXED_TEMPERATURE.get(model_name, temperature))
    except Exception as exc:  # noqa: BLE001 - instructor wraps the litellm error
        if "Only temperature=1 is supported" not in str(exc):
            raise
        # reasoning models such as claude-sonnet-5 only accept temperature=1
        logger.info("{} requires temperature=1, switching for this run", model_name)
        _FIXED_TEMPERATURE[model_name] = 1.0
        return _create(1.0)


def _load_cache(path: Path) -> dict:
    """Load previously written decisions, keyed by journal title."""
    if not path.exists():
        return {}
    cached = {}
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed cache line in {}", path)
                continue
            # only reuse successful decisions, retry the failed ones
            if record.get("error") is None:
                cached[record["source_title"]] = record
    return cached


def run(
    whitelist: Optional[str] = None,
    output_dir: Optional[str] = None,
    model_name: str = "claude-sonnet-5",
    max_workers: int = 8,
    min_confidence: float = 0.0,
    limit: Optional[int] = None,
    overwrite: bool = False,
) -> None:
    """Filter the journal whitelist with one LLM call per journal.

    Writes three files into ``output_dir``:

    * ``journal_decisions.jsonl`` - one decision per journal (cache and audit trail),
    * ``allowed_journals_filtered.csv`` - the whitelist without the rejected journals,
    * ``allowed_journals_rejected.csv`` - the removed journals with the reason, for review.

    Args:
        whitelist: Path to the whitelist CSV. Defaults to the packaged ``allowed_journals.csv``.
        output_dir: Where to write the outputs. Defaults to the directory of the whitelist.
        model_name: LiteLLM model identifier.
        max_workers: Number of concurrent LLM calls.
        min_confidence: Only drop a journal if the LLM was at least this confident.
            Raise this to make the filter more conservative.
        limit: Only process the first ``limit`` journals (useful for a dry run).
        overwrite: Ignore the existing decision cache and re-query every journal.
    """
    whitelist_path = Path(whitelist) if whitelist else _default_whitelist()
    # artifacts live next to this script, not inside the installed package
    out_dir = Path(output_dir) if output_dir else Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    decisions_path = out_dir / "journal_decisions.jsonl"
    filtered_path = out_dir / "allowed_journals_filtered.csv"
    rejected_path = out_dir / "allowed_journals_rejected.csv"

    df = pd.read_csv(whitelist_path)
    if limit is not None:
        df = df.head(limit)
    logger.info("Read {} journals from {}", len(df), whitelist_path)

    if overwrite and decisions_path.exists():
        decisions_path.unlink()
    cache = _load_cache(decisions_path)
    logger.info("Reusing {} cached decisions", len(cache))

    todo = df[~df["Source title"].isin(cache)].drop_duplicates(subset="Source title")
    logger.info("Querying {} journals with {}", len(todo), model_name)

    write_lock = threading.Lock()

    def _work(row: pd.Series) -> dict:
        title = row["Source title"]
        try:
            verdict = classify_journal(
                title=title,
                publisher=row.get("Publisher"),
                subject=_subject_area(row.get("Highest percentile")),
                citescore=row.get("CiteScore"),
                model_name=model_name,
            )
            record = {
                "source_title": title,
                "error": None,
                **verdict.model_dump(),
            }
        except Exception as exc:  # noqa: BLE001 - one bad journal must not kill the run
            logger.error("Failed for {!r}: {}", title, exc)
            record = {"source_title": title, "error": str(exc)}
        with write_lock:
            with decisions_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
        return record

    if len(todo):
        # The first journal is classified in the main thread: instructor populates
        # its mode registry lazily on the first request, and doing that from
        # several worker threads at once raises a RegistryError.
        rows = [row for _, row in todo.iterrows()]
        first = _work(rows[0])
        if first.get("error") is None:
            cache[first["source_title"]] = first
        else:
            logger.warning("First journal failed, aborting: {}", first["error"])
            return

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_work, row) for row in rows[1:]]
            for done, future in enumerate(as_completed(futures), start=1):
                record = future.result()
                if record.get("error") is None:
                    cache[record["source_title"]] = record
                if done % 25 == 0 or done == len(futures):
                    logger.info("{}/{} journals classified", done, len(futures))

    n_failed = sum(1 for title in df["Source title"] if title not in cache)
    if n_failed:
        logger.warning(
            "{} journals have no decision (LLM errors); they are kept. "
            "Re-run to retry them.",
            n_failed,
        )

    def _keep(title: str) -> bool:
        record = cache.get(title)
        if record is None:
            return True  # no decision -> keep, be conservative
        if record["could_publish_perovskite_experiment"]:
            return True
        return record["confidence"] < min_confidence  # not confident enough to drop

    keep_mask = df["Source title"].map(_keep)

    df[keep_mask].to_csv(filtered_path, index=False)

    rejected = df[~keep_mask].copy()
    rejected["llm_reason"] = rejected["Source title"].map(
        lambda title: cache[title]["reason"]
    )
    rejected["llm_confidence"] = rejected["Source title"].map(
        lambda title: cache[title]["confidence"]
    )
    rejected.to_csv(rejected_path, index=False)

    logger.info(
        "Kept {}/{} journals, removed {}", int(keep_mask.sum()), len(df), len(rejected)
    )
    logger.info("Wrote {}", filtered_path)
    logger.info("Wrote {}", rejected_path)
    logger.info("Decisions in {}", decisions_path)


def main_cli() -> None:
    fire.Fire({"run": run, "classify": classify_journal})


if __name__ == "__main__":
    main_cli()
