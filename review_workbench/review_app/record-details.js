// Record contents shown before a reviewer accepts or corrects a record.
// Source navigation and DOM creation are supplied by the application; these
// renderers preserve the owning record and nested path of every reported value.
export function renderSubjectContext(entry, context, path, ui, title = "Specimen treatment") {
  const { element, contextField, humanLabel, reportedValueGroup } = ui;
  return element("section", { className: "reported-value-group" }, [
    element("h5", { text: title }),
    ...(context ? [
      element("div", { className: "record-facts" }, [
        contextField("Treatment stage", humanLabel(context.treatment_stage)),
        contextField("Treated material", context.treated_material || "Not reported"),
      ]),
      reportedValueGroup(entry, "Treatment conditions", context.conditions || [], [...path, "conditions"]),
    ] : [element("p", { className: "muted", text: "Not reported" })]),
  ]);
}

function renderStabilityRecord(entry, ui) {
  const { element, contextField, humanLabel, reportedValueGroup, identifiers } = ui;
  const test = entry.item;
  const checkpoints = test.checkpoints || [];
  const familyField = identifiers.device_families;
  const deviceField = identifiers.individual_devices;
  return [
    element("div", { className: "record-facts" }, [
      contextField("Aged specimen", test.specimen_label),
      contextField("Identity link", humanLabel(test.link_status)),
      contextField("Linked device", test[deviceField] || "No device link"),
      contextField("Linked family", test[familyField] || "No family link"),
    ]),
    reportedValueGroup(entry, "Test-wide conditions", test.conditions || [], ["conditions"]),
    element("section", { className: "checkpoint-list" }, [
      element("h4", { text: `Checkpoints (${checkpoints.length})` }),
      ...checkpoints.map((checkpoint, checkpointIndex) => element("details", {
        className: "stability-checkpoint",
        properties: { open: checkpointIndex === 0 },
      }, [
        element("summary", { text: `${checkpoint.checkpoint_id}${checkpoint.time ? ` · ${checkpoint.time.raw_value}` : " · time not reported"}` }),
        element("div", { className: "checkpoint-content" }, [
          reportedValueGroup(entry, "Time", checkpoint.time ? [checkpoint.time] : [], ["checkpoints", checkpointIndex, "time"], "Time not reported", false),
          reportedValueGroup(entry, "Checkpoint-specific conditions", checkpoint.conditions || [], ["checkpoints", checkpointIndex, "conditions"]),
          reportedValueGroup(entry, "Outcomes", checkpoint.outcomes || [], ["checkpoints", checkpointIndex, "outcomes"], "No outcome extracted"),
        ]),
      ])),
    ]),
  ];
}


export function renderRecordDetails(entry, ui) {
  const { element, contextField, humanLabel, reportedValueGroup, identifiers } = ui;
  const item = entry.item;
  if (entry.kind === "stability_tests") return renderStabilityRecord(entry, ui);
  if (entry.kind === "individual_devices") return [
    renderSubjectContext(entry, item.subject_context, ["subject_context"], ui),
    reportedValueGroup(entry, "Specimen-specific properties", item.reported_properties || [], ["reported_properties"]),
  ];
  if (entry.kind === "performance_observations") return [
    element("div", { className: "record-facts" }, [
      contextField("Measurement type", humanLabel(item.measurement_type)),
      contextField("Scan direction", humanLabel(item.scan_direction)),
      contextField("Linked device", item[identifiers.individual_devices]),
    ]),
    reportedValueGroup(entry, "Measurement conditions", item.measurement_conditions || [], ["measurement_conditions"]),
    reportedValueGroup(entry, "Performance metrics", item.metrics || [], ["metrics"]),
  ];
  if (entry.kind === "population_statistics") return [
    element("div", { className: "record-facts" }, [
      contextField("Statistic", humanLabel(item.statistic_type)),
      contextField("Error bars", humanLabel(item.error_bar_type)),
      contextField("Sample size", item.sample_size == null ? "Not reported" : String(item.sample_size)),
      contextField("Measurement type", humanLabel(item.measurement_type)),
      contextField("Scan direction", humanLabel(item.scan_direction)),
      contextField("Linked family", item[identifiers.device_families] || "No family link"),
    ]),
    ...((item.subjects || []).length
      ? item.subjects.map((subject, index) => renderSubjectContext(entry, subject, ["subjects", index], ui, `Cohort treatment ${index + 1}`))
      : [element("p", { className: "muted", text: "Cohort treatments: not reported" })]),
    reportedValueGroup(entry, "Measurement conditions", item.measurement_conditions || [], ["measurement_conditions"]),
    reportedValueGroup(entry, "Aggregate metrics", item.metrics || [], ["metrics"]),
  ];
  return [];
}
