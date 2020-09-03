import os
import random
import string
import tempfile

import numpy as np
import pandas as pd
import pytest
import tfs

from omc3 import tbt
from omc3.definitions.constants import PLANES
from omc3.hole_in_one import hole_in_one_entrypoint

LIMITS = dict(F1=1e-6, A1=1.5e-3, P1=3e-4, F2=1.5e-4, A2=1.5e-1, P2=0.03)
NOISE = 3.2e-5
COUPLING = 0.01
NTURNS = 1024
NBPMS = 100
BASEAMP = 0.001
AMPZ, MUZ, TUNEZ = 0.01, 0.3, 0.008

np.random.seed(1234567)


class BasicTests:
    @staticmethod
    def test_harpy(_test_file, _model_file):
        model = _get_model_dataframe()
        tfs.write(_model_file, model, save_index="NAME")
        _write_tbt_file(model, os.path.dirname(_test_file))
        hole_in_one_entrypoint(harpy=True,
                               clean=True,
                               autotunes="transverse",
                               outputdir=os.path.dirname(_test_file),
                               files=[_test_file],
                               model=_model_file,
                               to_write=["lin"],
                               turn_bits=18,
                               unit="m")
        lin = dict(X=tfs.read(f"{_test_file}.linx"), Y=tfs.read(f"{_test_file}.liny"))
        model = tfs.read(_model_file)
        _assert_spectra(lin, model)

    @staticmethod
    def test_harpy_without_model(_test_file, _model_file):
        model = _get_model_dataframe()
        tfs.write(_model_file, model, save_index="NAME")
        _write_tbt_file(model, os.path.dirname(_test_file))
        hole_in_one_entrypoint(harpy=True,
                               clean=True,
                               autotunes="transverse",
                               outputdir=os.path.dirname(_test_file),
                               files=[_test_file],
                               to_write=["lin"],
                               turn_bits=18,
                               unit="m")
        lin = dict(X=tfs.read(f"{_test_file}.linx"), Y=tfs.read(f"{_test_file}.liny"))
        model = tfs.read(_model_file)
        _assert_spectra(lin, model)


class ExtendedTests:
    @staticmethod
    def test_freekick_harpy(_test_file, _model_file):
        model = _get_model_dataframe()
        tfs.write(_model_file, model, save_index="NAME")
        _write_tbt_file(model, os.path.dirname(_test_file))
        hole_in_one_entrypoint(harpy=True,
                               clean=True,
                               autotunes="transverse",
                               is_free_kick=True,
                               outputdir=os.path.dirname(_test_file),
                               files=[_test_file],
                               model=_model_file,
                               to_write=["lin"],
                               unit='m',
                               turn_bits=18)
        lin = dict(X=tfs.read(f"{_test_file}.linx"),
                   Y=tfs.read(f"{_test_file}.liny"))
        model = tfs.read(_model_file)
        for plane in PLANES:
            # main and secondary frequencies
            assert _rms(_diff(lin[plane].loc[:, f"TUNE{plane}"].to_numpy(),
                              model.loc[:, f"TUNE{plane}"].to_numpy())) < LIMITS["F1"]
            # main and secondary amplitudes
            # TODO remove factor 2 - only for backwards compatibility with Drive
            assert _rms(_rel_diff(lin[plane].loc[:, f"AMP{plane}"].to_numpy() * 2,
                                  model.loc[:, f"AMP{plane}"].to_numpy())) < LIMITS["A1"]
            # main and secondary phases
            assert _rms(_angle_diff(lin[plane].loc[:, f"MU{plane}"].to_numpy(),
                                    model.loc[:, f"MU{plane}"].to_numpy())) < LIMITS["P1"]

    @staticmethod
    def test_harpy_3d(_test_file, _model_file):
        model = _get_model_dataframe()
        tfs.write(_model_file, model, save_index="NAME")
        _write_tbt_file(model, os.path.dirname(_test_file))
        hole_in_one_entrypoint(harpy=True,
                               clean=True,
                               autotunes="all",
                               outputdir=os.path.dirname(_test_file),
                               files=[_test_file],
                               model=_model_file,
                               to_write=["lin"],
                               turn_bits=18,
                               unit="m")
        lin = dict(X=tfs.read(f"{_test_file}.linx"), Y=tfs.read(f"{_test_file}.liny"))
        model = tfs.read(_model_file)
        _assert_spectra(lin, model)
        assert _rms(_diff(lin["X"].loc[:, "TUNEZ"].to_numpy(), TUNEZ)) < LIMITS["F2"]
        assert _rms(_rel_diff(lin["X"].loc[:, f"AMPZ"].to_numpy() *
                              lin["X"].loc[:, f"AMPX"].to_numpy() * 2, AMPZ * BASEAMP)) < LIMITS["A2"]
        assert _rms(_angle_diff(lin["X"].loc[:, f"MUZ"].to_numpy(), MUZ)) < LIMITS["P2"]


def _assert_spectra(lin, model):
    for plane in PLANES:
        # main and secondary frequencies
        assert _rms(_diff(lin[plane].loc[:, f"TUNE{plane}"].to_numpy(),
                          model.loc[:, f"TUNE{plane}"].to_numpy())) < LIMITS["F1"]
        assert _rms(_diff(lin[plane].loc[:, f"FREQ{_couple(plane)}"].to_numpy(),
                          model.loc[:, f"TUNE{_other(plane)}"].to_numpy())) < LIMITS["F2"]
        # main and secondary amplitudes
        # TODO remove factor 2 - only for backwards compatibility with Drive
        assert _rms(_rel_diff(lin[plane].loc[:, f"AMP{plane}"].to_numpy() * 2,
                              model.loc[:, f"AMP{plane}"].to_numpy())) < LIMITS["A1"]
        assert _rms(_rel_diff(lin[plane].loc[:, f"AMP{_couple(plane)}"].to_numpy() *
                              lin[plane].loc[:, f"AMP{plane}"].to_numpy() * 2,
                              COUPLING * model.loc[:, f"AMP{_other(plane)}"].to_numpy())) < LIMITS["A2"]
        # main and secondary phases
        assert _rms(_angle_diff(lin[plane].loc[:, f"MU{plane}"].to_numpy(),
                                model.loc[:, f"MU{plane}"].to_numpy())) < LIMITS["P1"]
        assert _rms(_angle_diff(lin[plane].loc[:, f"PHASE{_couple(plane)}"].to_numpy(),
                                model.loc[:, f"MU{_other(plane)}"].to_numpy())) < LIMITS["P2"]


def _get_model_dataframe():
    return pd.DataFrame(data=dict(S=np.arange(NBPMS, dtype=float),
                                  AMPX=(np.random.rand(NBPMS) + 1) * BASEAMP,
                                  AMPY=(np.random.rand(NBPMS) + 1) * BASEAMP,
                                  MUX=np.random.rand(NBPMS) - 0.5,
                                  MUY=np.random.rand(NBPMS) - 0.5,
                                  TUNEX=0.25 + np.random.rand(1)[0] / 40,
                                  TUNEY=0.3 + np.random.rand(1)[0] / 40),
                        index=np.array([''.join(random.choices(string.ascii_uppercase, k=7))
                                        for _ in range(NBPMS)]))


def _write_tbt_file(model, dir_path):
    ints = np.arange(NTURNS) - NTURNS / 2
    data_x = model.loc[:, "AMPX"].to_numpy()[:, None] * np.cos(
        2 * np.pi * (model.loc[:, "MUX"].to_numpy()[:, None] +
                     model.loc[:, "TUNEX"].to_numpy()[:, None] * ints[None, :]))
    data_y = model.loc[:, "AMPY"].to_numpy()[:, None] * np.cos(
        2 * np.pi * (model.loc[:, "MUY"].to_numpy()[:, None] +
                     model.loc[:, "TUNEY"].to_numpy()[:, None] * ints[None, :]))
    data_z = AMPZ * BASEAMP * np.ones((NBPMS, 1)) * np.cos(
        2 * np.pi * (MUZ * np.ones((NBPMS, 1)) +
                     TUNEZ * np.ones((NBPMS, 1)) * ints[None, :]))
    mats = dict(X=pd.DataFrame(data=np.random.randn(model.index.size, NTURNS) * NOISE + data_x
                               + COUPLING * data_y + data_z, index=model.index),
                Y=pd.DataFrame(data=np.random.randn(model.index.size, NTURNS) * NOISE + data_y
                               + COUPLING * data_x, index=model.index))
    tbt.write(os.path.join(dir_path, "test_file"), tbt.TbtData([mats], None, [0], NTURNS))


def _other(plane):
    return "X" if plane == "Y" else "Y"


def _couple(plane):
    return "10" if plane == "Y" else "01"


def _rms(a):
    return np.sqrt(np.mean(np.square(a)))


def _diff(a, b):
    return a - b


def _rel_diff(a, b):
    return (a / b) - 1


def _angle_diff(a, b):
    ang = a - b
    return np.where(np.abs(ang) > 0.5, ang - np.sign(ang), ang)


@pytest.fixture()
def _test_file():
    with tempfile.TemporaryDirectory() as cwd:
        test_file = os.path.join(cwd, "test_file.sdds")
        yield test_file


@pytest.fixture()
def _model_file():
    with tempfile.TemporaryDirectory() as cwd:
        test_file = os.path.join(cwd, "model.tfs")
        yield test_file
