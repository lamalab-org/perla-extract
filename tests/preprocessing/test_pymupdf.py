from perovscribe.preprocessing.pymupdf_processor import PyMuPDFPreprocessor


def test_pymupdf(get_nat_comm_7139_file):
    preprocessor = PyMuPDFPreprocessor("pymupdf", cache_dir_root=None, use_cache=False)
    text = preprocessor.pdf_to_text(get_nat_comm_7139_file)
    assert len(text) > 0
    regression_text = ("Article" + "\n"
                        "https://doi.org/10.1038/s41467-024-51550-z" + "\n"
                        "Stabilization of highly efﬁcient perovskite" + "\n"
                        "solar cells with a tailored supramolecular" + "\n"
                        "interface" + "\n"
                        "Chenxu Zhao" + "\n"
                        "1,2,3,10, Zhiwen Zhou1,4,10" + "\n"
                        ", Masaud Alm")
    assert text[0:200] == regression_text
