/** Material names as papers spell them, as three separate suggestion lists.
 *
 * The annotation keeps the paper's wording, so every common spelling is its own option;
 * the browser shows the other spellings of the same material next to it. Lists follow
 * the field: layer materials (stack), constituents (precursors, ions, dopants) and step
 * materials (adds solvents, antisolvents and cleaning agents, which never form a layer).
 * Chosen from the spellings in the reference annotations plus common usage. These are
 * suggestions only; any text can be typed.
 *
 * `group(type, ...materials)`: each material is one spelling or an array of synonyms.
 */
const group = (type, ...materials) => ({ type, materials: materials.map(item => (Array.isArray(item) ? item : [item])) });

const TRANSPARENT = group("transparent electrode",
  ["ITO", "indium tin oxide", "indium-doped tin oxide"], ["FTO", "fluorine-doped tin oxide", "fluorine doped tin oxide"], "IZO", "AZO");
const METALS = group("metal / carbon",
  ["Au", "gold"], ["Ag", "silver"], ["Cu", "copper"], ["Al", "aluminium", "aluminum"], "carbon");
const ETL_OXIDES = group("electron transport oxide", "SnO2", "SnOx", "TiO2", ["ZnO", "ZnO NPs"], "Al2O3", "ZrO2");
const HTL_INORGANIC = group("hole transport (inorganic)", ["NiOx", "NiO"], ["MoO3", "MoOx"], "V2O5", "WO3", "CuSCN", "CuI");
const FULLERENES = group("fullerene / buffer",
  "C60", ["PCBM", "PC61BM", "PC60BM"], "PC71BM", "ICBA", ["BCP", "bathocuproine"], "Bphen", "LiF", "PFN-P2", "PEIE", ["PEI", "polyethylenimine"]);
const HTL_ORGANIC = group("hole transport (organic)",
  ["spiro-OMeTAD", "Spiro-OMeTAD", "spiro-MeOTAD", "Spiro-MeOTAD"], "PTAA", "P3HT", "PEDOT:PSS", "poly-TPD");
const SAMS = group("self-assembled monolayer", "2PACz", "MeO-2PACz", "Me-4PACz", "Br-2PACz");
const LEAD_TIN = group("lead / tin salt", ["PbI2", "lead iodide"], "PbBr2", "PbCl2", "Pb(SCN)2", "Pb(Ac)2", "SnI2", "SnF2");
const ORGANIC_CATIONS = group("organic cation salt",
  ["FAI", "formamidinium iodide"], ["MAI", "CH3NH3I", "methylammonium iodide"], "MABr", ["MACl", "methylammonium chloride"], "FABr", "FACl", "GAI");
const INORGANIC_CATIONS = group("inorganic cation salt", "CsI", "CsBr", "CsCl", "RbI", "RbCl", "KI");
const PASSIVATION = group("2D / passivation salt",
  ["PEAI", "phenethylammonium iodide", "phenylethylammonium iodide"], "PEABr", "PEACl", "BAI", "BABr", "OAI", "GuaBr");
const DOPANTS = group("dopant / additive",
  ["Li-TFSI", "LiTFSI", "lithium bis(trifluoromethanesulfonyl)imide"], ["tBP", "TBP", "4-tert-butylpyridine"], "FK209", "F4-TCNQ", "TEA-TFSI");

export const LAYER_MATERIALS = [
  group("substrate", "glass", "quartz", "PEN", "PET"),
  TRANSPARENT,
  group("electron transport", "SnO2", "SnOx", ["TiO2", "c-TiO2", "compact TiO2", "bl-TiO2"], ["mp-TiO2", "mesoporous TiO2", "m-TiO2"], ["ZnO", "ZnO NPs"], "Al2O3"),
  HTL_INORGANIC,
  FULLERENES,
  HTL_ORGANIC,
  SAMS,
  group("absorber", "perovskite", ["MAPbI3", "CH3NH3PbI3"], "FAPbI3", "CsPbI3", "CsPbI2Br", "CsPbBr3", ["MAPbI3-xClx", "CH3NH3PbI3-xClx"]),
  group("interlayer / passivation", "PEAI", "PEABr", "PEACl", "BAI", "OAI", "PMMA", "PCBM:PMMA"),
  METALS,
  group("encapsulation", "epoxy", "UV epoxy", "cover glass"),
];

export const CONSTITUENTS = [
  group("A-site ion", "FA", "MA", "Cs", "Rb", "K", "GA", "DMA"),
  group("B-site ion", "Pb", "Sn"),
  group("X-site ion", "I", "Br", "Cl"),
  LEAD_TIN, ORGANIC_CATIONS, INORGANIC_CATIONS, PASSIVATION, DOPANTS,
  group("host / polymer", ["spiro-OMeTAD", "Spiro-OMeTAD", "spiro-MeOTAD"], "PTAA", "P3HT", "PEDOT", "PSS", "PCBM", "PMMA", "PEG"),
  TRANSPARENT, ETL_OXIDES, HTL_INORGANIC, METALS,
  group("other layer material", "glass", "C60", "BCP"),
];

export const PROCESS_MATERIALS = [
  group("solvent",
    ["DMF", "N,N-dimethylformamide"], ["DMSO", "dimethyl sulfoxide", "dimethylsulfoxide"], ["GBL", "γ-butyrolactone"], "NMP",
    ["2-ME", "2-methoxyethanol"], ["acetonitrile", "ACN"], "methanol", ["ethanol", "EtOH"],
    ["IPA", "isopropanol", "2-propanol", "isopropyl alcohol"], "1-butanol", "chloroform"),
  group("solvent / antisolvent",
    ["chlorobenzene", "CB"], "toluene", "diethyl ether", "ethyl acetate", "methyl acetate", "anisole", ["dichlorobenzene", "DCB"], "trifluorotoluene"),
  group("cleaning", ["deionized water", "DI water"], "ultrapure water", "water", "acetone", "detergent", ["Hellmanex", "Hellmanex III"], "soapy water"),
  group("gas", ["nitrogen", "N2"], "air", "oxygen"),
  LEAD_TIN, ORGANIC_CATIONS, INORGANIC_CATIONS, PASSIVATION, DOPANTS,
  group("oxide precursor",
    "titanium diisopropoxide bis(acetylacetonate)", ["titanium isopropoxide", "TTIP"], "TiCl4", "TiO2 paste",
    "SnO2 colloid", "SnO2 nanoparticles", "SnCl2·2H2O", "nickel acetate", "ethanolamine", "acetylacetone", "HCl"),
  FULLERENES, HTL_ORGANIC, SAMS,
  group("evaporated metal / oxide", ["Au", "gold"], ["Ag", "silver"], ["Cu", "copper"], "Al", ["MoO3", "MoOx"]),
];

export const SUGGESTION_LISTS = {
  layer: { id: "layer-material-suggestions", groups: LAYER_MATERIALS },
  constituent: { id: "constituent-suggestions", groups: CONSTITUENTS },
  process: { id: "process-material-suggestions", groups: PROCESS_MATERIALS },
};

/** One option per spelling; the first group naming a spelling decides its hint. */
export function suggestionOptions(groups) {
  const seen = new Set();
  const options = [];
  for (const { type, materials } of groups) {
    for (const spellings of materials) {
      for (const name of spellings) {
        if (seen.has(name)) continue;
        seen.add(name);
        const others = spellings.filter(other => other !== name);
        options.push({ value: name, label: others.length ? `${type} · also written ${others.join(", ")}` : type });
      }
    }
  }
  return options;
}

/** Fill the page's three datalists once. */
export function fillMaterialDatalists(document, element) {
  for (const { id, groups } of Object.values(SUGGESTION_LISTS)) {
    const list = document.getElementById(id);
    if (!list || list.children?.length) continue;
    list.replaceChildren(...suggestionOptions(groups).map(option => element("option", { properties: option })));
  }
}
