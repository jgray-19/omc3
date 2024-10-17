from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pytest

import tfs
from omc3.correction.constants import VALUE, ERROR, WEIGHT
from omc3.correction.handler import get_measurement_data
from omc3.correction.model_appenders import add_coupling_to_model
from omc3.correction.model_diff import diff_twiss_parameters
from omc3.global_correction import global_correction_entrypoint as global_correction
from omc3.optics_measurements.constants import (
    NAME, AMPLITUDE, IMAG, REAL, BETA, DISPERSION,
    NORM_DISPERSION, F1001, F1010, TUNE, PHASE, ERR, DELTA, DELTAP_NAME)
from omc3.response_creator import create_response_entrypoint as create_response
from omc3.scripts.fake_measurement_from_model import VALUES, ERRORS
from omc3.scripts.fake_measurement_from_model import generate as fake_measurement
from omc3.utils import logging_tools
from omc3.utils.stats import rms

LOG = logging_tools.get_logger(__name__)
# LOG = logging_tools.get_logger('__main__', level_console=logging_tools.MADX)

# Paths ---
INPUTS = Path(__file__).parent.parent / 'inputs'
CORRECTION_INPUTS = INPUTS / "correction"
CORRECTION_TEST_INPUTS = INPUTS / "correction_test"

# Correction Input Parameters ---

RMS_TOL_DICT = {
    f"{PHASE}X": 0.001,
    f"{PHASE}Y": 0.001,
    f"{BETA}X": 0.01,
    f"{BETA}Y": 0.01,
    f"{DISPERSION}X": 0.0015,
    f"{DISPERSION}Y": 0.0015,
    f"{NORM_DISPERSION}X": 0.001,
    f"{TUNE}": 0.01,
    f"{F1001}R": 0.0015,
    f"{F1001}I": 0.0015,
    f"{F1010}R": 0.002,
    f"{F1010}I": 0.002,
}


@dataclass
class CorrectionParameters:
    twiss: Path
    correction_filename: Path
    optics_params: Sequence[str]
    variables: Sequence[str]
    weights: Sequence[float]
    fullresponse: str
    seed: int
    
    
def get_skew_params(beam):
    return CorrectionParameters(
        twiss=CORRECTION_INPUTS / f"inj_beam{beam}" / f"twiss_skew_quadrupole_error.dat",
        correction_filename=CORRECTION_TEST_INPUTS / f"changeparameters_injb{beam}_skewquadrupole.madx",
        optics_params=[f"{F1001}R", f"{F1001}I", f"{F1010}R", f"{F1010}I"],
        weights=[1., 1., 1., 1.],
        variables=["MQSl"],
        fullresponse="fullresponse_MQSl.h5",
        seed=2234,  # iteration test might not work with other seeds (converges too fast)
    )


def get_normal_params(beam):
    return CorrectionParameters(
        twiss=CORRECTION_INPUTS / f"inj_beam{beam}" / f"twiss_quadrupole_error.dat",
        correction_filename=CORRECTION_TEST_INPUTS / f"changeparameters_injb{beam}_quadrupole.madx",
        optics_params=[f"{PHASE}X", f"{PHASE}Y", f"{BETA}X", f"{BETA}Y", f"{NORM_DISPERSION}X", TUNE],
        weights=[1., 1., 1., 1., 1., 1.],
        variables=["MQY_Q4"],
        fullresponse="fullresponse_MQY.h5",
        seed=12368,  # iteration test might not work with other seeds (converges too fast)
    )


@pytest.mark.basic
@pytest.mark.parametrize('orientation', ('skew', 'normal'))
def test_lhc_global_correct(tmp_path, model_inj_beams, orientation):
    """Creates a fake measurement from a modfied model-twiss with (skew)
    quadrupole errors and runs global correction on this measurement.
    It is asserted that the resulting model approaches the modified twiss.
    In principle one could also check the last model, build from the final
    correction (as this correction is not plugged in to MAD-X again),
    but this is kind-of done with the correction test.
    Hint: the `model_inj_beam1` fixture is defined in `conftest.py`."""
    beam = model_inj_beams.beam
    correction_params = get_skew_params(beam) if orientation == 'skew' else get_normal_params(beam)
    iterations = 3   # '3' tests a single correction + one iteration, as the last (3rd) correction is not tested itself.

    # create and load fake measurement
    error_val = 0.1
    twiss_df, model_df, meas_dict = _create_fake_measurement(
        tmp_path, model_inj_beams.model_dir, correction_params.twiss, error_val, correction_params.optics_params, correction_params.seed
    )

    # Perform global correction
    global_correction(
        **model_inj_beams,
        # correction params
        meas_dir=tmp_path,
        variable_categories=correction_params.variables,
        fullresponse_path=model_inj_beams.model_dir / correction_params.fullresponse,
        optics_params=correction_params.optics_params,
        output_dir=tmp_path,
        weights=correction_params.weights,
        svd_cut=0.01,
        iterations=iterations,
    )

    # Test if corrected model is closer to model used to create measurement
    for iter_step in range(iterations):
        if iter_step == 0:
            model_iter_df = model_df
        else:
            model_iter_df = tfs.read(tmp_path / f"twiss_{iter_step}.tfs", index=NAME)
            model_iter_df = add_coupling_to_model(model_iter_df)

        diff_df = diff_twiss_parameters(model_iter_df, twiss_df, correction_params.optics_params)
        if TUNE in correction_params.optics_params:
            diff_df.headers[f"{DELTA}{TUNE}"] = np.array([diff_df[f"{DELTA}{TUNE}1"], diff_df[f"{DELTA}{TUNE}2"]])
        diff_rms = {param: rms(diff_df[f"{DELTA}{param}"] * weight)
                    for param, weight in zip(correction_params.optics_params, correction_params.weights)}

        ############ FOR DEBUGGING #############
        # Iteration 0 == fake uncorrected model
        # print()
        # print(f"ITERATION {iter_step}")
        # for param in correction_params.optics_params:
        #     print(f"{param}: {diff_rms[param]}")
        # print(f"Weighted Sum: {sum(diff_rms.values())}")
        # print()
        # continue
        # ########################################

        if iter_step > 0:
            # assert RMS after correction smaller than tolerances
            for param in correction_params.optics_params:
                assert diff_rms[param] < RMS_TOL_DICT[param], (
                    f"RMS for {param} in iteration {iter_step} larger than tolerance: "
                    f"{diff_rms[param]} >= {RMS_TOL_DICT[param]}."
                    )

            # assert total (weighted) RMS decreases between steps
            # ('skew' is converged after one step, still works with seed 2234)
            assert sum(diff_rms_prev.values()) > sum(diff_rms.values()), (
                f"Total RMS in iteration {iter_step} larger than in previous iteration."
                f"{sum(diff_rms.values())} >= {sum(diff_rms_prev.values())}."
            )

        diff_rms_prev = diff_rms


@pytest.mark.basic
@pytest.mark.parametrize('dpp', (-2e-4, -1e-4, 1e-4, 7.5e-4))
def test_lhc_global_correct_dpp(tmp_path, model_inj_beams, dpp):
    response_path = tmp_path / "full_response_dpp.h5"
    response_dict = create_response(
        outfile_path = response_path,
        variable_categories=[DELTAP_NAME],
        delta_k=2e-5,
        **model_inj_beams,
    )

    # Basic check if response was created correctly
    for key in response_dict.keys():
        assert DELTAP_NAME in response_dict[key].columns

    # create and load fake measurement
    dpp_path = run_dpp(tmp_path, dpp, model_inj_beams.beam)
    model_df = tfs.read(dpp_path, index=NAME)
    fake_measurement(
        twiss = model_df,
        parameters = [f"{PHASE}X", f"{PHASE}Y"],
        outputdir = tmp_path,
    )

    # See if the simulated dpp can be recreated
    for update_response in [True, False]:
        diff = np.inf
        for iteration in range(1, 4): # Must be at least 3 to test convergence
            global_correction(
                meas_dir = tmp_path,
                output_dir = tmp_path,
                fullresponse_path = response_path,
                variable_categories=[DELTAP_NAME],
                optics_params = [f"{PHASE}X", f"{PHASE}Y"],
                iterations=iteration,
                update_response=update_response,
                **model_inj_beams,
            )
            result = tfs.read(tmp_path / "changeparameters_iter.tfs", index=NAME)
            
            # Check if the output is correct within 5% (Beam 2 is not as accurate)
            rtol = 5e-2 if iteration == 1 else 2e-2
            assert np.isclose(dpp, -result[DELTA][DELTAP_NAME], rtol=rtol), f"Expected {dpp}, got {result[DELTA][DELTAP_NAME]}, diff: {dpp + result[DELTA][DELTAP_NAME]}, iteration: {iteration}"

            # Check if the result is converging or has converged (within 0.1%)
            rel_diff = np.abs(dpp + result[DELTA][DELTAP_NAME]) / np.abs(dpp)
            assert diff > rel_diff or np.isclose(diff, rel_diff, atol=1e-3), f"Convergence not reached, diff: {diff} <= {rel_diff}, iteration: {iteration}"
            diff = rel_diff
# Helper -----------------------------------------------------------------------


def _create_fake_measurement(tmp_path, model_path, twiss_path, error_val, optics_params, seed):
    model_df = tfs.read(model_path / "twiss.dat", index=NAME)
    model_df = add_coupling_to_model(model_df)

    twiss_df = tfs.read(twiss_path, index=NAME)
    twiss_df = add_coupling_to_model(twiss_df)

    # create fake measurement data
    fake_measurement(
        model=model_df,
        twiss=twiss_df,
        randomize=[VALUES, ERRORS],
        relative_errors=[error_val],
        seed=seed,
        outputdir=tmp_path,
    )

    # load the fake data into a dict
    _, meas_dict = get_measurement_data(
        optics_params,
        meas_dir=tmp_path,
        beta_filename='beta_phase_',
    )

    # map to VALUE, ERROR and WEIGHT, similar to filter_measurement
    # but without the filtering
    for col, meas in meas_dict.items():
        if col[:-1] in (F1010, F1001):
            col = {c[0]: c for c in (REAL, IMAG, PHASE, AMPLITUDE)}[col[-1]]

        if col != TUNE:
            meas[VALUE] = meas.loc[:, col].to_numpy()
            meas[ERROR] = meas.loc[:, f"{ERR}{col}"].to_numpy()
        meas[WEIGHT] = 1.
    return twiss_df, model_df, meas_dict

# Is the following better placed in a folder with reference files, 
# or to be generated on the fly?
from omc3 import madx_wrapper
from omc3.optics_measurements.constants import PHASE_ADV
def run_dpp(tmp_path, offset, beam):
    """
    Run a twiss on a 2018 LHC model with a given dpp offset. Then, correct and match before 
    writing the final twiss to a file, which only contains select BPMs, and the phase advances and s.
    This is used by the test_lhc_global_correct_dpp to verify that the global correction can
    calculate the dpp offset input in the fake measurement.
    """

    Qx = 62.28001034
    Qy = 60.31000965
    script = f"""
    option, -echo;
    call, file = 'omc3/model/madx_macros/general.macros.madx';
    call, file = 'omc3/model/madx_macros/lhc.macros.madx';
    call, file = 'omc3/model/accelerators/lhc/2018/main.seq';
    option, echo;
    exec, cycle_sequences();
    exec, define_nominal_beams();
    set, format = '.15e';
    call, file = 'tests/inputs/models/inj_beam{beam}/opticsfile.1'; !@modifier

    select, flag = twiss, pattern = 'BPM.*B[12]$', column = name, s, {PHASE_ADV}x, {PHASE_ADV}y;
    use, sequence = LHCB{beam};

    ! Match the tunes initially
    match, deltap = {offset};
    vary, name=dQx.b{beam};
    vary, name=dQy.b{beam};
    constraint, range = '#E', mux = {Qx}, muy = {Qy};
    lmdif, tolerance = 1.0e-10;
    endmatch;

    ! Run a twiss with the offset to get orbit
    twiss, deltap = {offset};

    ! Correct the orbit
    correct, mode = svd;

    ! Match the tunes back to normal
    match, deltap = {offset};
    vary, name=dQx.b{beam};
    vary, name=dQy.b{beam};
    constraint, range = '#E', mux = {Qx}, muy = {Qy};
    lmdif, tolerance = 1.0e-10;
    endmatch;

    ! Run the final twiss to get the off-orbit response
    twiss, deltap = {offset}, file = '{tmp_path}/twiss_dpp_{offset:.1e}_B{beam}.tfs';
    """
    madx_wrapper.run_string(script)
    return tmp_path / f"twiss_dpp_{offset:.1e}_B{beam}.tfs"