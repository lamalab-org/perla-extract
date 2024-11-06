from perovscribe.evaluations import Evaluations

import os
import json
THIS_DIR = os.path.dirname(os.path.realpath(__file__))
def test_evaluations():
    
    with open(os.path.dirname(os.path.realpath(__file__)) + os.sep + "truth_test_file_10.1016--j.orgel.2017.01.022.json") as f:
        truth = json.load(f)
    with open(os.path.join(THIS_DIR,  "extraction_best_test_file_10.1016--j.orgel.2017.01.022.json")) as f:
        best_extraction = json.load(f)
    with open(os.path.join(THIS_DIR,  "extraction_mid_test_file_10.1016--j.orgel.2017.01.022.json")) as f:
        mid_extraction = json.load(f)
    with open(os.path.join(THIS_DIR,  "extraction_worst_test_file_10.1016--j.orgel.2017.01.022.json")) as f:
        worst_extraction = json.load(f)
    assert Evaluations(truth, best_extraction).score > Evaluations(truth, mid_extraction).score
    assert Evaluations(truth, mid_extraction).score > Evaluations(truth, worst_extraction).score