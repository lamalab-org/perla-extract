"""Shared semantic rules for concepts used by several model passes.

Keeping these rules in one place prevents the inventory, extraction, refinement, and
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
