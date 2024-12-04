from perovscribe.postprocessing import normalize


def test_normalize(normalize_input, normalize_output):
    print(normalize(normalize_input), "\n=====\n", normalize_output)
    assert normalize(normalize_input) == normalize_output
