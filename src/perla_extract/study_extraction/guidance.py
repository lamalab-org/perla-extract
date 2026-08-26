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
