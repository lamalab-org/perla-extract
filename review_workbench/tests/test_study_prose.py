import json

from perla_extract.study_extraction.models import EvidenceBlock
from review_workbench.study_prose import (
    EMPTY,
    SECTIONS,
    PaperInfo,
    authors_phrase,
    render_prose,
)

BLOCKS = [
    EvidenceBlock(
        block_id="main_p2_text_1",
        source="main",
        page=2,
        kind="text",
        text="Devices used FTO/SnO2/perovskite/Spiro-OMeTAD/Au.",
    ),
    EvidenceBlock(
        block_id="main_p3_text_4",
        source="main",
        page=3,
        kind="text",
        text="The film was spin-coated at 4000 rpm for 30 s and annealed at 150 °C.",
    ),
    EvidenceBlock(
        block_id="supplement_p1_table_1",
        source="supplement",
        page=1,
        kind="table",
        text="Champion PCE 23.1% under AM1.5G.",
    ),
]


def cite(block_id: str, quote: str) -> list[dict]:
    return [{"block_id": block_id, "quote": quote}]


STACK = cite("main_p2_text_1", "FTO/SnO2/perovskite/Spiro-OMeTAD/Au")
FILM = cite("main_p3_text_4", "spin-coated at 4000 rpm for 30 s")
TABLE = cite("supplement_p1_table_1", "Champion PCE 23.1%")


def value(name: str, raw: str, unit: str | None, evidence: list[dict]) -> dict:
    return {
        "name": name,
        "raw_value": raw,
        "value_number": None,
        "unit": unit,
        "evidence": evidence,
    }


def layer(layer_id: str, sequence: int, role: str, material: str) -> dict:
    return {
        "layer_id": layer_id,
        "sequence": sequence,
        "role": role,
        "material": material,
        "constituents": [],
        "reported_properties": [],
        "evidence": STACK,
    }


def study(prefix: str = "x") -> dict:
    """A small synthetic study; ``prefix`` changes every identifier only."""

    family = f"{prefix}-family"
    device = f"{prefix}-device"
    return {
        "paper": {"title": "Synthetic study", "doi": None},
        "device_families": [
            {
                "family_id": family,
                "label": "Reference cell",
                "variant": None,
                "architecture": "planar",
                "polarity": "n-i-p",
                "full_stack_raw": None,
                "layers": [
                    layer(f"{prefix}-l3", 3, "absorber", "perovskite"),
                    layer(f"{prefix}-l1", 1, "transparent_electrode", "FTO"),
                    layer(f"{prefix}-l2", 2, "electron_transport_layer", "SnO2"),
                ],
                "absorbers": [],
                "processing_steps": [
                    {
                        "step_id": f"{prefix}-s1",
                        "sequence": 1,
                        "operation": "spin coating",
                        "target_layer_ids": [f"{prefix}-l3"],
                        "materials": ["perovskite precursor"],
                        "conditions": [
                            value("spin speed", "4000", "rpm", FILM),
                            value("spin duration", "30", "s", FILM),
                        ],
                        "evidence": FILM,
                    }
                ],
                "evidence": STACK,
            },
            {
                "family_id": f"{prefix}-other",
                "label": "Control cell",
                "variant": "without passivation",
                "architecture": None,
                "polarity": "not_reported",
                "full_stack_raw": None,
                "layers": [layer(f"{prefix}-c1", 1, "transparent_electrode", "ITO")],
                "absorbers": [],
                "processing_steps": [],
                "evidence": STACK,
            },
        ],
        "individual_devices": [
            {
                "device_id": device,
                "family_id": family,
                "label": "Champion",
                "variant": None,
                "champion_status": "yes",
                "selection_basis": "champion",
                "subject_context": None,
                "reported_properties": [],
                "evidence": TABLE,
            }
        ],
        "performance_observations": [
            {
                "observation_id": f"{prefix}-obs",
                "device_id": device,
                "measurement_type": "jv_scan",
                "scan_direction": "reverse",
                "measurement_conditions": [value("spectrum", "AM1.5G", None, TABLE)],
                "metrics": [value("PCE", "23.1", "%", TABLE)],
                "evidence": TABLE,
            }
        ],
        "population_statistics": [],
        "stability_tests": [],
        "unresolved_notes": ["Internal reviewer remark"],
    }


def plain(document) -> str:
    return "\n".join(
        "".join(
            segment.text
            + (
                f"[{','.join(map(str, segment.citations))}]"
                if segment.citations
                else ""
            )
            for segment in paragraph.segments
        )
        for section in document.sections
        for paragraph in section.paragraphs
    )


PAPER = PaperInfo(
    title="A synthetic perovskite study",
    authors=["Ada Lovelace", "Hopper, Grace", "Alan Turing"],
    journal="Synthetic Letters",
    year=2026,
)


def population(population_id: str, family_id: str, label: str, raw: str) -> dict:
    return {
        "population_id": population_id,
        "family_id": family_id,
        "label": label,
        "statistic_type": "median",
        "sample_size": 20,
        "subjects": [],
        "error_bar_type": "not_reported",
        "measurement_type": "jv_scan",
        "scan_direction": "reverse",
        "measurement_conditions": [],
        "metrics": [value("PCE", raw, "%", TABLE)],
        "evidence": TABLE,
    }


def stability_test() -> dict:
    return {
        "test_id": "x-test",
        "family_id": None,
        "device_id": "x-device",
        "specimen_label": "champion",
        "link_status": "explicit_device_link",
        "conditions": [value("temperature", "85", "°C", TABLE)],
        "checkpoints": [
            {
                "checkpoint_id": "x-checkpoint",
                "time": value("time", "1000", "h", TABLE),
                "conditions": [],
                "outcomes": [value("retained PCE", "90", "%", TABLE)],
                "evidence": FILM,
            }
        ],
        "evidence": TABLE,
    }


def test_every_section_is_present_in_a_fixed_order(empty_study):
    document = render_prose(empty_study, BLOCKS)

    assert [section.key for section in document.sections] == [k for k, _ in SECTIONS]
    texts = {
        section.key: [p.segments[0].text for p in section.paragraphs]
        for section in document.sections
    }
    assert texts["study"] == [
        "The authors describe the fabrication of no device families. They "
        "characterize no individual devices and no device populations, and report "
        "no stability tests."
    ]
    for key in ("devices", "results", "stability"):
        assert texts[key] == [EMPTY[key]]
    assert document.references == []


def test_authors_are_named_as_in_a_scientific_citation():
    assert authors_phrase([]) == "The authors"
    assert authors_phrase(["Ada Lovelace"]) == "Lovelace"
    assert authors_phrase(["Ada Lovelace", "Hopper, Grace"]) == "Lovelace and Hopper"
    assert authors_phrase(PAPER.authors) == "Lovelace et al."


def test_study_opens_with_the_publication_and_what_the_authors_did():
    text = plain(render_prose(study(), BLOCKS, PAPER))

    assert text.startswith(
        "Lovelace et al. in “A synthetic perovskite study” (Synthetic Letters, 2026) "
        "describe the fabrication of two device families. They characterize one "
        "individual device and no device populations, and report no stability tests."
    )


def test_families_read_as_fabrication_steps_and_architecture():
    text = plain(render_prose(study(), BLOCKS, PAPER))

    assert (
        "Family 1, called “Reference cell”[1], is fabricated in the following way: "
        "(1) spin coating of perovskite precursor for the perovskite layer[2], with "
        "spin speed of 4000 rpm[2] and spin duration of 30 s[2]. The fabricated "
        "devices have the architecture FTO (transparent electrode)[1] | SnO2 "
        "(electron-transport layer)[1] | perovskite (absorber)[1] (n-i-p; planar)."
    ) in text
    assert (
        "Family 2, called “Control cell” (without passivation)[1], has no reported "
        "fabrication steps."
    ) in text


def test_results_compare_populations_and_name_champion_devices():
    payload = study()
    payload["population_statistics"] = [
        population("x-p2", "x-other", "Control batch", "19.2"),
        population("x-p1", "x-family", "Reference batch", "21.8"),
    ]

    text = plain(render_prose(payload, BLOCKS, PAPER))

    assert (
        "Lovelace et al. report a median PCE of 21.8%[3] for family 1 (“Reference "
        "batch”, n = 20, reverse J–V scan), as compared to 19.2%[3] for family 2 "
        "(“Control batch”, n = 20, reverse J–V scan)."
    ) in text
    assert (
        "What concerns champion devices, the authors measured PCE of 23.1%[3] "
        "(reverse J–V scan under spectrum of AM1.5G[3]) for the champion device "
        "“Champion” of family 1."
    ) in text


def test_stability_reports_what_each_test_yielded():
    payload = study()
    payload["stability_tests"] = [stability_test()]

    text = plain(render_prose(payload, BLOCKS, PAPER))

    assert (
        "Stability investigations yielded the following. The champion device "
        "“Champion” of family 1[3] was aged with temperature of 85 °C[3]. After "
        "1000 h[2,3], the authors observed retained PCE of 90%[3]."
    ) in text


def test_absorbers_and_constituents_read_as_sentences():
    payload = study()
    payload["device_families"][0]["absorbers"] = [
        {
            "absorber_id": "x-absorber",
            "layer_id": "x-l3",
            "label": "main absorber",
            "formula": value("formula", "Cs0.05FA0.95PbI3", None, STACK),
            "properties": [value("band gap", "1.53", "eV", TABLE)],
            "constituents": [
                {
                    "name": "PbI2",
                    "role": "precursor",
                    "amount": value("concentration", "1.5", "M", FILM),
                    "evidence": FILM,
                }
            ],
            "evidence": STACK,
        }
    ]

    text = plain(render_prose(payload, BLOCKS, PAPER))

    assert (
        "The absorber “main absorber” in the perovskite layer[1] has the formula "
        "Cs0.05FA0.95PbI3[1], contains PbI2 as precursor (1.5 M)[2]; it has a band "
        "gap of 1.53 eV[3]."
    ) in text


def test_rendering_is_blind_to_identifiers_order_notes_and_candidate_metadata():
    first = study("ground")
    second = study("model")
    second["device_families"].reverse()
    second["device_families"][1]["layers"].reverse()
    second["paper"] = {"title": None, "doi": None}
    for payload in (first, second):
        payload["stability_tests"] = [stability_test()]
        payload["stability_tests"][0]["device_id"] = payload["individual_devices"][0][
            "device_id"
        ]

    a = render_prose(first, BLOCKS, PAPER)
    b = render_prose(second, BLOCKS, PAPER)

    assert a == b
    rendered = json.dumps(a.model_dump(mode="json"))
    for hidden in (
        "ground",
        "family_id",
        "main_p2_text_1",
        "Internal reviewer remark",
        "Synthetic study",
    ):
        assert hidden not in rendered


def test_references_are_numbered_by_first_use_and_merged_by_page_and_quote():
    payload = study()
    payload["device_families"][1]["evidence"] = cite(
        "main_p2_text_1", "FTO/SnO2/perovskite/Spiro-OMeTAD/Au "
    )
    payload["individual_devices"][0]["evidence"] = cite("missing_block", "a lost quote")

    document = render_prose(payload, BLOCKS)

    assert [(r.number, r.source, r.page) for r in document.references] == [
        (1, "main", 2),
        (2, "main", 3),
        (3, "supplement", 1),
        (4, None, None),
    ]
    assert document.references[3].quote == "a lost quote"
