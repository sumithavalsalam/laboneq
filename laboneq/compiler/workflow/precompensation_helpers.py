# Copyright 2019 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import math
from math import ceil
from typing import TYPE_CHECKING, Any, Dict, NewType

import numpy as np
from engineering_notation import EngNumber

from laboneq.compiler.common.device_type import DeviceType
from laboneq.core.exceptions import LabOneQException

if TYPE_CHECKING:
    from laboneq.compiler.experiment_access.experiment_dao import ExperimentDAO

PrecompensationType = NewType("PrecompensationType", Dict[str, Dict[str, Any]])


def precompensation_is_nonzero(precompensation: PrecompensationType):
    """Check whether the precompensation has any effect"""
    return precompensation is not None and (
        precompensation.get("exponential")
        or precompensation.get("high_pass")
        or precompensation.get("bounce")
        or precompensation.get("FIR")
    )


def precompensation_delay_samples(precompensation: PrecompensationType):
    """Compute the additional delay (in samples) caused by the precompensation"""
    if not precompensation_is_nonzero(precompensation):
        return 0
    delay = 72
    try:
        delay += 88 * len(precompensation["exponential"])
    except KeyError:
        pass
    if precompensation.get("high_pass") is not None:
        delay += 96
    if precompensation.get("bounce") is not None:
        delay += 32
    if precompensation.get("FIR") is not None:
        delay += 136
    return delay


def _adapt_precompensations_of_awg(signal_ids, precompensations):
    # If multiple signals per AWG, find the union of all filter enables
    number_of_exponentials = 0
    has_high_pass = None
    has_bounce = False
    has_FIR = False
    for signal_id in signal_ids:
        precompensation = precompensations.get(signal_id) or {}
        hp = bool(precompensation.get("high_pass"))
        if has_high_pass is None:
            has_high_pass = hp
        else:
            if hp != has_high_pass:
                raise RuntimeError(
                    "All precompensation settings for "
                    + "outputs of the same AWG must have the high pass "
                    + f"filter enabled or disabled; see signal {signal_id}"
                )
        exp = precompensation.get("exponential")
        if exp is not None and number_of_exponentials < len(exp):
            number_of_exponentials = len(exp)
        has_bounce = has_bounce or bool(precompensation.get("bounce"))
        has_FIR = has_FIR or bool(precompensation.get("FIR"))
    # Add zero effect filters to get consistent timing
    if has_bounce or has_FIR or number_of_exponentials:
        for signal_id in signal_ids:
            old_pc = precompensations.get(signal_id, {}) or {}
            new_pc = copy.deepcopy(old_pc)
            if number_of_exponentials:
                exp = new_pc.setdefault("exponential", [])
                exp += [{"amplitude": 0, "timeconstant": 10e-9}] * (
                    number_of_exponentials - len(exp)
                )
            if has_bounce and not new_pc.get("bounce"):
                new_pc["bounce"] = {"delay": 10e-9, "amplitude": 0}
            if has_FIR and not new_pc.get("FIR"):
                new_pc["FIR"] = {"coefficients": [1.0]}
            precompensations[signal_id] = new_pc


def adapt_precompensations(precompensations: PrecompensationType, dao: ExperimentDAO):
    """Make sure that we have the same timing for rf_signals on the same AWG"""
    signals_by_awg = {}
    # Group by AWG
    for signal_id in precompensations.keys():
        signal_info = dao.signal_info(signal_id)
        device_id = signal_info.device_id
        device_type = DeviceType(signal_info.device_type)
        channel = signal_info.channels[0]
        awg = (
            0
            if device_type == DeviceType.UHFQA
            else channel // device_type.channels_per_awg
        )
        signals_by_awg.setdefault((device_id, awg), []).append(signal_id)
    for signal_ids in signals_by_awg.values():
        if len(signal_ids) > 1:
            _adapt_precompensations_of_awg(signal_ids, precompensations)


def compute_precompensations_and_delays(dao: ExperimentDAO):
    """Retrieve precompensations from DAO, adapt those on the same AWG and
    compute timing"""
    precompensations = {
        id: copy.deepcopy(dao.precompensation(id)) for id in dao.signals()
    }
    adapt_precompensations(precompensations, dao)
    for signal_id, pc in precompensations.items():
        delay = precompensation_delay_samples(pc)
        pc = precompensations.setdefault(signal_id, {})
        if pc is None:
            precompensations[signal_id] = {"computed_delay_samples": delay}
        else:
            pc["computed_delay_samples"] = delay
    return precompensations


def compute_precompensation_delays_on_grid(
    precompensations: PrecompensationType, dao: ExperimentDAO, use_2GHz: bool
):
    """Compute delay_signal and port_delay contributions for each signal so that delays
    are commensurable with the grid"""
    signals = dao.signals()
    if not signals:
        return
    signal_infos = {
        signal_id: dao.signal_info(signal_id) for signal_id in dao.signals()
    }
    unique_sequencer_rates = set()
    sampling_rates_and_multiples = {}
    for signal_id in signals:
        devtype = DeviceType(signal_infos[signal_id].device_type)
        sampling_rate = (
            devtype.sampling_rate_2GHz
            if use_2GHz and devtype == DeviceType.HDAWG
            else devtype.sampling_rate
        )
        sequencer_rate = sampling_rate / devtype.sample_multiple
        sampling_rates_and_multiples[signal_id] = (
            sampling_rate,
            devtype.sample_multiple,
        )
        unique_sequencer_rates.add(int(sequencer_rate))

    common_sequencer_rate = np.gcd.reduce(list(unique_sequencer_rates))
    system_grid = 1.0 / common_sequencer_rate

    max_delay = 0
    for signal_id, pc in precompensations.items():
        delay = (
            precompensations[signal_id]["computed_delay_samples"]
            / sampling_rates_and_multiples[signal_id][0]
        )
        if max_delay < delay:
            max_delay = delay
    max_delay = ceil(max_delay / system_grid) * system_grid

    for signal_id in signals:
        pc = precompensations.setdefault(signal_id, {})
        try:
            delay_samples = pc["computed_delay_samples"]
        except KeyError:
            delay_samples = 0
        sampling_rate, multiple = sampling_rates_and_multiples[signal_id]
        max_delay_samples = max_delay * sampling_rate
        compensation = max_delay_samples - delay_samples
        delay_signal = (compensation // multiple) / sampling_rate * multiple
        port_delay = (compensation % multiple) / sampling_rate
        assert port_delay == 0 or signal_infos[signal_id].device_type != "uhfqa"
        pc["computed_delay_signal"] = delay_signal if abs(delay_signal) > 1e-12 else 0
        pc["computed_port_delay"] = port_delay if abs(port_delay) > 1e-12 else 0


def _round_to_FPGA(coef):
    # Rounds the filter coefficient to mimic the pseudo float implementation on
    # the FPGA This code follows the firmware notation for this functionality and
    # therefore looks odd
    DSP_OUTCOMP_BSHIFT_W = 2
    DSP_OUTCOMP_BSHIFT_C = 4
    DSP_OUTCOMP_COEF_W = 18

    bshift = 0  # bit shift. can be 0, -4, -8 or -12

    if coef != 0:
        bshift = int(
            max(
                min(
                    math.floor(-math.log2(abs(coef))) / (DSP_OUTCOMP_BSHIFT_C),
                    (1 << DSP_OUTCOMP_BSHIFT_W) - 1,
                ),
                0,
            )
        )
    coef = max(
        min(
            (
                round(
                    coef
                    * (1 << (DSP_OUTCOMP_COEF_W - 1 + DSP_OUTCOMP_BSHIFT_C * bshift))
                )
            ),
            (1 << (DSP_OUTCOMP_COEF_W - 1)) - 1,
        ),
        -(1 << (DSP_OUTCOMP_COEF_W - 1)),
    )
    rounded_coef = coef / (
        1 << (DSP_OUTCOMP_COEF_W - 1 + DSP_OUTCOMP_BSHIFT_C * bshift)
    )

    return rounded_coef


def clamp_exp_filter_params(timeconstant, amplitude, sampling_freq):
    EXP_IIR_DSP48_PPL_C = 2
    HZL_DSP_OUTC_COEF_EPS = 1.0e-32
    HZL_DSP_OUTCEXP_MIN_AMP = -1.0 + 1.0e-6
    HZL_DSP_OUTCEXP_MAX_ALPHA = 1.0 - 1.0e-6
    FPGA_PATHS = 8

    # First stage of clamping
    if timeconstant <= HZL_DSP_OUTC_COEF_EPS:
        timeconstant = HZL_DSP_OUTC_COEF_EPS

    if math.isnan(amplitude):
        amplitude = 0.0
    elif amplitude <= HZL_DSP_OUTCEXP_MIN_AMP:
        amplitude = HZL_DSP_OUTCEXP_MIN_AMP
    alpha = 1.0 - math.exp(-1 / (sampling_freq * timeconstant * (1.0 + amplitude)))

    if alpha >= HZL_DSP_OUTCEXP_MAX_ALPHA:
        alpha = HZL_DSP_OUTCEXP_MAX_ALPHA

    scaled_alpha = -FPGA_PATHS * EXP_IIR_DSP48_PPL_C * alpha

    # Second stage of clamping
    if scaled_alpha < -1.0:
        scaled_alpha = -1.0
        alpha = -scaled_alpha / (FPGA_PATHS * EXP_IIR_DSP48_PPL_C)

    if amplitude > 0.0:
        k = amplitude / (1.0 + amplitude - alpha)
    else:
        k = amplitude / ((1.0 + amplitude) * (1.0 - alpha))

    alpha = _round_to_FPGA(scaled_alpha) / (-FPGA_PATHS * EXP_IIR_DSP48_PPL_C)

    # Third stage of clamping: prevent the case where alpha == 0
    if alpha == 0.0:
        alpha = _round_to_FPGA(-1) / (-FPGA_PATHS * EXP_IIR_DSP48_PPL_C)
    k = _round_to_FPGA(k)

    # calc_exp_filter_params_reverse
    if k >= 0.0:
        amplitude = k * (1.0 - alpha) / (1.0 - k)
    else:
        amplitude = k * (1.0 - alpha) / (1.0 - k + alpha * k)
    timeconstant = -1.0 / (math.log(1 - alpha) * sampling_freq * (1 + amplitude))
    return timeconstant, amplitude


def verify_exponential_filter_params(
    timeconstant: float, amplitude: float, sampling_freq: float, signal_id: str
) -> str:
    timeconstant_clamped, amplitude_clamped = clamp_exp_filter_params(
        timeconstant, amplitude, sampling_freq
    )
    if not math.isclose(
        timeconstant_clamped, timeconstant, rel_tol=0.001
    ) or not math.isclose(amplitude_clamped, amplitude, rel_tol=0.001):
        return (
            f"Exponential precompensation values of signal '{signal_id}' out of range; "
            f"they will be clamped to timeconstant={EngNumber(timeconstant_clamped, significant=4)}s, "
            f"amplitude={EngNumber(amplitude_clamped, significant=4)}."
        )
    return ""


def verify_precompensation_parameters(
    precompensation: Dict, sampling_rate: float, signal_id: str
) -> str:
    if not precompensation:
        return ""
    warnings = []
    pcexp = precompensation.get("exponential")
    if pcexp:
        if len(precompensation["exponential"]) > 8:
            raise LabOneQException(
                f"Too many exponential filters defined on '{signal_id}'. Maximum is 8 exponential filters."
            )
        warnings += [
            verify_exponential_filter_params(
                e["timeconstant"], e["amplitude"], sampling_rate, signal_id
            )
            for e in pcexp
        ]
    hp = precompensation.get("high_pass")
    if hp and not (166e-3 >= hp["timeconstant"] >= 208e-12):
        warnings.append(
            f"High pass precompensation timeconstant of signal '{signal_id}' out "
            "of range; will be clamped to [208 ps, 166 ms]."
        )
    bounce = precompensation.get("bounce")
    if bounce:
        if bounce["delay"] > 103.3e-9:
            warnings.append(
                f"Bounce precompensation timeconstant of signal '{signal_id}' out "
                "of range; will be clamped to 103.3 ns."
            )
        if abs(bounce["amplitude"]) > 1:
            warnings.append(
                f"Bounce precompensation amplitude of signal '{signal_id}' out "
                "of range; will be clamped to +/- 1."
            )
    fir = precompensation.get("FIR")
    if fir:
        if len(fir["coefficients"]) > 40:
            raise LabOneQException(
                "Too many coefficients in FIR filter defined on "
                f"'{signal_id}'. Maximum is 40 coefficients."
            )
        if any(abs(np.array(fir["coefficients"])) > 4):
            warnings.append(
                f"FIR precompensation coefficients of signal '{signal_id}' out "
                "of range; will be clamped to +/- 4."
            )

    return " ".join(filter(None, warnings))
