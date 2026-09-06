"""Shared semantic rules for concepts used by several model passes.

Keeping these rules in one place prevents claim collection, extraction, refinement, and
repair calls from assigning subtly different meanings to the same scientific entity.
"""

DEVICE_FAMILY_POLICY = """Device-family boundary:
- A device family is a reusable, complete photovoltaic architecture defined by its
  ordered functional-layer materials, absorber composition or compositions, and
  device polarity or topology.
- Create a separate family when an identity-defining part of that design changes,
  such as the absorber composition, a functional-layer material, or the cell
  topology. A named material that remains in the finished device can therefore
  distinguish a family.
- Do not create another family solely for a fabrication parameter, post-treatment,
  thickness, area, measurement protocol, scan direction, aging condition, champion
  label, or experimental-arm name when the underlying device design is unchanged.
  Preserve a supported specimen-specific difference in
  IndividualDevice.reported_properties. If no record can carry a group-only process
  difference without pretending it describes one specimen, explain it in
  unresolved_notes rather than inventing another family.
- Films, partial stacks, substrates, half-cells, electrodes, and material specimens
  made only for characterization are context, not photovoltaic device families.
  Include one only when the evidence establishes that it is a complete photovoltaic
  device made or measured in the present study.
- When the evidence does not establish whether two labels denote different designs,
  keep the uncertainty explicit instead of guessing a split or merge.
"""

SHARED_QUANTITY_POLICY = """Shared-quantity boundary:
- When the source explicitly gives one quantity to a coordinated list of named
  materials or metrics, emit one atomic ReportedValue for each named item. Give each
  value an item-specific name while preserving the same source-reported raw quantity
  and evidence span. Equal values for different materials are not duplicates.
- Apply a shared value only when the source grammar establishes that scope. Do not
  propagate a value from the first item to the rest of a list using assumed
  stoichiometry, chemical knowledge, or a nearby recipe. If the scope is ambiguous,
  preserve the ambiguity in unresolved_notes instead of guessing.
"""

RECORD_BOUNDARY_POLICY = """Record boundary:
- A top-level record represents a scientific object or reported result, not merely a
  phrase, figure, experimental arm, or available measurement method.
- Create a PopulationStatistic only when the source reports an aggregate,
  distribution, range, extremum, or other summary over multiple devices. Device area,
  a comparison between groups, one best-device value, several individual traces, and
  a statement that several devices were measured are not population statistics by
  themselves.
- Create a PerformanceObservation only when at least one outcome is reported for a
  supported individual device. A spectrum, transient, or characterization technique
  mentioned without a usable outcome is context.
- Create a StabilityTest only for aging or operational stability of a photovoltaic
  device with a reported outcome. A film, layer, or aged material prepared only for
  microscopy, spectroscopy, or depth profiling is a characterization specimen.
- Device area and specimen-specific fabrication settings are device properties. Scan
  direction and measurement protocol distinguish observations. Neither creates a
  population or device family.
- Reconcile repeated mentions globally. Do not create a second record for the same
  device and protocol merely because another passage reports an additional metric.
"""

COMPOSITION_BOUNDARY_POLICY = """Composition and stack boundary:
- A processing solvent, antisolvent, catalyst, cleaning agent, or temporary reagent is
  not a device layer unless the source says that it remains in the finished device.
- Preserve chemical formulae, stoichiometric parentheses, uncertainty intervals, and
  composition ranges as reported. Do not replace a reported range with one guessed
  composition.
- Keep a host absorber, an additive, and a distinct surface treatment or surface phase
  separate when the source distinguishes them. Do not rewrite a surface modifier into
  the bulk absorber formula or fuse two source-described layers into one chemical name.
- Apply a shared stack to variants only when the source explicitly establishes that
  the other layers are shared. When supported, repeat the complete stack rather than
  emitting fragmentary variant families.
- A concentration, dose, processing condition, or treatment label alone does not
  establish a new device family. If the finished functional-layer material identity or
  absorber composition is not shown to differ, preserve the distinction on the
  specimen or as unresolved group-level context instead of multiplying families.
"""

EVIDENCE_INTERPRETATION_POLICY = """Evidence and inference boundary:
- Store source-reported text and table values, including atomic qualitative values
  such as \"over 80%\". Do not turn them into a more precise number.
- Do not digitize curves or estimate coordinates from plots. A figure caption or
  parser-extracted table is usable only for the text or numbers it actually contains.
- Do not fill categorical conditions from customary practice. If scan direction,
  atmosphere, circuit condition, device identity, or another field is only plausible
  or visually inferred, leave the field unknown and explain the unresolved inference.
- A likely shared specimen is not an explicit link. Keep measurements separate when
  the paper does not establish that they were made on the same physical device.
"""
