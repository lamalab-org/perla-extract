from perovscribe.preprocessing.pymupdf_processor import PyMuPDFPreprocessor


def test_pymupdf(get_nat_comm_7139_file):
    preprocessor = PyMuPDFPreprocessor("pymupdf", cache_dir_root=None, use_cache=False)
    text = preprocessor.pdf_to_text(get_nat_comm_7139_file)
    assert len(text) > 0
    assert isinstance(text, str)
