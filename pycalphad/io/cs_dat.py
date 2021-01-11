"""
Support for reading ChemSage DAT files.

This implementation is based on a careful reading of Thermochimica source code (with some trial-and-error).
Thermochimica is software written by M. H. A. Piro and released under the BSD License.

Careful of a gotcha:  `obj.setParseAction` modifies the object in place but calling it a name makes a new object:

`obj = Word(nums); obj is obj.setParseAction(lambda t: t)` is True
`obj = Word(nums); obj is obj('abc').setParseAction(lambda t: t)` is False



"""

import numpy as np
from dataclasses import dataclass
from pycalphad import Database
from collections import deque


class TokenParser(deque):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token_index = 0

    def parse(self, cls: type):
        self.token_index += 1
        return cls(self.popleft())

    def parseN(self, N: int, cls: type):
        if N < 1:
            raise ValueError(f'N must be >=1, got {N}')
        return [self.parse(cls) for _ in range(N)]


@dataclass
class Header:
    list_soln_species_count: [int]
    num_stoich_phases: int
    pure_elements: [str]
    pure_elements_mass: [float]
    gibbs_coefficient_idxs: [int]
    excess_coefficient_idxs: [int]


@dataclass
class AdditionalCoefficientPair:
    coefficient: float
    exponent: float


@dataclass
class Interval:
    temperature: float
    coefficients: [float]
    additional_coeff_pairs: [AdditionalCoefficientPair]


@dataclass
class IntervalFixedCP:
    # Fixed term heat capacity interval
    temperature: float
    H298: float
    S298: float
    CP_coefficients: float
    H_trans: float
    additional_coeff_pairs: [AdditionalCoefficientPair]


@dataclass
class Quadruplet:
    quadruplet_idxs: [int]  # exactly four
    quadruplet_coordinations: [float]  # exactly four


@dataclass
class ExcessQuadruplet:
    mixing_type: int
    mixing_code: str
    mixing_const: [int]  # exactly four
    mixing_exponents: [int]  # exactly four
    junk: [float]  # exactly twelve
    additional_mixing_const: int
    additional_mixing_exponent: int
    excess_coeffs: [float]


@dataclass
class Endmember:
    species_name: str
    gibbs_eq_type: str
    stoichiometry_pure_elements: [float]
    intervals: [Interval]


@dataclass
class EndmemberMagnetic(Endmember):
    curie_temperature: float
    magnetic_moment: float


@dataclass
class EndmemberRealGas(Endmember):
    # Tsonopoulos data
    Tc: float
    Pc: float
    Vc: float
    acentric_factor: float
    dipole_moment: float


@dataclass
class EndmemberAqueous(Endmember):
    charge: float


@dataclass
class EndmemberSUBQ(Endmember):
    stoichiometry_quadruplet: [float]
    zeta: float


@dataclass
class ExcessCEF:
    interacting_species_idxs: [int]
    parameter_order: int
    parameters: [float]


@dataclass
class ExcessCEFMagnetic:
    interacting_species_idxs: [int]
    parameter_order: int
    curie_temperature: float
    magnetic_moment: float


@dataclass
class ExcessQKTO:
    interacting_species_idxs: [int]
    interacing_species_type: [int]
    coefficients: [float]


@dataclass
class _Phase:
    phase_name: str
    phase_type: str
    endmembers: [Endmember]


@dataclass
class Phase_RealGas(_Phase):
    endmembers: [EndmemberRealGas]


@dataclass
class Phase_Stoichiometric:
    phase_name: str
    phase_type: str
    endmembers: [Endmember]  # exactly one


@dataclass
class Phase_Aqueous(_Phase):
    endmembers: [EndmemberAqueous]


@dataclass
class Phase_CEF(_Phase):
    subl_ratios: [float]
    excess_parameters: [ExcessCEF]


@dataclass
class Phase_SUBQ(_Phase):
    num_pairs: int
    num_quadruplets: int
    num_subl_1_const: int
    num_subl_2_const: int
    subl_1_const: [str]
    subl_2_const: [str]
    subl_1_charges: [float]
    subl_1_chemical_groups: [int]
    subl_2_charges: [float]
    subl_2_chemical_groups: [int]
    subl_const_idx_pairs: [(int,)]
    quadruplets: [Quadruplet]
    excess_parameters: [ExcessQuadruplet]


def tokenize(instring, startline=0):
    return TokenParser('\n'.join(instring.splitlines()[startline:]).split())


def parse_header(toks: TokenParser) -> Header:
    num_pure_elements = toks.parse(int)
    num_soln_phases = toks.parse(int)
    list_soln_species_count = toks.parseN(num_soln_phases, int)
    num_stoich_phases = toks.parse(int)
    pure_elements = toks.parseN(num_pure_elements, str)
    pure_elements_mass = toks.parseN(num_pure_elements, float)
    num_gibbs_coeffs = toks.parse(int)
    gibbs_coefficient_idxs = toks.parseN(num_gibbs_coeffs, int)
    num_excess_coeffs = toks.parse(int)
    excess_coefficient_idxs = toks.parseN(num_excess_coeffs, int)
    header = Header(list_soln_species_count, num_stoich_phases, pure_elements, pure_elements_mass, gibbs_coefficient_idxs, excess_coefficient_idxs)
    return header


def parse_additional_terms(toks: TokenParser):
    num_additional_terms = toks.parse(int)
    return [AdditionalCoefficientPair(*toks.parseN(2, float)) for _ in range(num_additional_terms)]


def parse_interval(toks: TokenParser, num_gibbs_coeffs, has_additional_terms) -> Interval:
    temperature = toks.parse(float)
    coefficients = toks.parseN(num_gibbs_coeffs, float)
    if has_additional_terms:
        additional_coeff_pairs = parse_additional_terms(toks)
    else:
        additional_coeff_pairs = []
    return Interval(temperature, coefficients, additional_coeff_pairs)


def parse_interval_fixed_heat_capacity(toks: TokenParser, num_gibbs_coeffs, H298, S298, has_H_trans=False, has_additional_terms=False) -> IntervalFixedCP:
    # 6 coefficients are required
    assert num_gibbs_coeffs == 6
    if has_H_trans:
        H_trans = toks.parse(float)
    else:
        H_trans = 0.0  # 0.0 will be added to the first (or only) interval
    temperature = toks.parse(float)
    CP_coefficients = toks.parseN(4, float)
    if has_additional_terms:
        additional_coeff_pairs = parse_additional_terms(toks)
    else:
        additional_coeff_pairs = []
    return IntervalFixedCP(temperature, H298, S298, CP_coefficients, H_trans, additional_coeff_pairs)


def parse_endmember(toks: TokenParser, num_pure_elements, num_gibbs_coeffs, is_stoichiometric=False):
    species_name = toks.parse(str)
    if toks[0] == '#':
        # special case for stoichiometric phases, this is a dummy species, skip it
        _ = toks.parse(str)
    gibbs_eq_type = toks.parse(int)
    has_magnetic = gibbs_eq_type > 12
    gibbs_eq_type_reduced = (gibbs_eq_type - 12) if has_magnetic else gibbs_eq_type
    has_additional_terms = gibbs_eq_type_reduced in (4,)
    num_intervals = toks.parse(int)
    stoichiometry_pure_elements = toks.parseN(num_pure_elements, float)
    # Piecewise endmember energy intervals
    if gibbs_eq_type_reduced in (1, 4):
        intervals = [parse_interval(toks, num_gibbs_coeffs, has_additional_terms) for _ in range(num_intervals)]
    elif gibbs_eq_type_reduced in (7,):
        H298 = toks.parse(float)
        S298 = toks.parse(float)
        # parse the first without H_trans, then parse the rest
        intervals = [parse_interval_fixed_heat_capacity(toks, num_gibbs_coeffs, H298, S298, has_H_trans=False)]
        for _ in range(num_intervals - 1):
            intervals.append(parse_interval_fixed_heat_capacity(toks, num_gibbs_coeffs, H298, S298, has_H_trans=True))
    else:
        raise ValueError(f"Gibbs equation type {gibbs_eq_type} is not yet supported. Expected one of (1, 4, 7, 13, 16, 19).")
    # magnetic terms
    if has_magnetic:
        curie_temperature = toks.parse(float)
        magnetic_moment = toks.parse(float)
        if is_stoichiometric:
            # two more terms
            # TODO: not clear what these are for, throwing them out for now.
            toks.parse(float)
            toks.parse(float)
        return EndmemberMagnetic(species_name, gibbs_eq_type, stoichiometry_pure_elements, intervals, curie_temperature, magnetic_moment)
    return Endmember(species_name, gibbs_eq_type, stoichiometry_pure_elements, intervals)


def parse_endmember_aqueous(toks: TokenParser, num_pure_elements: int, num_gibbs_coeffs: int):
    # add an extra "pure element" to parse the charge
    em = parse_endmember(toks, num_pure_elements + 1, num_gibbs_coeffs)
    return EndmemberAqueous(em.species_name, em.gibbs_eq_type, em.stoichiometry_pure_elements[1:], em.intervals, em.stoichiometry_pure_elements[0])


def parse_endmember_subq(toks: TokenParser, num_pure_elements, num_gibbs_coeffs, zeta=None):
    em = parse_endmember(toks, num_pure_elements, num_gibbs_coeffs)
    # TODO: is 5 correct? I only have two SUBQ/SUBG databases and they seem equivalent
    # I think the first four are the actual stoichiometries of each element in the quadruplet, but I'm unclear.
    stoichiometry_quadruplet = toks.parseN(5, float)
    if zeta is None:
        # This is SUBQ we need to parse it. If zeta is passed, that means we're in SUBG mode
        zeta = toks.parse(float)
    return EndmemberSUBQ(em.species_name, em.gibbs_eq_type, em.stoichiometry_pure_elements, em.intervals, stoichiometry_quadruplet, zeta)


def parse_quadruplet(toks):
    quad_idx = toks.parseN(4, int)
    quad_coords = toks.parseN(4, float)
    return Quadruplet(quad_idx, quad_coords)


def parse_subq_excess(toks, mixing_type, num_excess_coeffs):
    mixing_code = toks.parse(str)
    mixing_const = toks.parseN(4, int)
    mixing_exponents = toks.parseN(4, int)
    junk = toks.parseN(12, float)
    additional_mixing_const = toks.parse(int)
    additional_mixing_exponent = toks.parse(float)
    excess_coeffs = toks.parseN(num_excess_coeffs, float)
    return ExcessQuadruplet(mixing_type, mixing_code, mixing_const, mixing_exponents, junk, additional_mixing_const, additional_mixing_exponent, excess_coeffs)


def parse_phase_subq(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs):
    if phase_type == 'SUBG':
        zeta = toks.parse(float)
    else:
        zeta = None
    num_pairs = toks.parse(int)
    num_quadruplets = toks.parse(int)
    endmembers = [parse_endmember_subq(toks, num_pure_elements, num_gibbs_coeffs, zeta=zeta) for _ in range(num_pairs)]
    num_subl_1_const = toks.parse(int)
    num_subl_2_const = toks.parse(int)
    subl_1_const = toks.parseN(num_subl_1_const, str)
    subl_2_const = toks.parseN(num_subl_2_const, str)
    subl_1_charges = toks.parseN(num_subl_1_const, float)
    subl_1_chemical_groups = toks.parseN(num_subl_1_const, int)
    subl_2_charges = toks.parseN(num_subl_2_const, float)
    subl_2_chemical_groups = toks.parseN(num_subl_2_const, int)
    # TODO: not exactly sure my math is right on how many pairs, but I think it should be cations*anions
    subl_1_pair_idx = toks.parseN(num_subl_1_const*num_subl_2_const, int)
    subl_2_pair_idx = toks.parseN(num_subl_1_const*num_subl_2_const, int)
    subl_const_idx_pairs = [(s1i, s2i) for s1i, s2i in zip(subl_1_pair_idx, subl_2_pair_idx)]
    quadruplets = [parse_quadruplet(toks) for _ in range(num_quadruplets)]
    excess_parameters = []
    while True:
        mixing_type = toks.parse(int)
        if mixing_type == 0:
            break
        elif mixing_type == -9:
            # some garbage, like 1 2 3K 1 2K 1 3K 2 3 6, 90 of them
            toks.parseN(90, str)
            break
        excess_parameters.append(parse_subq_excess(toks, mixing_type, num_excess_coeffs))

    return Phase_SUBQ(phase_name, phase_type, endmembers, num_pairs, num_quadruplets, num_subl_1_const, num_subl_2_const, subl_1_const, subl_2_const, subl_1_charges, subl_1_chemical_groups, subl_2_charges, subl_2_chemical_groups, subl_const_idx_pairs, quadruplets, excess_parameters)


def parse_excess_magnetic_parameters(toks):
    excess_terms = []
    while True:
        num_interacting_species = toks.parse(int)
        if num_interacting_species == 0:
            break
        interacting_species_idxs = toks.parseN(num_interacting_species, int)
        num_terms = toks.parse(int)
        for parameter_order in range(num_terms):
            curie_temperature = toks.parse(float)
            magnetic_moment = toks.parse(float)
            excess_terms.append(ExcessCEFMagnetic(interacting_species_idxs, parameter_order, curie_temperature, magnetic_moment))
    return excess_terms


def parse_excess_parameters(toks, num_excess_coeffs):
    excess_terms = []
    while True:
        num_interacting_species = toks.parse(int)
        if num_interacting_species == 0:
            break
        interacting_species_idxs = toks.parseN(num_interacting_species, int)
        num_terms = toks.parse(int)
        for parameter_order in range(num_terms):
            excess_terms.append(ExcessCEF(interacting_species_idxs, parameter_order, toks.parseN(num_excess_coeffs, float)))
    return excess_terms


def parse_excess_parameters_pitz(toks, num_excess_coeffs):
    excess_terms = []
    while True:
        num_interacting_species = toks.parse(int)
        if num_interacting_species == 0:
            break
        # TODO: check if this is correct for this model
        # there are always 3 ints, regardless of the above "number of interacting species", if the number of interactings species is 2, we'll just throw the third number away for now
        if num_interacting_species == 2:
            interacting_species_idxs = toks.parseN(num_interacting_species, int)
            toks.parse(int)
        elif num_interacting_species == 3:
            interacting_species_idxs = toks.parseN(num_interacting_species, int)
        else:
            raise ValueError(f"Invalid number of interacting species for Pitzer model, got {num_interacting_species} (expected 2 or 3).")
        # TODO: not sure exactly if this value is parameter order, but it seems to be something like that
        parameter_order = None
        excess_terms.append(ExcessCEF(interacting_species_idxs, parameter_order, toks.parseN(num_excess_coeffs, float)))
    return excess_terms


def parse_excess_qkto(toks, num_excess_coeffs):
    excess_terms = []
    while True:
        num_interacting_species = toks.parse(int)
        if num_interacting_species == 0:
            break
        interacting_species_idxs = toks.parseN(num_interacting_species, int)
        interacing_species_type = toks.parseN(num_interacting_species, int)  # species type, for identify the asymmetry
        coefficients = toks.parseN(num_excess_coeffs, float)
        excess_terms.append(ExcessQKTO(interacting_species_idxs, interacing_species_type, coefficients))
    return excess_terms


def parse_phase_cef(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs, num_const):
    magnetic_phase_type = len(phase_type) == 5 and phase_type[4] == 'M'
    if magnetic_phase_type:
        # ignore first two numbers, these don't seem to be meaningful and always come in 2 regardless of # of sublattices
        # TODO: these are magnetic, not garbage
        toks.parseN(2, float)
    endmembers = []
    for _ in range(num_const):
        if phase_type == 'PITZ':
            endmembers.append(parse_endmember_aqueous(toks, num_pure_elements, num_gibbs_coeffs))
        else:
            endmembers.append(parse_endmember(toks, num_pure_elements, num_gibbs_coeffs))
        if phase_type in ('QKTO',):
            # TODO: is this correct?
            # there's two additional numbers. they seem to always be 1.000 and 1, so we'll just throw them away
            toks.parse(float)
            toks.parse(int)

    # defining sublattice model
    if phase_type in ('SUBL', 'SUBLM'):
        num_subl = toks.parse(int)
        subl_atom_fracs = toks.parseN(num_subl, float)
        # some phases have number of atoms after a colon in the phase name, e.g. SIGMA:30
        if len(phase_name.split(':')) > 1:
            num_atoms = float(phase_name.split(':')[1])
        else:
            num_atoms = 1.0
        subl_ratios = [num_atoms*subl_frac for subl_frac in subl_atom_fracs]
        # read the data used to recover the mass, it's redundant and doesn't need to be stored
        subl_constituents = toks.parseN(num_subl, int)
        num_species = sum(subl_constituents)
        _ = toks.parseN(num_species, str)
        num_endmembers = int(np.prod(subl_constituents))
        for _ in range(num_subl):
            _ = toks.parseN(num_endmembers, int)
    elif phase_type in ('IDMX', 'RKMP', 'QKTO', 'RKMPM', 'PITZ'):
        subl_ratios = [1.0]
    else:
        raise NotImplemented(f"Phase type {phase_type} does not have method defined for determing the sublattice ratios")

    # excess terms
    if phase_type in ('IDMX',):
        # No excess parameters
        excess_parameters = []
    elif phase_type in ('PITZ',):
        excess_parameters = parse_excess_parameters_pitz(toks, num_excess_coeffs)
    elif phase_type in ('RKMP', 'SUBLM', 'RKMPM', 'SUBL'):
        # SUBL will have no excess parameters, but it will have the "0" terminator like it has excess parameters so we can use the excess parameter parsing to process it all the same.
        excess_parameters = []
        if magnetic_phase_type:
            excess_parameters.extend(parse_excess_magnetic_parameters(toks))
        excess_parameters.extend(parse_excess_parameters(toks, num_excess_coeffs))
    elif phase_type in ('QKTO',):
        excess_parameters = parse_excess_qkto(toks, num_excess_coeffs)
    return Phase_CEF(phase_name, phase_type, endmembers, subl_ratios, excess_parameters)


def parse_phase_real_gas(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_const):
    endmembers = []
    for _ in range(num_const):
        em = parse_endmember(toks, num_pure_elements, num_gibbs_coeffs)
        Tc = toks.parse(float)
        Pc = toks.parse(float)
        Vc = toks.parse(float)
        acentric_factor = toks.parse(float)
        dipole_moment = toks.parse(float)
        endmembers.append(EndmemberRealGas(em.species_name, em.gibbs_eq_type, em.stoichiometry_pure_elements, em.intervals, Tc, Pc, Vc, acentric_factor, dipole_moment))
    return Phase_RealGas(phase_name, phase_type, endmembers)


def parse_phase_aqueous(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_const):
    endmembers = [parse_endmember_aqueous(toks, num_pure_elements, num_gibbs_coeffs) for _ in range(num_const)]
    return Phase_Aqueous(phase_name, phase_type, endmembers)


def parse_phase(toks, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs, num_const):
    """Dispatches to the correct parser depending on the phase type"""
    phase_name = toks.parse(str)
    phase_type = toks.parse(str)
    if phase_type in ('SUBQ', 'SUBG'):
        phase = parse_phase_subq(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs)
    elif phase_type == 'IDVD':
        phase = parse_phase_real_gas(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_const)
    elif phase_type == 'IDWZ':
        phase = parse_phase_aqueous(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_const)
    elif phase_type in ('IDMX', 'RKMP', 'RKMPM', 'QKTO', 'SUBL', 'SUBLM', 'PITZ'):
        # all these phases parse the same
        phase = parse_phase_cef(toks, phase_name, phase_type, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs, num_const)
    else:
        raise NotImplementedError(f"phase type {phase_type} not yet supported")
    return phase


def parse_stoich_phase(toks, num_pure_elements, num_gibbs_coeffs):
    endmember = parse_endmember(toks, num_pure_elements, num_gibbs_coeffs, is_stoichiometric=True)
    phase_name = endmember.species_name
    return Phase_Stoichiometric(phase_name, None, [endmember])


def parse_cs_dat(instring):
    toks = tokenize(instring, startline=1)
    header = parse_header(toks)
    num_pure_elements = len(header.pure_elements)
    num_gibbs_coeffs = len(header.gibbs_coefficient_idxs)
    num_excess_coeffs = len(header.excess_coefficient_idxs)
    # num_const = 0 is gas phase that isn't present, so skip it
    solution_phases = [parse_phase(toks, num_pure_elements, num_gibbs_coeffs, num_excess_coeffs, num_const) for num_const in header.list_soln_species_count if num_const != 0]
    stoichiometric_phases = [parse_stoich_phase(toks, num_pure_elements, num_gibbs_coeffs) for _ in range(header.num_stoich_phases)]
    # TODO: better handling and validation of this
    remaining_tokens = ' '.join(toks)
    # print(remaining_tokens)
    return header, solution_phases, stoichiometric_phases, toks


def read_cs_dat(dbf: Database, fd):
    """
    Parse a ChemSage DAT file into a pycalphad Database object.

    Parameters
    ----------
    dbf : Database
        A pycalphad Database.
    fd : file-like
        File descriptor.
    """
    data = parse_cs_dat(fd.read())
    # TODO: modify the dbf in place


Database.register_format("dat", read=read_cs_dat, write=None)
