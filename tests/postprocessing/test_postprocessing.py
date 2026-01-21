from perla_extract.postprocessing import normalize


def test_normalize(normalize_input, normalize_output):
    assert normalize(normalize_input) == normalize_output
