/** Quick entry of J-V and related results: one grid row per performance observation.
 *
 * The grid only fills the existing PerformanceObservation shape. Values are kept as
 * typed (raw_value); a plain number is also stored as value_number. Empty cells are
 * left out, so a row records only what the paper reports.
 */

export const JV_METRICS = [
  { name: "PCE", unit: "%" },
  { name: "Voc", unit: "V" },
  { name: "Jsc", unit: "mA cm-2" },
  { name: "FF", unit: "%" },
  { name: "Rs", unit: "Ω cm2", optional: true },
  { name: "Rsh", unit: "Ω cm2", optional: true },
  { name: "HI", unit: "", optional: true },
];

export const ROW_PRESETS = [
  { label: "+ Reverse scan", measurement_type: "jv_scan", scan_direction: "reverse" },
  { label: "+ Forward scan", measurement_type: "jv_scan", scan_direction: "forward" },
  { label: "+ Stabilized output", measurement_type: "stabilized_power_output", scan_direction: "not_applicable" },
  { label: "+ Certified", measurement_type: "certified_measurement", scan_direction: "not_reported" },
  { label: "+ EQE-integrated Jsc", measurement_type: "eqe_integrated_current", scan_direction: "not_applicable" },
];

const MEASUREMENT_TYPES = ["jv_scan", "stabilized_power_output", "certified_measurement", "eqe_integrated_current", "other", "not_reported"];
const SCAN_DIRECTIONS = ["reverse", "forward", "not_applicable", "not_reported"];

/** The number in a plain numeric cell ("23.5", "−0.8", "1,12", "4,000"); anything else stays
 * raw only. Groups of three digits after a comma are thousands ("4,000" rpm is 4000), unless the
 * number starts with 0 ("0,800" is 0.8); a shorter group is a decimal comma ("1,12" is 1.12).
 */
export function reportedNumber(raw) {
  const text = String(raw ?? "").trim().replace(/[−–]/g, "-");
  if (/^[+-]?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$/.test(text)) return Number(text.replace(/,/g, ""));
  if (!/^[+-]?\d+(?:[.,]\d+)?$/.test(text)) return null;
  return Number(text.replace(",", "."));
}

/** Store a value as the paper prints it with its unit ("1.45M"), when the grounding check
 * found only that spelling. The number stays saved; the unit field is kept.
 */
export function adoptPrintedSpelling(value, printed) {
  if (!printed || printed === value.raw_value) return false;
  if (value.value_number == null) value.value_number = reportedNumber(value.raw_value);
  value.raw_value = printed;
  return true;
}

// Unit spellings the scorer's unit library does not know, mapped to ones it does. The unit
// field is not checked against the quote (the number is), so a standard spelling is safe.
const UNIT_SPELLINGS = new Map([
  ["r.p.m.", "rpm"], ["r.p.m", "rpm"], ["rpm", "rpm"], ["rev/min", "rpm"], ["rev min-1", "rpm"], ["rev min−1", "rpm"],
  ["oc", "°C"], ["ºc", "°C"], ["°c", "°C"], ["torr", "torr"], ["mtorr", "mtorr"],
]);

/** A unit in a spelling the scorer understands; other units are returned as typed. */
export function standardUnit(unit) {
  const text = String(unit ?? "").trim();
  return UNIT_SPELLINGS.get(text.toLowerCase()) ?? text;
}

function freshIdentifier(prefix, used) {
  let suffix = 1;
  while (used.has(`${prefix}-${suffix}`)) suffix += 1;
  used.add(`${prefix}-${suffix}`);
  return `${prefix}-${suffix}`;
}

/** Turn grid rows into PerformanceObservation records; rows without any value are skipped. */
export function observationsFromRows({ rows, deviceId, units, usedIds }) {
  const used = new Set(usedIds);
  return rows
    .map((row) => {
      const metrics = JV_METRICS
        .filter((metric) => String(row.values[metric.name] ?? "").trim())
        .map((metric) => {
          const raw = String(row.values[metric.name]).trim();
          const unit = standardUnit(units[metric.name]);
          return { name: metric.name, raw_value: raw, value_number: reportedNumber(raw), unit: unit || null, evidence: [] };
        });
      return { row, metrics };
    })
    .filter(({ metrics }) => metrics.length)
    .map(({ row, metrics }) => ({
      observation_id: freshIdentifier("observation", used),
      device_id: deviceId,
      measurement_type: row.measurement_type,
      scan_direction: row.scan_direction,
      measurement_conditions: [],
      metrics,
      evidence: [],
    }));
}

const comparable = text => String(text ?? "").normalize("NFKC").replace(/[−–—]/g, "-").replace(/\s+/g, "").toLowerCase();

/** Values that do not occur in the quote; the server would reject those rows. */
export function valuesMissingFromQuote(observations, quote) {
  const source = comparable(quote);
  return observations.flatMap((observation, index) => observation.metrics
    .filter((metric) => !source.includes(comparable(metric.raw_value)))
    .map((metric) => `row ${index + 1} ${metric.name} "${metric.raw_value}"`));
}

/** The grid UI. Evidence selection and saving stay with the caller (app.js). */
export function createJvTable({ document, element, context, humanLabel }) {
  const $ = id => document.getElementById(id);
  let rows = [];
  let units = {};
  let shown = new Set();
  let focusRow = null;

  function devices() {
    const identifier = context().bundle?.summary?.record_identifiers?.individual_devices || "device_id";
    return (context().bundle?.ground_truth?.individual_devices || [])
      .map(device => ({ value: device[identifier], label: device.label || device[identifier] }));
  }

  function select(choices, value, onChange, label) {
    const node = element("select", { attributes: { "aria-label": label } }, choices.map(choice => element("option", {
      text: humanLabel(choice), properties: { value: choice, selected: choice === value },
    })));
    node.addEventListener("change", () => onChange(node.value));
    return node;
  }

  function columns() {
    return JV_METRICS.filter(metric => !metric.optional || shown.has(metric.name));
  }

  function renderColumns() {
    const toggles = JV_METRICS.filter(metric => metric.optional).map((metric) => {
      const box = element("input", { properties: { type: "checkbox", checked: shown.has(metric.name), value: metric.name } });
      box.addEventListener("change", () => {
        if (box.checked) shown.add(metric.name); else shown.delete(metric.name);
        render();
      });
      return element("label", { className: "layer-target" }, [box, element("span", { text: `Show ${metric.name}` })]);
    });
    $("jv-columns").replaceChildren(...toggles);
  }

  function renderRows() {
    const visible = columns();
    const layout = { style: `--jv-columns:${visible.length}` };
    const head = element("div", { className: "jv-row jv-head", attributes: layout }, [
      element("span", { text: "Measurement" }),
      element("span", { text: "Scan" }),
      ...visible.map((metric) => {
        const unit = element("input", {
          properties: { value: units[metric.name] ?? "", placeholder: "unit" },
          attributes: { "aria-label": `${metric.name} unit` },
        });
        unit.id = `jv-unit-${metric.name}`;
        unit.addEventListener("input", () => { units[metric.name] = unit.value; });
        return element("label", { className: "jv-metric-head" }, [element("strong", { text: metric.name }), unit]);
      }),
      element("span", {}),
    ]);
    const body = rows.map((row, index) => element("div", { className: "jv-row", attributes: layout }, [
      select(MEASUREMENT_TYPES, row.measurement_type, (value) => { row.measurement_type = value; }, `Row ${index + 1} measurement`),
      select(SCAN_DIRECTIONS, row.scan_direction, (value) => { row.scan_direction = value; }, `Row ${index + 1} scan direction`),
      ...visible.map((metric) => {
        const cell = element("input", {
          properties: { value: row.values[metric.name] ?? "", placeholder: metric.name },
          attributes: { "aria-label": `Row ${index + 1} ${metric.name}` },
        });
        cell.id = `jv-cell-${index}-${metric.name}`;
        cell.addEventListener("input", () => { row.values[metric.name] = cell.value; });
        return cell;
      }),
      element("button", {
        text: "×", className: "danger-link", properties: { type: "button" },
        attributes: { "aria-label": `Remove row ${index + 1}` },
        events: { click: () => { rows.splice(index, 1); render(); } },
      }),
    ]));
    $("jv-table").replaceChildren(head, ...(body.length ? body : [element("p", { className: "muted", text: "Add a row with the buttons below." })]));
  }

  function renderPresets() {
    $("jv-presets").replaceChildren(...ROW_PRESETS.map(preset => element("button", {
      text: preset.label, properties: { type: "button" },
      events: { click: () => addRow(preset) },
    })));
  }

  function render() {
    renderColumns();
    renderRows();
    if (focusRow != null) {
      $(`jv-cell-${focusRow}-PCE`)?.focus();
      focusRow = null;
    }
  }

  function addRow(preset) {
    rows.push({ measurement_type: preset.measurement_type, scan_direction: preset.scan_direction, values: {} });
    focusRow = rows.length - 1;
    render();
  }

  function open() {
    rows = [];
    units = Object.fromEntries(JV_METRICS.map(metric => [metric.name, metric.unit]));
    shown = new Set();
    const choices = devices();
    $("jv-device").replaceChildren(
      element("option", { text: choices.length ? "Choose the measured device" : "Add the individual device first", properties: { value: "", selected: true, disabled: true } }),
      ...choices.map(choice => element("option", { text: `${choice.label} · ${choice.value}`, properties: { value: choice.value } })),
    );
    $("jv-device").value = "";
    $("jv-citation-block").value = "";
    $("jv-citation-quote").value = "";
    $("jv-evidence-results").replaceChildren();
    $("jv-status").textContent = "";
    renderPresets();
    addRow(ROW_PRESETS[0]);
    const dialog = $("jv-dialog");
    if (!dialog.open) dialog.show();
  }

  /** The records to save, or the reason they cannot be saved yet. */
  function collect() {
    const deviceId = $("jv-device").value;
    const citation = { block_id: $("jv-citation-block").value.trim(), quote: $("jv-citation-quote").value.trim() };
    const identifiers = context().bundle?.summary?.record_identifiers?.performance_observations || "observation_id";
    const usedIds = (context().bundle?.ground_truth?.performance_observations || []).map(item => item[identifiers]);
    const observations = observationsFromRows({ rows, deviceId, units, usedIds });
    const problems = [];
    if (!deviceId) problems.push("Choose the measured device.");
    if (!observations.length) problems.push("Enter at least one value.");
    if (!citation.block_id || !citation.quote) problems.push("Choose an evidence block and provide the exact quote.");
    else {
      const missing = valuesMissingFromQuote(observations, citation.quote);
      if (missing.length) problems.push(`Not found in the quote: ${missing.join(", ")}. Quote the table or sentence that contains them.`);
    }
    return { observations, citation, problems };
  }

  return {
    open,
    collect,
    /** Drop rows that were saved, so a retry after an error does not add them twice. */
    removeSaved(count) {
      let left = count;
      rows = rows.filter((row) => {
        const hasValue = JV_METRICS.some(metric => String(row.values[metric.name] ?? "").trim());
        if (hasValue && left > 0) { left -= 1; return false; }
        return true;
      });
      render();
    },
    get changed() {
      return rows.some(row => Object.values(row.values).some(value => String(value ?? "").trim()));
    },
  };
}
