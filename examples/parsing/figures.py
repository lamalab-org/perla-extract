"""Test native figure-text recovery using saved, complete Docling conversions.

This is a parser diagnostic, not a production backend. Existing evidence stays
unchanged. The supplement retains native PDF text and its physical location;
it cannot recover rasterized labels or infer values from plotted curves.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import pymupdf

from examples.parsing.compare import literal_present


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def figure_regions(document: dict) -> list[dict]:
    """Use Docling picture boxes and explicit caption links, without guessing.

    All returned coordinates are PDF points measured from the top left. Small
    logos and panel letters are excluded by size, not by reference outcomes.
    """
    regions = []
    for picture in document.get("pictures", []):
        captions = []
        for ref in picture.get("captions", []):
            _, collection, index = ref["$ref"].split("/")
            captions.append(document[collection][int(index)].get("text", ""))
        for prov in picture.get("prov", []):
            page = prov["page_no"]
            size = document["pages"][str(page)]["size"]
            box = prov["bbox"]
            if box["coord_origin"] == "BOTTOMLEFT":
                rect = pymupdf.Rect(
                    box["l"],
                    size["height"] - box["t"],
                    box["r"],
                    size["height"] - box["b"],
                )
            elif box["coord_origin"] == "TOPLEFT":
                rect = pymupdf.Rect(box["l"], box["t"], box["r"], box["b"])
            else:
                raise ValueError(f"Unknown coordinate origin: {box}")
            bounds = pymupdf.Rect(0, 0, size["width"], size["height"])
            if not bounds.contains(rect) or rect.is_empty:
                raise ValueError(f"Invalid picture box on page {page}: {rect}")
            if (
                rect.width < 80
                or rect.height < 40
                or rect.get_area() < bounds.get_area() * 0.015
            ):
                continue
            regions.append(
                {
                    "picture_ref": picture["self_ref"],
                    "page": page,
                    "bbox": list(rect),
                    "caption": "\n".join(captions),
                    "caption_refs": picture.get("captions", []),
                    "page_fraction": rect.get_area() / bounds.get_area(),
                }
            )
    # Assign a native span to the smallest containing panel before larger figures.
    return sorted(
        regions,
        key=lambda r: (r["page"], pymupdf.Rect(r["bbox"]).get_area(), r["picture_ref"]),
    )


def represented(span: dict, blocks: list[dict]) -> bool:
    """Deduplicate only text already present at the same physical location.

    The same number in another panel or on another page is independent evidence.
    Blocks lacking geometry cannot establish a physical duplicate.
    """
    rect = pymupdf.Rect(span["bbox"])
    text = "".join(span["text"].split())
    return any(
        block.get("bbox")
        and (rect & pymupdf.Rect(block["bbox"])).get_area() >= 0.8 * rect.get_area()
        and text in "".join(block["text"].split())
        for block in blocks
    )


def recover(pdf, document: dict, blocks: list[dict]) -> list[dict]:
    """Attach native spans to picture regions, retaining font, color and geometry."""
    pages = {}
    seen = set()
    recovered = []
    for region in figure_regions(document):
        page_no = region["page"]
        if page_no not in pages:
            pages[page_no] = pdf[page_no - 1].get_text(
                "dict",
                sort=True,
                flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES,
            )
        rect = pymupdf.Rect(region["bbox"])
        spans = []
        page_blocks = [b for b in blocks if b["page"] == page_no]
        for block in pages[page_no]["blocks"]:
            for line_index, line in enumerate(block.get("lines", [])):
                for span in line["spans"]:
                    span_rect = pymupdf.Rect(span["bbox"])
                    key = (page_no, tuple(span["bbox"]), span["text"])
                    if key in seen or not span["text"].strip() or span_rect.is_empty:
                        continue
                    if (rect & span_rect).get_area() < 0.8 * span_rect.get_area():
                        continue
                    seen.add(key)
                    spans.append(
                        {
                            "text": span["text"],
                            "bbox": list(span_rect),
                            "color": f"#{span['color']:06x}",
                            "font": span["font"],
                            "size": span["size"],
                            "direction": list(line["dir"]),
                            "line": [block["number"], line_index],
                            "already_in_baseline": represented(span, page_blocks),
                        }
                    )

        def joined(only_new: bool) -> str:
            lines = {}
            for span in spans:
                if not only_new or not span["already_in_baseline"]:
                    lines.setdefault(tuple(span["line"]), []).append(span["text"])
            return "\n".join(" ".join(parts) for parts in lines.values())

        recovered.append(
            region
            | {
                "text": joined(False),
                "new_text": joined(True),
                "spans": spans,
                "native_characters": sum(len(s["text"]) for s in spans),
                "new_spans": sum(not s["already_in_baseline"] for s in spans),
            }
        )
    return recovered


def probe_comparison(blocks: list[dict], figures: list[dict], job: dict) -> list[dict]:
    """Run the existing page-specific literal diagnostic without changing scoring."""
    baseline = {}
    supplement = {}
    start, end = job.get("page_range", (1, float("inf")))
    for block in blocks:
        baseline.setdefault(block["page"], []).append(block["text"])
    for figure in figures:
        supplement.setdefault(figure["page"], []).append(figure["new_text"])
    return [
        p
        | {
            "baseline": literal_present(
                p["literal"], "\n".join(baseline.get(p["page"], []))
            ),
            "supplemented": literal_present(
                p["literal"],
                "\n".join(baseline.get(p["page"], []) + supplement.get(p["page"], [])),
            ),
        }
        for p in job["probes"]
        if p["source"] == job["source"] and start <= p["page"] <= end
    ]


def run(baseline: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    plan = read_json(baseline / "plan.json")
    jobs = [j for j in plan["jobs"] if j["condition"] == "standard"]
    # Freeze admission before reading any recovery outcomes. The upstream study
    # can continue; newly completed baselines belong to a subsequent test.
    admitted = []
    for job in jobs:
        folder = baseline / job["paper_id"] / job["source"] / "standard"
        metrics = folder / "metrics.json"
        if metrics.exists() and read_json(metrics)["status"] == "complete":
            admitted.append((job, folder))
    write_json(
        output / "protocol.json",
        {
            "baseline": str(baseline),
            "plan_sha256": digest(baseline / "plan.json"),
            "script_sha256": digest(Path(__file__)),
            "sources": [
                {
                    "paper_id": j["paper_id"],
                    "source": j["source"],
                    "pdf_sha256": j["source_sha256"],
                    "docling_sha256": digest(f / "docling.json"),
                    "evidence_sha256": digest(f / "document.json"),
                    "reference_sha256": j["reference_sha256"],
                }
                for j, f in admitted
            ],
            "production_changes": False,
            "provider_requests": 0,
            "metric": "Existing source/page-specific literal visibility; not field precision/recall",
        },
    )
    rows, index = [], []
    for job, folder in admitted:
        start = time.perf_counter()
        pdf_path = Path(job["pdf"])
        if digest(pdf_path) != job["source_sha256"]:
            raise ValueError(f"Source PDF changed: {pdf_path}")
        blocks = read_json(folder / "document.json")["blocks"]
        with pymupdf.open(pdf_path) as pdf:
            figures = recover(pdf, read_json(folder / "docling.json"), blocks)
        for figure in figures:
            figure.update(
                paper_id=job["paper_id"], source=job["source"], pdf=str(pdf_path)
            )
            figure["id"] = (
                f"{job['paper_id']}/{job['source']}/p{figure['page']}-{figure['picture_ref'].split('/')[-1]}"
            )
        probes = probe_comparison(blocks, figures, job)
        destination = output / job["paper_id"] / job["source"]
        write_json(destination / "figures.json", figures)
        write_json(destination / "probes.json", probes)
        rows.append(
            {
                "paper_id": job["paper_id"],
                "source": job["source"],
                "figures": len(figures),
                "native_spans": sum(len(f["spans"]) for f in figures),
                "new_spans": sum(f["new_spans"] for f in figures),
                "new_characters": sum(len(f["new_text"]) for f in figures),
                "probes": len(probes),
                "baseline_found": sum(p["baseline"] for p in probes),
                "supplemented_found": sum(p["supplemented"] for p in probes),
                "seconds": time.perf_counter() - start,
            }
        )
        index.extend(figures)
    write_json(output / "figures.json", index)
    if rows:
        with (output / "results.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    write_json(
        output / "summary.json",
        {
            "sources": len(rows),
            "papers": len({r["paper_id"] for r in rows}),
            **{
                k: sum(r[k] for r in rows)
                for k in (
                    "figures",
                    "native_spans",
                    "new_spans",
                    "new_characters",
                    "probes",
                    "baseline_found",
                    "supplemented_found",
                    "seconds",
                )
            },
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, required=True, help="Existing compare.py runs folder"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New folder for this test"
    )
    args = parser.parse_args()
    run(args.baseline.resolve(), args.output.resolve())
