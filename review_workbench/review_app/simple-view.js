/**
 * Pure building blocks of the simplified A/B comparison page.
 *
 * Nothing here touches global state: prose rendering takes the document to build
 * with, and scroll alignment works on plain section offsets, so every piece can be
 * tested without a browser.
 */

const SOURCE_NAMES = { main: "Main paper", supplement: "Supporting information" };

export function sourceName(source) {
  return SOURCE_NAMES[source] || "Unknown source";
}

/** Fold case, dashes and punctuation so a quote can be found in PDF text lines. */
export function normalizedCitation(value) {
  let result = "";
  const text = String(value || "").normalize("NFKC").replaceAll("−", "-").replaceAll("–", "-").replaceAll("—", "-");
  for (const character of text) {
    for (const folded of character.toLocaleLowerCase()) {
      if (/[\p{L}\p{N}_.%<>~=+\/-]/u.test(folded)) result += folded;
    }
  }
  return result;
}

/** Indexes of the page text lines that together contain the quote; empty if absent. */
export function citationLineIndexes(lines, quote) {
  const needle = normalizedCitation(quote);
  if (!needle) return [];
  let haystack = "";
  const owners = [];
  lines.forEach((line, index) => {
    const normalized = normalizedCitation(line.text);
    haystack += normalized;
    for (let position = 0; position < normalized.length; position += 1) owners.push(index);
  });
  const start = haystack.indexOf(needle);
  return start < 0 ? [] : [...new Set(owners.slice(start, start + needle.length))];
}

/** Paper identifiers are DOIs with "/" written as "--" (10.1021--acsaem.1c00413). */
export function doiFromPaperId(paperId) {
  const value = String(paperId || "");
  return /^10\.\d{4,9}--/.test(value) ? value.replace("--", "/") : null;
}

/** Turn a Crossref "message" into the comparison's publication metadata. */
export function paperFromCrossref(message) {
  const authors = (message?.author || []).map(author =>
    [author.given, author.family].filter(Boolean).join(" ") || author.name || "").filter(Boolean);
  const parts = message?.issued?.["date-parts"]?.[0] || message?.published?.["date-parts"]?.[0] || [];
  return {
    title: message?.title?.[0] || null,
    authors,
    journal: message?.["container-title"]?.[0] || null,
    year: Number.isInteger(parts[0]) ? parts[0] : null,
    doi: message?.DOI || null,
  };
}

/** Where a pane is scrolled to: the section at its top and how far into it. */
export function scrollAnchor(sections, scrollTop) {
  if (!sections.length) return null;
  let current = sections[0];
  for (const section of sections) if (section.top <= scrollTop + 1) current = section;
  const fraction = current.height > 0 ? (scrollTop - current.top) / current.height : 0;
  return { key: current.key, fraction: Math.min(1, Math.max(0, fraction)) };
}

/** The scroll position that shows the same section and fraction in another pane. */
export function scrollTarget(sections, anchor) {
  const section = anchor && sections.find(item => item.key === anchor.key);
  if (!section) return null;
  return section.top + anchor.fraction * section.height;
}

function citationLabel(numbers) {
  return `[${numbers.join(",")}]`;
}

/**
 * Render one candidate's prose.
 *
 * Citations become buttons carrying their reference number; ``onCite`` receives the
 * reference so the page can open and highlight its quote. Section elements carry
 * ``data-section`` so both panes can be aligned while scrolling.
 */
export function renderProse(document, prose, { label, onCite = () => {} } = {}) {
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const references = new Map((prose.references || []).map(reference => [reference.number, reference]));
  const article = make("article", "prose");
  article.setAttribute("aria-label", `Extraction ${label}`);

  for (const section of prose.sections || []) {
    const element = make("section", "prose-section");
    element.dataset.section = section.key;
    element.append(make("h3", "", section.title));
    for (const paragraph of section.paragraphs || []) {
      const node = make("p", "");
      for (const segment of paragraph.segments || []) {
        node.append(document.createTextNode(segment.text));
        if (!segment.citations?.length) continue;
        const cite = make("sup", "citation");
        segment.citations.forEach((number, index) => {
          const reference = references.get(number);
          const button = make("button", "cite", String(number));
          button.type = "button";
          button.dataset.reference = String(number);
          button.title = reference ? `${sourceName(reference.source)}${reference.page ? `, p. ${reference.page}` : ""}: “${reference.quote}”` : "";
          button.setAttribute("aria-label", `Reference ${number} of extraction ${label}`);
          button.addEventListener("click", () => reference && onCite(reference, label));
          cite.append(index === 0 ? document.createTextNode("[") : document.createTextNode(","), button);
        });
        cite.append(document.createTextNode("]"));
        node.append(cite);
      }
      element.append(node);
    }
    article.append(element);
  }

  const list = make("section", "prose-section prose-references");
  list.dataset.section = "references";
  list.append(make("h3", "", "References"));
  if (!(prose.references || []).length) list.append(make("p", "muted", "No citations."));
  const ordered = make("ol", "");
  for (const reference of prose.references || []) {
    const item = make("li", "");
    item.value = reference.number;
    const button = make("button", "reference", "");
    button.type = "button";
    button.dataset.reference = String(reference.number);
    button.append(
      make("span", "reference-place", reference.page
        ? `${citationLabel([reference.number])} ${sourceName(reference.source)}, p. ${reference.page}`
        : `${citationLabel([reference.number])} Source block not found`),
      make("q", "", reference.quote),
    );
    button.addEventListener("click", () => onCite(reference, label));
    item.append(button);
    ordered.append(item);
  }
  list.append(ordered);
  article.append(list);
  return article;
}
