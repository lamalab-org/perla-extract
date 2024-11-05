from perovscribe.evaluations import Evaluations

import os
import json

def test_evaluations():
    
    with open(os.path.dirname(os.path.realpath(__file__)) + os.sep + "truth_test_file_10.1016--j.orgel.2017.01.022.json") as f:
        truth = json.load(f)
    with open(os.path.dirname(os.path.realpath(__file__)) + os.sep + "extraction_test_file_10.1016--j.orgel.2017.01.022.json") as f:
        extraction = json.load(f)
    assert Evaluations(truth, extraction).score == 0.9998021369212505