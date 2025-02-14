from pydantic import BaseModel, Field, confloat
from typing import List, Literal, Optional


class UnitValue(BaseModel):
    value: Optional[float] = Field(None)
    unit: Optional[str] = Field(None)

class Size(UnitValue):
    value: Optional[confloat(gt=0)] = Field(None)
    unit: Optional[Literal['nm']] = Field(None)

class SurfaceCharge(BaseModel):
    value: Optional[Literal["Negative", "Neutral", "Positive"]] = Field(
        None,
        description="Surface charge of nanoparticles"
    )

class ZetaPotential(UnitValue):
    value: Optional[confloat(ge=-100, le=100)] = Field(None)
    unit: Optional[Literal['mV']] = Field(None)

class Shape(BaseModel):
    value: Optional[Literal["Spherical", "Rod", "Shell", "Cage", "Other"]] = Field(
        None,
        description="Different shapes"
    )

class Composition(BaseModel):
    backbone: Optional[str] = Field(
        None,
        description="The backbone of nanoparticles such as 'graphene oxide'",
    )
    functionalization: Optional[str] = Field(
        None,
        description="The surface coating of nanoparticles such as 'PEGylated'",
    )

class Concentration(UnitValue):
    value: Optional[float] = Field(None)
    unit: Optional[Literal["mg/mL"]] = Field(None)

class Solubility(BaseModel):
    solvent: Optional[Literal["Water", "PBS", "Ethanol", "Other"]] = Field(
        None,
        description="Solvent in which solubility is measured",
    )
    concentration: Optional[Concentration]

class PolymerType(BaseModel):
    value: Optional[Literal["Lipid nanoparticles", "Polymer nanoparticles", "other"]] = Field(
        None,
        description="Different types, extendable"
    )

class TargetTissue(BaseModel):
    value: Optional[Literal["Brain", "Liver", "Lung", "Serum", "Plasma", "other"]] = Field(
        None,
        description="Targeting tissues"
    )

class Nanoparticles(BaseModel):
    size: Optional[Size] = Field(None)
    surface_charge: Optional[SurfaceCharge] = Field(None)
    zeta_potential: Optional[ZetaPotential] = Field(None)
    shape: Optional[Shape] = Field(None)
    composition: Optional[Composition] = Field(None)
    solubility: Optional[Solubility] = Field(None)
    polymer_type: Optional[PolymerType] = Field(None)
    target_tissue: Optional[TargetTissue] = Field(None)

class NanoparticlesUnit(BaseModel):
    Unit: Optional[Nanoparticles] = Field(None)
    