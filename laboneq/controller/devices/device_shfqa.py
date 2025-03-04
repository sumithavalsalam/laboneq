# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import numpy as np
from numpy import typing as npt

from laboneq.controller.attribute_value_tracker import (
    AttributeName,
    DeviceAttribute,
    DeviceAttributesView,
)
from laboneq.controller.communication import (
    CachingStrategy,
    DaqNodeAction,
    DaqNodeGetAction,
    DaqNodeSetAction,
)
from laboneq.controller.devices.device_shf_base import DeviceSHFBase
from laboneq.controller.devices.device_zi import (
    SequencerPaths,
    Waveforms,
    delay_to_rounded_samples,
)
from laboneq.controller.recipe_1_4_0 import (
    IO,
    Initialization,
    IntegratorAllocation,
    Measurement,
)
from laboneq.controller.recipe_enums import TriggeringMode
from laboneq.controller.recipe_processor import (
    AwgConfig,
    AwgKey,
    DeviceRecipeData,
    RecipeData,
    RtExecutionInfo,
    get_wave,
)
from laboneq.controller.util import LabOneQControllerException
from laboneq.core.types.enums.acquisition_type import AcquisitionType, is_spectroscopy
from laboneq.core.types.enums.averaging_mode import AveragingMode

_logger = logging.getLogger(__name__)

INTERNAL_TRIGGER_CHANNEL = 1024  # PQSC style triggering on the SHFSG/QC
SOFTWARE_TRIGGER_CHANNEL = 8  # Software triggering on the SHFQA

SAMPLE_FREQUENCY_HZ = 2.0e9
DELAY_NODE_GRANULARITY_SAMPLES = 4
DELAY_NODE_MAX_SAMPLES = 1e-6 * SAMPLE_FREQUENCY_HZ
# About DELAY_NODE_MAX_SAMPLES: The max time is actually 131e-6 s (at least I can set that
# value in GUI and API). However, there were concerns that these long times are not tested
# often enough - also, if you read the value back from the API, some lesser significant bits
# have strange values which looked a bit suspicious. Therefore, it was decided to limit the
# maximum delay to 1 us for now


class DeviceSHFQA(DeviceSHFBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dev_type = "SHFQA4"
        self.dev_opts = []
        self._channels = 4
        self._wait_for_awgs = True
        self._emit_trigger = False

    @property
    def dev_repr(self) -> str:
        if self.options.is_qc:
            return f"SHFQC/QA:{self.serial}"
        return f"SHFQA:{self.serial}"

    def _process_dev_opts(self):
        if self.dev_type == "SHFQA4":
            self._channels = 4
        elif self.dev_type == "SHFQA2":
            self._channels = 2
        elif self.dev_type == "SHFQC":
            self._channels = 1
        else:
            _logger.warning(
                "%s: Unknown device type '%s', assuming SHFQA4 device.",
                self.dev_repr,
                self.dev_type,
            )
            self._channels = 4

    def _get_sequencer_type(self) -> str:
        return "qa"

    def get_sequencer_paths(self, index: int) -> SequencerPaths:
        return SequencerPaths(
            elf=f"/{self.serial}/qachannels/{index}/generator/elf/data",
            progress=f"/{self.serial}/qachannels/{index}/generator/elf/progress",
            enable=f"/{self.serial}/qachannels/{index}/generator/enable",
            ready=f"/{self.serial}/qachannels/{index}/generator/ready",
        )

    def _get_num_awgs(self):
        return self._channels

    def _validate_range(self, io: IO.Data, is_out: bool):
        if io.range is None:
            return
        input_ranges = np.array(
            [-50, -30, -25, -20, -15, -10, -5, 0, 5, 10], dtype=np.float64
        )
        output_ranges = np.array(
            [-30, -25, -20, -15, -10, -5, 0, 5, 10], dtype=np.float64
        )
        range_list = output_ranges if is_out else input_ranges
        label = "Output" if is_out else "Input"

        if io.range_unit not in (None, "dBm"):
            raise LabOneQControllerException(
                f"{label} range of device {self.dev_repr} is specified in "
                f"units of {io.range_unit}. Units must be 'dBm'."
            )
        if not any(np.isclose([io.range] * len(range_list), range_list)):
            _logger.warning(
                "%s: %s channel %d range %.1f is not on the list of allowed ranges: %s. "
                "Nearest allowed range will be used.",
                self.dev_repr,
                label,
                io.channel,
                io.range,
                range_list,
            )

    def _osc_group_by_channel(self, channel: int) -> int:
        return channel

    def _get_next_osc_index(
        self, osc_group: int, previously_allocated: int
    ) -> int | None:
        if previously_allocated >= 1:
            return None
        return previously_allocated

    def _make_osc_path(self, channel: int, index: int) -> str:
        return f"/{self.serial}/qachannels/{channel}/oscs/{index}/freq"

    def disable_outputs(
        self, outputs: set[int], invert: bool
    ) -> list[DaqNodeSetAction]:
        channels_to_disable: list[DaqNodeSetAction] = []
        for ch in range(self._channels):
            if (ch in outputs) != invert:
                channels_to_disable.append(
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{ch}/output/on",
                        0,
                        caching_strategy=CachingStrategy.NO_CACHE,
                    )
                )
        return channels_to_disable

    def on_experiment_end(self):
        nodes = super().on_experiment_end()
        return [
            *nodes,
            # in CW spectroscopy mode, turn off the tone
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/*/spectroscopy/envelope/enable",
                1,
            ),
        ]

    def _nodes_to_monitor_impl(self) -> list[str]:
        nodes = super()._nodes_to_monitor_impl()
        for awg in range(self._get_num_awgs()):
            nodes.extend(
                [
                    f"/{self.serial}/qachannels/{awg}/generator/enable",
                    f"/{self.serial}/qachannels/{awg}/generator/ready",
                    f"/{self.serial}/qachannels/{awg}/spectroscopy/psd/enable",
                    f"/{self.serial}/qachannels/{awg}/spectroscopy/result/enable",
                    f"/{self.serial}/qachannels/{awg}/readout/result/enable",
                ]
            )
        return nodes

    def configure_acquisition(
        self,
        awg_key: AwgKey,
        awg_config: AwgConfig,
        integrator_allocations: list[IntegratorAllocation.Data],
        averages: int,
        averaging_mode: AveragingMode,
        acquisition_type: AcquisitionType,
    ) -> list[DaqNodeAction]:

        average_mode = 0 if averaging_mode == AveragingMode.CYCLIC else 1
        nodes = [
            *self._configure_readout(
                acquisition_type,
                awg_key,
                awg_config,
                integrator_allocations,
                averages,
                average_mode,
            ),
            *self._configure_spectroscopy(
                acquisition_type,
                awg_key.awg_index,
                awg_config.result_length,
                averages,
                average_mode,
            ),
            *self._configure_scope(
                enable=acquisition_type == AcquisitionType.RAW,
                channel=awg_key.awg_index,
                averages=averages,
                acquire_length=awg_config.raw_acquire_length,
            ),
        ]
        return nodes

    def _configure_readout(
        self,
        acquisition_type: AcquisitionType,
        awg_key: AwgKey,
        awg_config: AwgConfig,
        integrator_allocations: list[IntegratorAllocation.Data],
        averages: int,
        average_mode: int,
    ):
        enable = acquisition_type in [
            AcquisitionType.INTEGRATION,
            AcquisitionType.DISCRIMINATION,
        ]
        channel = awg_key.awg_index
        nodes_to_initialize_readout = []
        if enable:
            nodes_to_initialize_readout.extend(
                [
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/readout/result/length",
                        awg_config.result_length,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/readout/result/averages",
                        averages,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/readout/result/source",
                        # 1 - result_of_integration
                        # 3 - result_of_discrimination
                        3 if acquisition_type == AcquisitionType.DISCRIMINATION else 1,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/readout/result/mode",
                        average_mode,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/readout/result/enable",
                        0,
                    ),
                ]
            )
            if acquisition_type in [
                AcquisitionType.INTEGRATION,
                AcquisitionType.DISCRIMINATION,
            ]:
                for integrator in integrator_allocations:
                    if (
                        integrator.device_id != awg_key.device_uid
                        or integrator.signal_id not in awg_config.acquire_signals
                    ):
                        continue
                    assert len(integrator.channels) == 1
                    integrator_idx = integrator.channels[0]
                    nodes_to_initialize_readout.append(
                        DaqNodeSetAction(
                            self._daq,
                            f"/{self.serial}/qachannels/{channel}/readout/discriminators/"
                            f"{integrator_idx}/threshold",
                            integrator.threshold,
                        )
                    )
        nodes_to_initialize_readout.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{channel}/readout/result/enable",
                1 if enable else 0,
            )
        )
        return nodes_to_initialize_readout

    def _configure_spectroscopy(
        self,
        acq_type: AcquisitionType,
        channel: int,
        result_length: int,
        averages: int,
        average_mode: int,
    ):
        nodes_to_initialize_spectroscopy = []
        if is_spectroscopy(acq_type):
            nodes_to_initialize_spectroscopy.extend(
                [
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/spectroscopy/result/length",
                        result_length,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/spectroscopy/result/averages",
                        averages,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/spectroscopy/result/mode",
                        average_mode,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/spectroscopy/psd/enable",
                        0,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{channel}/spectroscopy/result/enable",
                        0,
                    ),
                ]
            )

        if acq_type == AcquisitionType.SPECTROSCOPY_PSD:
            nodes_to_initialize_spectroscopy.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{channel}/spectroscopy/psd/enable",
                    1,
                ),
            )

        nodes_to_initialize_spectroscopy.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{channel}/spectroscopy/result/enable",
                1 if is_spectroscopy(acq_type) else 0,
            )
        )
        return nodes_to_initialize_spectroscopy

    def _configure_scope(
        self, enable: bool, channel: int, averages: int, acquire_length: int
    ):
        # TODO(2K): multiple acquire events
        nodes_to_initialize_scope = []
        if enable:
            nodes_to_initialize_scope.extend(
                [
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/time", 0
                    ),  # 0 -> 2 GSa/s
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/averaging/enable", 1
                    ),
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/averaging/count", averages
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/scopes/0/channels/{channel}/enable",
                        1,
                    ),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/scopes/0/channels/{channel}/inputselect",
                        channel,
                    ),  # channelN_signal_input
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/length", acquire_length
                    ),
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/segments/enable", 0
                    ),
                    # TODO(2K): multiple acquire events per monitor
                    # DaqNodeSetAction(self._daq, f"/{self.serial}/scopes/0/segments/enable", 1),
                    # DaqNodeSetAction(self._daq, f"/{self.serial}/scopes/0/segments/count",
                    #                  measurement.result_length),
                    # TODO(2K): only one trigger is possible for all channels. Which one to use?
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/scopes/0/trigger/channel",
                        64 + channel,
                    ),  # channelN_sequencer_monitor0
                    # TODO(2K): 200ns input-to-output delay was taken from one of the example
                    # notebooks, what value to use?
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/trigger/delay", 200e-9
                    ),
                    DaqNodeSetAction(
                        self._daq, f"/{self.serial}/scopes/0/trigger/enable", 1
                    ),
                    DaqNodeSetAction(self._daq, f"/{self.serial}/scopes/0/enable", 0),
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/scopes/0/single",
                        1,
                        caching_strategy=CachingStrategy.NO_CACHE,
                    ),
                ]
            )
        nodes_to_initialize_scope.append(
            DaqNodeSetAction(
                self._daq, f"/{self.serial}/scopes/0/enable", 1 if enable else 0
            )
        )
        return nodes_to_initialize_scope

    def collect_execution_nodes(self):
        _logger.debug("Starting execution...")
        return [
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{awg_index}/generator/enable",
                1,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
            for awg_index in self._allocated_awgs
        ]

    def collect_start_execution_nodes(self):
        if self._emit_trigger:
            return [
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/system/internaltrigger/enable"
                    if self.options.is_qc
                    else f"/{self.serial}/system/swtriggers/0/single",
                    1,
                    caching_strategy=CachingStrategy.NO_CACHE,
                )
            ]
        return []

    def conditions_for_execution_ready(self) -> dict[str, Any]:
        # TODO(janl): Not sure whether we need this condition this on the SHFQA (including SHFQC)
        # as well. The state of the generator enable wasn't always pickup up reliably, so we
        # only check in cases where we rely on external triggering mechanisms.
        conditions: dict[str, Any] = {}
        if self._wait_for_awgs:
            for awg_index in self._allocated_awgs:
                conditions[
                    f"/{self.serial}/qachannels/{awg_index}/generator/enable"
                ] = 1
        return conditions

    def conditions_for_execution_done(
        self, acquisition_type: AcquisitionType
    ) -> dict[str, Any]:
        conditions: dict[str, Any] = {}
        for awg_index in self._allocated_awgs:
            conditions[f"/{self.serial}/qachannels/{awg_index}/generator/enable"] = 0
            if is_spectroscopy(acquisition_type):
                conditions[
                    f"/{self.serial}/qachannels/{awg_index}/spectroscopy/result/enable"
                ] = 0
            elif acquisition_type in [
                AcquisitionType.INTEGRATION,
                AcquisitionType.DISCRIMINATION,
            ]:
                conditions[
                    f"/{self.serial}/qachannels/{awg_index}/readout/result/enable"
                ] = 0
        return conditions

    def pre_process_attributes(
        self,
        initialization: Initialization.Data,
    ) -> Iterator[DeviceAttribute]:
        yield from super().pre_process_attributes(initialization)

        for output in initialization.outputs or []:
            if output.amplitude is not None:
                yield DeviceAttribute(
                    name=AttributeName.QA_OUT_AMPLITUDE,
                    index=output.channel,
                    value_or_param=output.amplitude,
                )

        center_frequencies = {}
        ios = (initialization.outputs or []) + (initialization.inputs or [])
        for idx, io in enumerate(ios):
            if io.lo_frequency is not None:
                if io.channel in center_frequencies:
                    prev_io_idx = center_frequencies[io.channel]
                    if ios[prev_io_idx].lo_frequency != io.lo_frequency:
                        raise LabOneQControllerException(
                            f"{self.dev_repr}: Local oscillator frequency mismatch between IOs "
                            f"sharing channel {io.channel}: "
                            f"{ios[prev_io_idx].lo_frequency} != {io.lo_frequency}"
                        )
                    continue
                center_frequencies[io.channel] = idx
                yield DeviceAttribute(
                    name=AttributeName.QA_CENTER_FREQ,
                    index=io.channel,
                    value_or_param=io.lo_frequency,
                )

    def collect_initialization_nodes(
        self, device_recipe_data: DeviceRecipeData, initialization: Initialization.Data
    ) -> list[DaqNodeSetAction]:
        _logger.debug("%s: Initializing device...", self.dev_repr)

        nodes_to_initialize_output: list[DaqNodeSetAction] = []

        outputs = initialization.outputs or []
        for output in outputs:
            self._warn_for_unsupported_param(
                output.offset is None or output.offset == 0,
                "voltage_offsets",
                output.channel,
            )
            self._warn_for_unsupported_param(
                output.gains is None, "correction_matrix", output.channel
            )
            self._allocated_awgs.add(output.channel)
            nodes_to_initialize_output.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{output.channel}/output/on",
                    1 if output.enable else 0,
                )
            )
            if output.range is not None:
                self._validate_range(output, is_out=True)
                nodes_to_initialize_output.append(
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{output.channel}/output/range",
                        output.range,
                    )
                )

            nodes_to_initialize_output.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{output.channel}/generator/single",
                    1,
                )
            )

        return nodes_to_initialize_output

    def collect_prepare_nt_step_nodes(
        self, attributes: DeviceAttributesView, recipe_data: RecipeData
    ) -> list[DaqNodeAction]:
        nodes_to_set = super().collect_prepare_nt_step_nodes(attributes, recipe_data)

        acquisition_type = RtExecutionInfo.get_acquisition_type(
            recipe_data.rt_execution_infos
        )

        for ch in range(self._channels):
            [synth_cf], synth_cf_updated = attributes.resolve(
                keys=[(AttributeName.QA_CENTER_FREQ, ch)]
            )
            if synth_cf_updated:
                nodes_to_set.append(
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{ch}/centerfreq",
                        synth_cf,
                    )
                )

            [out_amp], out_amp_updated = attributes.resolve(
                keys=[(AttributeName.QA_OUT_AMPLITUDE, ch)]
            )
            if out_amp_updated:
                nodes_to_set.append(
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{ch}/oscs/0/gain",
                        out_amp,
                    )
                )

            [
                output_scheduler_port_delay,
                output_port_delay,
            ], output_updated = attributes.resolve(
                keys=[
                    (AttributeName.OUTPUT_SCHEDULER_PORT_DELAY, ch),
                    (AttributeName.OUTPUT_PORT_DELAY, ch),
                ]
            )
            output_delay = (
                0.0
                if output_scheduler_port_delay is None
                else output_scheduler_port_delay + (output_port_delay or 0.0)
            )
            set_output = output_updated and output_scheduler_port_delay is not None

            [
                input_scheduler_port_delay,
                input_port_delay,
            ], input_updated = attributes.resolve(
                keys=[
                    (AttributeName.INPUT_SCHEDULER_PORT_DELAY, ch),
                    (AttributeName.INPUT_PORT_DELAY, ch),
                ]
            )
            measurement_delay = (
                0.0
                if input_scheduler_port_delay is None
                else input_scheduler_port_delay + (input_port_delay or 0.0)
            )
            set_input = input_updated and input_scheduler_port_delay is not None

            base_channel_path = f"/{self.serial}/qachannels/{ch}"
            if is_spectroscopy(acquisition_type):
                output_delay_path = f"{base_channel_path}/spectroscopy/envelope/delay"
                meas_delay_path = f"{base_channel_path}/spectroscopy/delay"
            else:
                output_delay_path = f"{base_channel_path}/generator/delay"
                meas_delay_path = f"{base_channel_path}/readout/integration/delay"
                measurement_delay += output_delay
                set_input = set_input or set_output

            if set_output:
                output_delay_rounded = (
                    delay_to_rounded_samples(
                        channel=ch,
                        dev_repr=self.dev_repr,
                        delay=output_delay,
                        sample_frequency_hz=SAMPLE_FREQUENCY_HZ,
                        granularity_samples=DELAY_NODE_GRANULARITY_SAMPLES,
                        max_node_delay_samples=DELAY_NODE_MAX_SAMPLES,
                    )
                    / SAMPLE_FREQUENCY_HZ
                )
                nodes_to_set.append(
                    DaqNodeSetAction(self._daq, output_delay_path, output_delay_rounded)
                )

            if set_input:
                measurement_delay_rounded = (
                    delay_to_rounded_samples(
                        channel=ch,
                        dev_repr=self.dev_repr,
                        delay=measurement_delay,
                        sample_frequency_hz=SAMPLE_FREQUENCY_HZ,
                        granularity_samples=DELAY_NODE_GRANULARITY_SAMPLES,
                        max_node_delay_samples=DELAY_NODE_MAX_SAMPLES,
                    )
                    / SAMPLE_FREQUENCY_HZ
                )
                nodes_to_set.append(
                    DaqNodeSetAction(
                        self._daq, meas_delay_path, measurement_delay_rounded
                    )
                )

        return nodes_to_set

    def prepare_upload_binary_wave(
        self,
        filename: str,
        waveform: npt.ArrayLike,
        awg_index: int,
        wave_index: int,
        acquisition_type: AcquisitionType,
    ):
        assert not is_spectroscopy(acquisition_type) or wave_index == 0
        return DaqNodeSetAction(
            self._daq,
            f"/{self.serial}/qachannels/{awg_index}/spectroscopy/envelope/wave"
            if is_spectroscopy(acquisition_type)
            else f"/{self.serial}/qachannels/{awg_index}/generator/waveforms/{wave_index}/wave",
            waveform,
            filename=filename,
            caching_strategy=CachingStrategy.NO_CACHE,
        )

    def prepare_upload_all_binary_waves(
        self,
        awg_index,
        waves: Waveforms,
        acquisition_type: AcquisitionType,
    ):
        waves_upload: list[DaqNodeSetAction] = []
        has_spectroscopy_envelope = False
        if is_spectroscopy(acquisition_type):
            if len(waves) > 1:
                raise LabOneQControllerException(
                    f"{self.dev_repr}: Only one envelope waveform per physical channel is "
                    f"possible in spectroscopy mode. Check play commands for channel {awg_index}."
                )
            max_len = 65536
            for wave in waves:
                has_spectroscopy_envelope = True
                wave_len = len(wave.samples)
                if wave_len > max_len:
                    max_pulse_len = max_len / SAMPLE_FREQUENCY_HZ
                    raise LabOneQControllerException(
                        f"{self.dev_repr}: Length {wave_len} of the envelope waveform "
                        f"'{wave.name}' for spectroscopy unit {awg_index} exceeds maximum "
                        f"of {max_len} samples. Ensure measure pulse doesn't "
                        f"exceed {max_pulse_len * 1e6:.3f} us."
                    )
                waves_upload.append(
                    self.prepare_upload_binary_wave(
                        filename=wave.name,
                        waveform=wave.samples,
                        awg_index=awg_index,
                        wave_index=0,
                        acquisition_type=acquisition_type,
                    )
                )
        else:
            max_len = 4096
            for wave in waves:
                wave_len = len(wave.samples)
                if wave_len > max_len:
                    max_pulse_len = max_len / SAMPLE_FREQUENCY_HZ
                    raise LabOneQControllerException(
                        f"{self.dev_repr}: Length {wave_len} of the waveform '{wave.name}' "
                        f"for generator {awg_index} / wave slot {wave.index} exceeds maximum "
                        f"of {max_len} samples. Ensure measure pulse doesn't exceed "
                        f"{max_pulse_len * 1e6:.3f} us."
                    )
                waves_upload.append(
                    self.prepare_upload_binary_wave(
                        filename=wave.name,
                        waveform=wave.samples,
                        awg_index=awg_index,
                        wave_index=wave.index,
                        acquisition_type=acquisition_type,
                    )
                )
        waves_upload.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{awg_index}/spectroscopy/envelope/enable",
                1 if has_spectroscopy_envelope else 0,
            )
        )
        return waves_upload

    def _configure_readout_mode_nodes(
        self,
        dev_input: IO.Data,
        dev_output: IO.Data,
        measurement: Measurement.Data | None,
        device_uid: str,
        recipe_data: RecipeData,
    ):
        _logger.debug("%s: Setting measurement mode to 'Readout'.", self.dev_repr)

        nodes_to_set_for_readout_mode = [
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{measurement.channel}/readout/integration/length",
                measurement.length,
            ),
        ]

        max_len = 4096
        for (
            integrator_allocation
        ) in recipe_data.recipe.experiment.integrator_allocations:
            if (
                integrator_allocation.device_id != device_uid
                or integrator_allocation.awg != measurement.channel
            ):
                continue
            if integrator_allocation.weights is None:
                # Skip configuration if no integration weights provided to keep same behavior
                # TODO(2K): Consider not emitting the integrator allocation in this case.
                continue

            if len(integrator_allocation.channels) != 1:
                raise LabOneQControllerException(
                    f"{self.dev_repr}: Internal error - expected 1 integrator for "
                    f"signal '{integrator_allocation.signal_id}', "
                    f"got {len(integrator_allocation.channels)}"
                )
            integration_unit_index = integrator_allocation.channels[0]
            wave_name = integrator_allocation.weights + ".wave"
            weight_vector = np.conjugate(
                get_wave(wave_name, recipe_data.scheduled_experiment.waves)
            )
            wave_len = len(weight_vector)
            if wave_len > max_len:
                max_pulse_len = max_len / SAMPLE_FREQUENCY_HZ
                raise LabOneQControllerException(
                    f"{self.dev_repr}: Length {wave_len} of the integration weight "
                    f"'{integration_unit_index}' of channel {measurement.channel} exceeds "
                    f"maximum of {max_len} samples. Ensure length of acquire kernels don't "
                    f"exceed {max_pulse_len * 1e6:.3f} us."
                )
            node_path = (
                f"/{self.serial}/qachannels/{measurement.channel}/readout/integration/"
                f"weights/{integration_unit_index}/wave"
            )
            nodes_to_set_for_readout_mode.append(
                DaqNodeSetAction(
                    self._daq,
                    node_path,
                    weight_vector,
                    filename=wave_name,
                    caching_strategy=CachingStrategy.CACHE,
                )
            )
        return nodes_to_set_for_readout_mode

    def _configure_spectroscopy_mode_nodes(
        self, dev_input: IO.Data, measurement: Measurement.Data | None
    ):
        _logger.debug("%s: Setting measurement mode to 'Spectroscopy'.", self.dev_repr)

        nodes_to_set_for_spectroscopy_mode = [
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{measurement.channel}/spectroscopy/trigger/channel",
                32 + measurement.channel,
            ),
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/{measurement.channel}/spectroscopy/length",
                measurement.length,
            ),
        ]

        return nodes_to_set_for_spectroscopy_mode

    def collect_awg_before_upload_nodes(
        self, initialization: Initialization.Data, recipe_data: RecipeData
    ):
        nodes_to_initialize_measurement = []

        acquisition_type = RtExecutionInfo.get_acquisition_type(
            recipe_data.rt_execution_infos
        )

        for measurement in initialization.measurements:
            nodes_to_initialize_measurement.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{measurement.channel}/mode",
                    0 if is_spectroscopy(acquisition_type) else 1,
                )
            )

            dev_input = next(
                (
                    inp
                    for inp in initialization.inputs
                    if inp.channel == measurement.channel
                ),
                None,
            )
            dev_output = next(
                (
                    output
                    for output in initialization.outputs
                    if output.channel == measurement.channel
                ),
                None,
            )
            if is_spectroscopy(acquisition_type):
                nodes_to_initialize_measurement.extend(
                    self._configure_spectroscopy_mode_nodes(dev_input, measurement)
                )
            else:
                nodes_to_initialize_measurement.extend(
                    self._configure_readout_mode_nodes(
                        dev_input,
                        dev_output,
                        measurement,
                        initialization.device_uid,
                        recipe_data,
                    )
                )
        return nodes_to_initialize_measurement

    def collect_awg_after_upload_nodes(self, initialization: Initialization.Data):
        nodes_to_initialize_measurement = []
        inputs = initialization.inputs or []
        for dev_input in inputs:
            nodes_to_initialize_measurement.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{dev_input.channel}/input/on",
                    1,
                )
            )
            if dev_input.range is not None:
                self._validate_range(dev_input, is_out=False)
                nodes_to_initialize_measurement.append(
                    DaqNodeSetAction(
                        self._daq,
                        f"/{self.serial}/qachannels/{dev_input.channel}/input/range",
                        dev_input.range,
                    )
                )

        for measurement in initialization.measurements:
            channel = 0
            if initialization.config.triggering_mode == TriggeringMode.DESKTOP_LEADER:
                # standalone QA oder QC
                channel = (
                    SOFTWARE_TRIGGER_CHANNEL
                    if self.options.is_qc
                    else INTERNAL_TRIGGER_CHANNEL
                )
            nodes_to_initialize_measurement.append(
                DaqNodeSetAction(
                    self._daq,
                    f"/{self.serial}/qachannels/{measurement.channel}/generator/"
                    f"auxtriggers/0/channel",
                    channel,
                )
            )

        return nodes_to_initialize_measurement

    def collect_trigger_configuration_nodes(
        self, initialization: Initialization.Data, recipe_data: RecipeData
    ) -> list[DaqNodeAction]:
        _logger.debug("Configuring triggers...")
        self._wait_for_awgs = True
        self._emit_trigger = False

        nodes_to_configure_triggers = []

        triggering_mode = initialization.config.triggering_mode

        if triggering_mode == TriggeringMode.ZSYNC_FOLLOWER:
            pass
        elif triggering_mode == TriggeringMode.DESKTOP_LEADER:
            self._wait_for_awgs = False
            self._emit_trigger = True
            if self.options.is_qc:
                int_trig_base = f"/{self.serial}/system/internaltrigger"
                nodes_to_configure_triggers.append(
                    DaqNodeSetAction(self._daq, f"{int_trig_base}/enable", 0)
                )
                nodes_to_configure_triggers.append(
                    DaqNodeSetAction(self._daq, f"{int_trig_base}/repetitions", 1)
                )
        else:
            raise LabOneQControllerException(
                f"Unsupported triggering mode: {triggering_mode} for device type SHFQA."
            )

        for awg_index in (
            self._allocated_awgs if len(self._allocated_awgs) > 0 else range(1)
        ):
            markers_base = f"/{self.serial}/qachannels/{awg_index}/markers"
            src = 32 + awg_index
            nodes_to_configure_triggers.append(
                DaqNodeSetAction(self._daq, f"{markers_base}/0/source", src),
            )
            nodes_to_configure_triggers.append(
                DaqNodeSetAction(self._daq, f"{markers_base}/1/source", src),
            )
        return nodes_to_configure_triggers

    def get_measurement_data(
        self,
        channel: int,
        acquisition_type: AcquisitionType,
        result_indices: list[int],
        num_results: int,
        hw_averages: int,
    ):
        assert len(result_indices) == 1
        result_path = f"/{self.serial}/qachannels/{channel}/" + (
            "spectroscopy/result/data/wave"
            if is_spectroscopy(acquisition_type)
            else f"readout/result/data/{result_indices[0]}/wave"
        )
        attempts = 3  # Hotfix HBAR-949
        while attempts > 0:
            attempts -= 1
            # @TODO(andreyk): replace the raw daq reply parsing on site here and hide it
            # inside Communication class
            data_node_query = self._daq.get_raw(result_path)
            actual_num_measurement_points = len(
                data_node_query[result_path][0]["vector"]
            )
            if actual_num_measurement_points < num_results:
                time.sleep(0.1)
                continue
            break
        assert actual_num_measurement_points == num_results, (
            f"number of measurement points {actual_num_measurement_points} returned by daq "
            f"from device '{self.dev_repr}' does not match length of recipe "
            f"measurement_map which is {num_results}"
        )
        result: npt.ArrayLike = data_node_query[result_path][0]["vector"]
        if acquisition_type == AcquisitionType.DISCRIMINATION:
            return result.real
        return result

    def get_input_monitor_data(self, channel: int, num_results: int):
        result_path_ch = f"/{self.serial}/scopes/0/channels/{channel}/wave"
        node_data = self._daq.get_raw(result_path_ch)
        data = node_data[result_path_ch][0]["vector"][0:num_results]
        return data

    def check_results_acquired_status(
        self, channel, acquisition_type: AcquisitionType, result_length, hw_averages
    ):
        unit = "spectroscopy" if is_spectroscopy(acquisition_type) else "readout"
        results_acquired_path = (
            f"/{self.serial}/qachannels/{channel}/{unit}/result/acquired"
        )
        batch_get_results = self._daq.batch_get(
            [
                DaqNodeGetAction(
                    self._daq,
                    results_acquired_path,
                    caching_strategy=CachingStrategy.NO_CACHE,
                )
            ]
        )
        actual_results = batch_get_results[results_acquired_path]
        expected_results = result_length * hw_averages
        if actual_results != expected_results:
            raise LabOneQControllerException(
                f"The number of measurements ({actual_results}) executed for device {self.serial} "
                f"on channel {channel} does not match the number of measurements "
                f"defined ({expected_results}). Probably the time between measurements or within "
                f"a loop is too short. Please contact Zurich Instruments."
            )

    def collect_reset_nodes(self) -> list[DaqNodeAction]:
        reset_nodes = super().collect_reset_nodes()
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/*/generator/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/*/readout/result/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/*/spectroscopy/psd/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/qachannels/*/spectroscopy/result/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/scopes/0/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        reset_nodes.append(
            DaqNodeSetAction(
                self._daq,
                f"/{self.serial}/scopes/0/channels/*/enable",
                0,
                caching_strategy=CachingStrategy.NO_CACHE,
            )
        )
        return reset_nodes
