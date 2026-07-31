from pint import UnitRegistry
import re
from platformdirs import user_data_dir
from pathlib import Path

max_retries = 3
# Initialize the UnitRegistry
ureg = UnitRegistry()

ureg.define("sun = 1 kW/m^2")

pint = {
    "default_units_by_type": {
        ureg.percent.dimensionality: (ureg.percent, "%"),  # Efficiency, humidity, etc.
        (ureg.ampere / (ureg.centimeter**2)).dimensionality: (
            ureg.milliampere / (ureg.centimeter**2),
            "mA cm^-2",
        ),  # Current density
        ureg.volt.dimensionality: (ureg.volt, "V"),  # Voltage
        ureg.nanometer.dimensionality: (ureg.nanometer, "nm"),  # Thickness,
        (ureg.meter**2).dimensionality: (ureg.centimeter**2, "cm^2"),
        ureg.day.dimensionality: (
            ureg.second,
            "s",
        ),  # Time (converted to hours for finer granularity)
        ureg.celsius.dimensionality: (
            ureg.celsius,
            "°C",
        ),  # Temperature converted from Celsius
        (1 * ureg.mg / ureg.mL).dimensionality: (ureg.mg / ureg.mL, "mg/mL"),
        (ureg.mW / ureg.cm**2).dimensionality: ((ureg.mW / ureg.cm**2), "mW cm^-2"),
        (ureg.mW / ureg.cm**2).dimensionality: ((ureg.mW / ureg.cm**2), "mW cm^-2"),
        (ureg.mol / ureg.L).dimensionality: ((ureg.mol / ureg.L), "mol/L"),
        (ureg.eV).dimensionality: ((ureg.eV), "eV"),
        (ureg.meter**3).dimensionality: (ureg.milliliter, "mL"),
    }
}

default_units = {
    "thickness": "nm",
    "light_intensity": "mW cm^-2",
    "duration": "s",
    "temperature": "°C",
    "time": "h",
    "PCE_after_1000_hours": "%",
    "humidity": "%",
    "PCE_at_the_start_of_the_experiment": "%",
    "PCE_at_the_end_of_description": "%",
    "PCE_T80": "%",
    "bandgap": "eV",
    "concentration": "mol/L",
    "volume": "mL",
}


papersbot_runs_path = Path(user_data_dir("papersbot_run","perla-extractor")+"/runs")


retry_dates = [30, 90, 180, 360]  # days

RELAXED_REGEX = re.compile(
    r"""
    # CONDITION 1: Must contain a perovskite-related term
    (?=.*\b(?:
        perovskite[s]?|
        PSC[s]?|
        halide|
        CsPb|
        MAPb|
        FAPb|
        CH3NH3|
        formamidinium
    )\b)

    # CONDITION 2: Must ALSO contain a solar-cell-related term
    (?=.*(?:
        # --- List of all word-based terms from all 3 regexes ---
        \b(?:
            # Solar/PV terms
            solar[\s-]?cell[s]?|
            photovoltaic[s]?|
            PV|
            solar|

            # Performance Metrics
            efficiency|
            PCE|
            power[\s-]conversion[\s-]efficiency|
            energy[\s-]conversion|
            conversion|
            performance|
            V(?:OC|oc)|
            open[\s-]circuit[\s-]voltage|
            J(?:SC|sc)|
            short[\s-]circuit[\s-]current|
            fill[\s-]factor|
            FF|
            quantum[\s-]efficiency|
            EQE|
            IPCE|
            hysteresis|

            # Device/Material terms
            device[s]?|
            cell[s]?|
            hole[\s-]transport|
            electron[\s-]transport|
            HTL|
            ETL|
            absorber|
            active[\s-]layer|
            photoactive|

            # Electrical terms
            conductivity|
            carrier|
            charge|
            recombination|
            extraction|
            transport|
            energy|
            power|
            voltage|
            current|

            # Stability/Environmental terms
            stability|
            lifetime|
            degradation|
            aging|
            operational|
            moisture|
            thermal|
            illumination|
            light|
            AM1\.5|
            sun|

            # Processing terms
            fabrication|
            processing|
            preparation|
            synthesis|

            # Application terms
            commercialization|
            applications?
        )\b|

        # --- List of all numeric/unit-based terms from all 3 regexes ---
        [\d]+(?:\.\d*)?[\s]*%|                    # Percentage (e.g., 25.5%)
        [\d]+(?:\.\d*)?[\s]*mA[\/\s]*cm[2²]?|    # Current density (e.g., 22 mA/cm2)
        [\d]+(?:\.\d*)?[\s]*V|                    # Voltage (e.g., 1.1 V)
        (?:[\d]+(?:\.\d*)?[\s]*)?mW(?:[\/\s]*cm)?| # Power density (e.g., 100 mW/cm or mW)
        (?:[\d]+(?:\.\d*)?[\s]*)?W[\/\s]*g        # Power-to-weight (e.g., 10 W/g)
    ))
    
    # If both conditions are met, match the entire string
    .*?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# This is the regular expression that selects the papers of interest
STRICT_REGEX = re.compile(
    r"""
  (
    # Perovskite cell variations
    \b(perovskite(?:[\s-](?:solar|photovoltaic|PV))?(?:\s*cell[s]?|\s*device[s]?)|PSC[s]?)\b
    # Single junction specific terms
    |single[\s-]?(?:junction|layer|absorber|heterojunction|stack)
    # Architecture variations for single junction
    |(?:planar|mesoscopic|inverted|flexible|rigid|printable)[\s-]?perovskite
    # Exclude explicit mentions of tandem/multi-junction
    (?<!tandem[\s-])(?<!multi[\s-])(?<!double[\s-])(?<!triple[\s-])
  )
  .*?
  (
    # Performance metrics
    \b(?:efficiency|PCE|power[\s-]conversion[\s-]efficiency
    |V(?:OC|oc)|open[\s-]circuit[\s-]voltage
    |J(?:SC|sc)|short[\s-]circuit[\s-]current(?:[\s-]density)?
    |fill[\s-]factor|FF
    |stability|lifetime|degradation|performance
    |I[- ]?V(?:[\s-]curve)?|J[- ]?V(?:[\s-]curve)?|current[\s-](?:voltage|density)
    |hysteresis|quantum[\s-]efficiency|(?:internal|external)[\s-]quantum[\s-]efficiency|(?:IPCE|EQE)
    |[\d]+(?:\.\d+)?%|[\d]+(?:\.\d+)?[\s]?mA\/cm2|[\d]+(?:\.\d+)?[\s]?V)\b
  )
""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)