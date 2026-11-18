// Completeness is a source-review decision, never inferred from model agreement.
const domains = {
  performance: "Performance",
  material_device_identity: "Material and device identity",
  processing: "Processing",
  stability: "Stability",
};
const fields = {
  text_and_tables: "Text and tables",
  figures: "Figures",
  attribution_context: "Specimen, treatment and measurement context",
};
const statuses = {
  unreviewed: "Not reviewed",
  partial: "Incomplete",
  complete: "Complete",
  not_applicable: "Not present in this source",
};

export function renderCoverage(container, coverage, element, onChange) {
  container.replaceChildren(...Object.entries(domains).map(([domain, title]) => {
    const saved = coverage?.domains?.[domain] || {};
    return element("fieldset", {}, [
      element("legend", { text: title }),
      ...Object.entries(fields).map(([field, label]) => element("label", {}, [
        element("span", { text: label }),
        element("select", { dataset: { coverageDomain: domain, coverageField: field } },
          Object.entries(statuses).map(([value, text]) => element("option", {
            text, properties: { value, selected: (saved[field] || "unreviewed") === value },
          }))),
      ])),
      element("label", {}, [
        element("span", { text: "Source locations and reason for these decisions" }),
        element("textarea", {
          dataset: { coverageDomain: domain, coverageField: "evidence" },
          properties: { value: saved.evidence || "", maxLength: 4000 },
        }),
      ]),
    ]);
  }));
  for (const input of container.querySelectorAll("select, textarea")) {
    input.addEventListener("input", onChange);
  }
}

export function readCoverage(container) {
  const result = { domains: {} };
  for (const input of container.querySelectorAll("[data-coverage-domain]")) {
    const { coverageDomain: domain, coverageField: field } = input.dataset;
    result.domains[domain] ||= {};
    result.domains[domain][field] = input.value;
  }
  return result;
}
