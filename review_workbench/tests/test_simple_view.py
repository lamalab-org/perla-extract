"""Behaviour of the simplified A/B page's pure browser module."""

import json
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "review_app"
pytestmark = pytest.mark.usefixtures("node_runtime")

FAKE_DOCUMENT = """
class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.className = ''; this._text = ''; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  click() { this.listeners.click?.(); }
  set textContent(value) { this._text = String(value); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
}
const fakeDocument = {
  createElement: tag => new FakeNode(tag),
  createTextNode: text => ({ textContent: text, children: [] }),
};
const all = node => [node, ...(node.children || []).flatMap(all)];
"""


def run_javascript(script: str) -> None:
    module = (APP / "simple-view.js").as_uri()
    imports = (
        "import assert from 'node:assert/strict';\n"
        "import { citationLineIndexes, doiFromPaperId, paperFromCrossref, "
        "renderProse, scrollAnchor, scrollTarget } from "
        f"{json.dumps(module)};\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", imports + FAKE_DOCUMENT + script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_prose_renders_sections_citations_and_references():
    run_javascript("""
const prose = {
  sections: [
    {key: 'study', title: 'Study', paragraphs: [{segments: [{text: 'Two families.', citations: []}]}]},
    {key: 'devices', title: 'Investigated devices', paragraphs: [
      {segments: [{text: 'Family 1, called “Reference”', citations: [1, 2]}, {text: ', is planar.', citations: []}]},
    ]},
  ],
  references: [
    {number: 1, source: 'main', page: 2, quote: 'planar devices'},
    {number: 2, source: null, page: null, quote: 'lost quote'},
  ],
};
const cited = [];
const article = renderProse(fakeDocument, prose, {label: 'B', onCite: (reference, label) => cited.push([reference.number, label])});
const nodes = all(article);
assert.deepEqual(nodes.filter(n => n.dataset?.section).map(n => n.dataset.section), ['study', 'devices', 'references']);
const paragraph = nodes.find(n => n.tag === 'p' && n.textContent.startsWith('Family 1'));
assert.equal(paragraph.textContent, 'Family 1, called “Reference”[1,2], is planar.');
const buttons = nodes.filter(n => n.className === 'cite');
assert.deepEqual(buttons.map(n => n.dataset.reference), ['1', '2']);
assert.equal(buttons[0].title, 'Main paper, p. 2: “planar devices”');
buttons[0].click();
const listed = nodes.filter(n => n.className === 'reference');
assert.equal(listed[1].textContent, '[2] Source block not foundlost quote');
listed[1].click();
assert.deepEqual(cited, [[1, 'B'], [2, 'B']]);
assert.equal(article.attributes['aria-label'], 'Extraction B');
""")


def test_scrolling_aligns_the_same_section_and_fraction():
    run_javascript("""
const a = [{key: 'study', top: 0, height: 100}, {key: 'devices', top: 100, height: 400}, {key: 'results', top: 500, height: 200}];
const b = [{key: 'study', top: 0, height: 60}, {key: 'devices', top: 60, height: 800}, {key: 'results', top: 860, height: 100}];
assert.deepEqual(scrollAnchor(a, 0), {key: 'study', fraction: 0});
assert.deepEqual(scrollAnchor(a, 300), {key: 'devices', fraction: 0.5});
assert.equal(scrollTarget(b, scrollAnchor(a, 300)), 460);
assert.equal(scrollTarget(b, scrollAnchor(a, 600)), 910);
assert.equal(scrollTarget(b, {key: 'missing', fraction: 0}), null);
assert.equal(scrollAnchor([], 10), null);
""")


def test_quotes_are_matched_across_lines_and_punctuation():
    run_javascript("""
const lines = [{text: 'The champion device reached'}, {text: 'a PCE of 24.1 %, which'}, {text: 'is high.'}];
assert.deepEqual(citationLineIndexes(lines, 'champion device reached a PCE of 24.1%'), [0, 1]);
assert.deepEqual(citationLineIndexes(lines, 'not on this page'), []);
assert.deepEqual(citationLineIndexes(lines, ''), []);
""")


def test_publication_metadata_comes_from_a_doi():
    run_javascript("""
assert.equal(doiFromPaperId('10.1021--acsaem.1c00413'), '10.1021/acsaem.1c00413');
assert.equal(doiFromPaperId('local-paper'), null);
const paper = paperFromCrossref({
  title: ['A synthetic study'], 'container-title': ['Synthetic Letters'], DOI: '10.0000/x',
  author: [{given: 'Ada', family: 'Lovelace'}, {name: 'PERLA Consortium'}],
  issued: {'date-parts': [[2026, 3]]},
});
assert.deepEqual(paper, {title: 'A synthetic study', authors: ['Ada Lovelace', 'PERLA Consortium'],
  journal: 'Synthetic Letters', year: 2026, doi: '10.0000/x'});
assert.deepEqual(paperFromCrossref({}), {title: null, authors: [], journal: null, year: null, doi: null});
""")
