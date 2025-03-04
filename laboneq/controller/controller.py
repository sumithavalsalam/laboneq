# Copyright 2019 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import concurrent.futures
import itertools
import logging
import os
import time
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import zhinst.utils
from numpy import typing as npt

from laboneq import __version__
from laboneq._observability import tracing
from laboneq.controller.communication import (
    DaqNodeAction,
    DaqNodeSetAction,
    DaqWrapper,
    batch_set,
)
from laboneq.controller.devices.device_collection import DeviceCollection
from laboneq.controller.devices.device_uhfqa import DeviceUHFQA
from laboneq.controller.devices.device_zi import Waveforms
from laboneq.controller.devices.zi_node_monitor import ResponseWaiter
from laboneq.controller.near_time_runner import NearTimeRunner
from laboneq.controller.recipe_1_4_0 import *  # noqa: F401, F403
from laboneq.controller.recipe_processor import (
    RecipeData,
    RtExecutionInfo,
    pre_process_compiled,
)
from laboneq.controller.results import (
    build_partial_result,
    make_acquired_result,
    make_empty_results,
)
from laboneq.controller.util import LabOneQControllerException
from laboneq.core.types.enums.acquisition_type import AcquisitionType
from laboneq.core.types.enums.averaging_mode import AveragingMode
from laboneq.core.utilities.replace_pulse import ReplacementType, calc_wave_replacements
from laboneq.data.execution_payload import TargetSetup
from laboneq.executor.execution_from_experiment import ExecutionFactoryFromExperiment
from laboneq.executor.executor import Statement

if TYPE_CHECKING:
    from laboneq.controller.devices.device_zi import DeviceZI
    from laboneq.core.types import CompiledExperiment
    from laboneq.data.execution_payload import ExecutionPayload
    from laboneq.dsl import Session
    from laboneq.dsl.experiment.pulse import Pulse
    from laboneq.dsl.result.results import Results


_logger = logging.getLogger(__name__)


# Only recheck for the proper connected state if there was no check since more than
# the below amount of seconds. Important for performance with many small experiments
# executed in a batch.
CONNECT_CHECK_HOLDOFF = 10  # sec


class ControllerRunParameters:
    shut_down: bool = False
    dry_run: bool = False
    disconnect: bool = False
    working_dir: str = "laboneq_output"
    setup_filename = None
    servers_filename = None
    ignore_version_mismatch = False
    reset_devices = False


# atexit hook
def _stop_controller(controller: "Controller"):
    controller.shut_down()


@dataclass
class _SeqCCompileItem:
    awg_index: int
    seqc_code: str | None = None
    seqc_filename: str | None = None
    elf: bytes | None = None


@dataclass
class _UploadItem:
    seqc_item: _SeqCCompileItem | None
    waves: Waveforms | None
    command_table: dict[Any] | None


class Controller:
    def __init__(
        self,
        run_parameters: ControllerRunParameters = None,
        target_setup: TargetSetup = None,
        user_functions: dict[str, Callable] = None,
    ):
        self._run_parameters = run_parameters or ControllerRunParameters()
        self._devices = DeviceCollection(
            target_setup,
            self._run_parameters.dry_run,
            self._run_parameters.ignore_version_mismatch,
            self._run_parameters.reset_devices,
        )

        self._last_connect_check_ts: float = None

        # Waves which are uploaded to the devices via pulse replacements
        self._current_waves = []
        self._user_functions: dict[str, Callable] = user_functions
        self._nodes_from_user_functions: list[DaqNodeAction] = []
        self._recipe_data: RecipeData = None
        self._session = None
        self._results: Results = None

        _logger.debug("Controller created")
        _logger.debug("Controller debug logging is on")

        _logger.info("VERSION: laboneq %s", __version__)

    def _allocate_resources(self):
        self._devices.free_allocations()
        osc_params = self._recipe_data.recipe.experiment.oscillator_params
        for osc_param in sorted(osc_params, key=lambda p: p.id):
            self._devices.find_by_uid(osc_param.device_id).allocate_osc(osc_param)

    def _reset_to_idle_state(self):
        reset_nodes = []
        for _, device in self._devices.all:
            reset_nodes.extend(device.collect_reset_nodes())
        batch_set(reset_nodes)

    def _apply_recipe_initializations(self):
        nodes_to_initialize: list[DaqNodeAction] = []
        for initialization in self._recipe_data.initializations:
            device = self._devices.find_by_uid(initialization.device_uid)
            nodes_to_initialize.extend(
                device.collect_initialization_nodes(
                    self._recipe_data.device_settings[initialization.device_uid],
                    initialization,
                )
            )
            nodes_to_initialize.extend(device.collect_osc_initialization_nodes())

        batch_set(nodes_to_initialize)

    def _set_nodes_before_awg_program_upload(self):
        nodes_to_initialize = []
        for initialization in self._recipe_data.initializations:
            device = self._devices.find_by_uid(initialization.device_uid)
            nodes_to_initialize.extend(
                device.collect_awg_before_upload_nodes(
                    initialization, self._recipe_data
                )
            )
        batch_set(nodes_to_initialize)

    @tracing.trace("awg-program-handler")
    def _upload_awg_programs(self, nt_step: NtStepKey):
        # Mise en place:
        awg_data: dict[DeviceZI, list[_UploadItem]] = defaultdict(list)
        compile_data: dict[DeviceZI, list[_SeqCCompileItem]] = defaultdict(list)
        recipe_data = self._recipe_data
        acquisition_type = RtExecutionInfo.get_acquisition_type(
            recipe_data.rt_execution_infos
        )
        for initialization in recipe_data.initializations:
            device = self._devices.find_by_uid(initialization.device_uid)

            if initialization.awgs is None:
                continue

            for awg_obj in initialization.awgs:
                awg_index = awg_obj.awg
                rt_exec_step = next(
                    (
                        r
                        for r in recipe_data.recipe.experiment.realtime_execution_init
                        if r.device_id == initialization.device_uid
                        and r.awg_id == awg_obj.awg
                        and r.nt_step == nt_step
                    ),
                    None,
                )
                if rt_exec_step is None:
                    continue

                seqc_code = device.prepare_seqc(
                    recipe_data.scheduled_experiment, rt_exec_step.seqc_ref
                )
                waves = device.prepare_waves(
                    recipe_data.scheduled_experiment, rt_exec_step.wave_indices_ref
                )
                command_table = device.prepare_command_table(
                    recipe_data.scheduled_experiment, rt_exec_step.wave_indices_ref
                )

                seqc_item = _SeqCCompileItem(
                    awg_index=awg_index,
                )

                if seqc_code is not None:
                    seqc_item.seqc_code = seqc_code
                    seqc_item.seqc_filename = rt_exec_step.seqc_ref
                    compile_data[device].append(seqc_item)

                awg_data[device].append(
                    _UploadItem(
                        seqc_item=seqc_item,
                        waves=waves,
                        command_table=command_table,
                    )
                )

        if compile_data:
            self._awg_compile(compile_data)

        # Upload AWG programs, waveforms, and command tables:
        elf_node_settings: dict[DaqWrapper, list[DaqNodeSetAction]] = defaultdict(list)
        elf_upload_conditions: dict[DaqWrapper, dict[str, Any]] = defaultdict(dict)
        wf_node_settings: dict[DaqWrapper, list[DaqNodeSetAction]] = defaultdict(list)
        for device, items in awg_data.items():
            for item in items:
                seqc_item = item.seqc_item
                if seqc_item.elf is not None:
                    set_action = device.prepare_upload_elf(
                        seqc_item.elf, seqc_item.awg_index, seqc_item.seqc_filename
                    )
                    node_settings = elf_node_settings[device.daq]
                    node_settings.append(set_action)

                    if isinstance(device, DeviceUHFQA):
                        # UHFQA does not yet support upload of ELF and waveforms in
                        # a single transaction.
                        ready_node = device.get_sequencer_paths(
                            seqc_item.awg_index
                        ).ready
                        elf_upload_conditions[device.daq][ready_node] = 1

                if isinstance(device, DeviceUHFQA):
                    wf_dev_nodes = wf_node_settings[device.daq]
                else:
                    wf_dev_nodes = elf_node_settings[device.daq]

                if item.waves is not None:
                    wf_dev_nodes += device.prepare_upload_all_binary_waves(
                        seqc_item.awg_index, item.waves, acquisition_type
                    )

                if item.command_table is not None:
                    set_action = device.prepare_upload_command_table(
                        seqc_item.awg_index, item.command_table
                    )
                    wf_dev_nodes.append(set_action)

        if len(elf_upload_conditions) > 0:
            for daq in elf_upload_conditions.keys():
                daq.node_monitor.flush()

        _logger.debug("Started upload of AWG programs...")
        with tracing.get_tracer().start_span("upload-awg-programs") as _:
            for daq, nodes in elf_node_settings.items():
                daq.batch_set(nodes)

            if len(elf_upload_conditions) > 0:
                _logger.debug("Waiting for devices...")
                response_waiter = ResponseWaiter()
                for daq, conditions in elf_upload_conditions.items():
                    response_waiter.add(
                        target=daq.node_monitor,
                        conditions=conditions,
                    )
                timeout_s = 10
                if not response_waiter.wait_all(timeout=timeout_s):
                    raise LabOneQControllerException(
                        f"AWGs not in ready state within timeout ({timeout_s} s)."
                    )
            if len(wf_node_settings) > 0:
                _logger.debug("Started upload of waveforms...")
                with tracing.get_tracer().start_span("upload-waveforms") as _:
                    for daq, nodes in wf_node_settings.items():
                        daq.batch_set(nodes)
        _logger.debug("Finished upload.")

    @classmethod
    def _awg_compile(cls, awg_data: dict[DeviceZI, list[_SeqCCompileItem]]):
        # Compile in parallel:
        def worker(device: DeviceZI, item: _SeqCCompileItem, span: tracing.Span):
            with tracing.get_tracer().start_span("compile-awg-thread", span) as _:
                item.elf = device.compile_seqc(
                    item.seqc_code, item.awg_index, item.seqc_filename
                )

        _logger.debug("Started compilation of AWG programs...")
        with tracing.get_tracer().start_span("compile-awg-programs") as awg_span:
            max_workers = os.environ.get("LABONEQ_AWG_COMPILER_MAX_WORKERS")
            max_workers = int(max_workers) if max_workers is not None else None
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                futures = [
                    executor.submit(worker, device, item, awg_span)
                    for device, items in awg_data.items()
                    for item in items
                ]
                concurrent.futures.wait(futures)
                exceptions = [
                    future.exception()
                    for future in futures
                    if future.exception() is not None
                ]
                if len(exceptions) > 0:
                    raise LabOneQControllerException(
                        "Compilation failed. See log output for details."
                    )
        _logger.debug("Finished compilation.")

    def _set_nodes_after_awg_program_upload(self):
        nodes_to_initialize = []
        for initialization in self._recipe_data.initializations:
            device = self._devices.find_by_uid(initialization.device_uid)
            nodes_to_initialize.extend(
                device.collect_awg_after_upload_nodes(initialization)
            )

        batch_set(nodes_to_initialize)

    def _initialize_awgs(self, nt_step: NtStepKey):
        self._set_nodes_before_awg_program_upload()
        self._upload_awg_programs(nt_step=nt_step)
        self._set_nodes_after_awg_program_upload()

    def _configure_triggers(self):
        nodes_to_configure_triggers = []

        for uid, device in itertools.chain(
            self._devices.leaders, self._devices.followers
        ):
            init = self._recipe_data.get_initialization_by_device_uid(uid)
            if init is None:
                continue
            nodes_to_configure_triggers.extend(
                device.collect_trigger_configuration_nodes(init, self._recipe_data)
            )

        batch_set(nodes_to_configure_triggers)

    def _initialize_devices(self):
        self._reset_to_idle_state()
        self._allocate_resources()
        self._apply_recipe_initializations()

    def _execute_one_step_followers(self):
        _logger.debug("Settings nodes to start on followers")

        nodes_to_execute = []
        for _, device in self._devices.followers:
            nodes_to_execute.extend(device.collect_execution_nodes())

        batch_set(nodes_to_execute)

        response_waiter = ResponseWaiter()
        for _, device in self._devices.followers:
            response_waiter.add(
                target=device.daq.node_monitor,
                conditions=device.conditions_for_execution_ready(),
            )
        if not response_waiter.wait_all(timeout=2):
            _logger.warning(
                "Conditions to start RT on followers still not fulfilled after 2"
                " seconds, nonetheless trying to continue..."
            )

        # Standalone workaround: The device is triggering itself,
        # thus split the execution into AWG trigger arming and triggering
        nodes_to_execute = []
        for _, device in self._devices.followers:
            nodes_to_execute.extend(device.collect_start_execution_nodes())

        batch_set(nodes_to_execute)

    def _execute_one_step_leaders(self):
        _logger.debug("Settings nodes to start on leaders")
        nodes_to_execute = []

        for _, device in self._devices.leaders:
            nodes_to_execute.extend(device.collect_execution_nodes())
        batch_set(nodes_to_execute)

    def _wait_execution_to_stop(self, acquisition_type: AcquisitionType):
        min_wait_time = self._recipe_data.recipe.experiment.max_step_execution_time
        if min_wait_time is None:
            _logger.warning(
                "No estimation available for the execution time, assuming 10 sec."
            )
            min_wait_time = 10.0
        elif min_wait_time > 5:  # Only inform about RT executions taking longer than 5s
            _logger.info("Estimated RT execution time: %.2f s.", min_wait_time)
        guarded_wait_time = round(
            min_wait_time * 1.1 + 1
        )  # +10% and fixed 1sec guard time

        response_waiter = ResponseWaiter()
        for _, device in self._devices.followers:
            response_waiter.add(
                target=device.daq.node_monitor,
                conditions=device.conditions_for_execution_done(acquisition_type),
            )
        if not response_waiter.wait_all(timeout=guarded_wait_time):
            _logger.warning(
                (
                    "Stop conditions still not fulfilled after %f s, estimated"
                    " execution time was %.2f s. Continuing to the next step."
                ),
                guarded_wait_time,
                min_wait_time,
            )

    def _execute_one_step(self, acquisition_type: AcquisitionType):
        _logger.debug("Step executing")

        self._devices.flush_monitor()

        # Can't batch everything together, because PQSC needs to be executed after HDs
        # otherwise it can finish before AWGs are started, and the trigger is lost
        self._execute_one_step_followers()
        self._execute_one_step_leaders()

        _logger.debug("Execution started")

        self._wait_execution_to_stop(acquisition_type)

        _logger.debug("Execution stopped")

    def connect(self):
        now = time.time()
        if (
            self._last_connect_check_ts is None
            or now - self._last_connect_check_ts > CONNECT_CHECK_HOLDOFF
        ):
            self._devices.connect()
        self._last_connect_check_ts = now

    def disable_outputs(
        self,
        device_uids: list[str] = None,
        logical_signals: list[str] = None,
        unused_only: bool = False,
    ):
        self._devices.disable_outputs(device_uids, logical_signals, unused_only)

    def shut_down(self):
        _logger.info("Shutting down all devices...")
        self._devices.shut_down()
        _logger.info("Successfully Shut down all devices.")

    def disconnect(self):
        _logger.info("Disconnecting from all devices and servers...")
        self._devices.disconnect()
        self._last_connect_check_ts = None
        _logger.info("Successfully disconnected from all devices and servers.")

    # TODO(2K): remove legacy code
    def execute_compiled_legacy(
        self, compiled_experiment: CompiledExperiment, session: Session = None
    ):
        execution: Statement
        if hasattr(compiled_experiment.scheduled_experiment, "execution"):
            execution = compiled_experiment.scheduled_experiment.execution
        else:
            execution = ExecutionFactoryFromExperiment().make(
                compiled_experiment.experiment
            )

        self._recipe_data = pre_process_compiled(
            compiled_experiment.scheduled_experiment, self._devices, execution
        )

        self._session = session
        if session is None:
            self._results = None
        else:
            self._results = session._last_results

        self._execute_compiled_impl()

    def execute_compiled(self, job: ExecutionPayload):
        self._recipe_data = pre_process_compiled(
            job.scheduled_experiment,
            self._devices,
            job.scheduled_experiment.execution,
        )
        self._results = None

        self._execute_compiled_impl()

    def _execute_compiled_impl(self):
        self.connect()  # Ensure all connect configurations are still valid!
        self._prepare_result_shapes()
        try:
            self._devices.start_monitor()
            self._initialize_devices()

            # Ensure no side effects from the previous execution in the same session
            self._current_waves = []
            self._nodes_from_user_functions = []
            _logger.info("Starting near-time execution...")
            with tracing.get_tracer().start_span("near-time-execution"):
                NearTimeRunner(controller=self).run(self._recipe_data.execution)
            _logger.info("Finished near-time execution.")
            for _, device in self._devices.all:
                device.check_errors()
        finally:
            self._devices.stop_monitor()

        self._devices.on_experiment_end()

        if self._run_parameters.shut_down is True:
            self.shut_down()

        if self._run_parameters.disconnect is True:
            self.disconnect()

    def _find_awg(self, seqc_name: str) -> tuple[str, int]:
        # TODO(2K): Do this in the recipe preprocessor, or even modify the compiled experiment
        #  data model
        for rt_exec_step in self._recipe_data.recipe.experiment.realtime_execution_init:
            if rt_exec_step.seqc_ref == seqc_name:
                return rt_exec_step.device_id, rt_exec_step.awg_id
        return None, None

    def replace_pulse(
        self, pulse_uid: str | Pulse, pulse_or_array: npt.ArrayLike | Pulse
    ):
        """Replaces specific pulse with the new sample data on the device.

        This is useful when called from the user function, allows fast waveform replacement within
        near-time loop without experiment recompilation.

        Args:
            pulse_uid: pulse to replace, can be Pulse object or uid of the pulse
            pulse_or_array: replacement pulse, can be Pulse object or value array
            (see sampled_pulse_* from the pulse library)
        """
        if isinstance(pulse_uid, str):
            for waveform in self._recipe_data.scheduled_experiment.pulse_map[
                pulse_uid
            ].waveforms.values():
                if any([instance.can_compress for instance in waveform.instances]):
                    _logger.error(
                        (
                            "Pulse replacement on pulses that allow compression not"
                            " allowed. Pulse %s"
                        ),
                        pulse_uid,
                    )
                    return

        if hasattr(pulse_uid, "can_compress") and pulse_uid.can_compress:
            _logger.error(
                (
                    "Pulse replacement on pulses that allow compression not allowed."
                    " Pulse %s"
                ),
                pulse_uid.uid,
            )
            return

        acquisition_type = RtExecutionInfo.get_acquisition_type(
            self._recipe_data.rt_execution_infos
        )
        wave_replacements = calc_wave_replacements(
            self._recipe_data.scheduled_experiment,
            pulse_uid,
            pulse_or_array,
            self._current_waves,
        )
        for repl in wave_replacements:
            awg_indices = next(
                a
                for a in self._recipe_data.scheduled_experiment.wave_indices
                if a["filename"] == repl.awg_id
            )
            awg_wave_map: dict[str, list[int | str]] = awg_indices["value"]
            target_wave = awg_wave_map.get(repl.sig_string)
            seqc_name = repl.awg_id
            awg = self._find_awg(seqc_name)
            device = self._devices.find_by_uid(awg[0])

            if repl.replacement_type == ReplacementType.I_Q:
                clipped = np.clip(repl.samples, -1.0, 1.0)
                bin_wave = zhinst.utils.convert_awg_waveform(*clipped)
                self._nodes_from_user_functions.append(
                    device.prepare_upload_binary_wave(
                        filename=repl.sig_string + " (repl)",
                        waveform=bin_wave,
                        awg_index=awg[1],
                        wave_index=target_wave[0],
                        acquisition_type=acquisition_type,
                    )
                )
            elif repl.replacement_type == ReplacementType.COMPLEX:
                np.clip(repl.samples.real, -1.0, 1.0, out=repl.samples.real)
                np.clip(repl.samples.imag, -1.0, 1.0, out=repl.samples.imag)
                self._nodes_from_user_functions.append(
                    device.prepare_upload_binary_wave(
                        filename=repl.sig_string + " (repl)",
                        waveform=repl.samples,
                        awg_index=awg[1],
                        wave_index=target_wave[0],
                        acquisition_type=acquisition_type,
                    )
                )

    def _prepare_rt_execution(self, rt_section_uid: str) -> list[DaqNodeAction]:
        if rt_section_uid is None:
            return [], []  # Old recipe-based execution - skip RT preparation
        rt_execution_info = self._recipe_data.rt_execution_infos[rt_section_uid]
        self._nodes_from_user_functions.sort(key=lambda v: v.path)
        nodes_to_prepare_rt = [*self._nodes_from_user_functions]
        self._nodes_from_user_functions.clear()
        for _, device in self._devices.leaders:
            nodes_to_prepare_rt.extend(device.configure_feedback(self._recipe_data))
        for awg_key, awg_config in self._recipe_data.awgs_producing_results():
            device = self._devices.find_by_uid(awg_key.device_uid)
            if rt_execution_info.averaging_mode == AveragingMode.SINGLE_SHOT:
                effective_averages = 1
                effective_averaging_mode = AveragingMode.CYCLIC
                # TODO(2K): handle sequential
            else:
                effective_averages = rt_execution_info.averages
                effective_averaging_mode = rt_execution_info.averaging_mode
            nodes_to_prepare_rt.extend(
                device.configure_acquisition(
                    awg_key,
                    awg_config,
                    self._recipe_data.recipe.experiment.integrator_allocations,
                    effective_averages,
                    effective_averaging_mode,
                    rt_execution_info.acquisition_type,
                )
            )
        return nodes_to_prepare_rt

    def _prepare_result_shapes(self):
        if self._results is None:
            self._results = make_empty_results()
        if len(self._recipe_data.rt_execution_infos) == 0:
            return
        if len(self._recipe_data.rt_execution_infos) > 1:
            raise LabOneQControllerException(
                "Multiple 'acquire_loop_rt' sections per experiment is not supported."
            )
        rt_info = next(iter(self._recipe_data.rt_execution_infos.values()))
        for handle, shape_info in self._recipe_data.result_shapes.items():
            if rt_info.acquisition_type == AcquisitionType.RAW:
                if len(self._recipe_data.result_shapes) > 1:
                    raise LabOneQControllerException(
                        "Multiple raw acquire events with handles "
                        f"{list(self._recipe_data.result_shapes.keys())}. "
                        "Only single raw acquire per experiment allowed."
                    )
                signal_id = rt_info.signal_by_handle(handle)
                awg_config = self._recipe_data.awg_config_by_acquire_signal(signal_id)
                # Use default length 4096, in case AWG config is not available
                raw_acquire_length = (
                    4096 if awg_config is None else awg_config.raw_acquire_length
                )
                empty_res = make_acquired_result(
                    data=np.empty(shape=[raw_acquire_length], dtype=np.complex128),
                    axis_name=["samples"],
                    axis=[np.arange(raw_acquire_length)],
                )
                empty_res.data[:] = np.nan
                self._results.acquired_results[handle] = empty_res
                return  # Only one result supported in RAW mode
            axis_name = deepcopy(shape_info.base_axis_name)
            axis = deepcopy(shape_info.base_axis)
            shape = deepcopy(shape_info.base_shape)
            if shape_info.additional_axis > 1:
                axis_name.append(handle)
                axis.append(
                    np.linspace(
                        0, shape_info.additional_axis - 1, shape_info.additional_axis
                    )
                )
                shape.append(shape_info.additional_axis)
            empty_res = make_acquired_result(
                data=np.empty(shape=tuple(shape), dtype=np.complex128),
                axis_name=axis_name,
                axis=axis,
            )
            if len(shape) == 0:
                empty_res.data = np.nan
            else:
                empty_res.data[:] = np.nan
            self._results.acquired_results[handle] = empty_res

    def _read_one_step_results(self, nt_step: NtStepKey, rt_section_uid: str):
        if rt_section_uid is None:
            return  # Old recipe-based execution - skip partial result processing
        rt_execution_info = self._recipe_data.rt_execution_infos[rt_section_uid]
        for awg_key, awg_config in self._recipe_data.awgs_producing_results():
            device = self._devices.find_by_uid(awg_key.device_uid)
            if rt_execution_info.acquisition_type == AcquisitionType.RAW:
                raw_results = device.get_input_monitor_data(
                    awg_key.awg_index, awg_config.raw_acquire_length
                )
                # Copy to all result handles, but actually only one handle is supported for now
                for signal in awg_config.acquire_signals:
                    mapping = rt_execution_info.signal_result_map.get(signal, [])
                    unique_handles = set(mapping)
                    for handle in unique_handles:
                        result = self._results.acquired_results[handle]
                        for raw_result_idx, raw_result in enumerate(raw_results):
                            result.data[raw_result_idx] = raw_result
            else:
                if rt_execution_info.averaging_mode == AveragingMode.SINGLE_SHOT:
                    effective_averages = 1
                else:
                    effective_averages = rt_execution_info.averages
                device.check_results_acquired_status(
                    awg_key.awg_index,
                    rt_execution_info.acquisition_type,
                    awg_config.result_length,
                    effective_averages,
                )
                for signal in awg_config.acquire_signals:
                    integrator_allocation = next(
                        i
                        for i in self._recipe_data.recipe.experiment.integrator_allocations
                        if i.signal_id == signal
                    )
                    assert integrator_allocation.device_id == awg_key.device_uid
                    assert integrator_allocation.awg == awg_key.awg_index
                    result_indices = integrator_allocation.channels
                    raw_results = device.get_measurement_data(
                        awg_key.awg_index,
                        rt_execution_info.acquisition_type,
                        result_indices,
                        awg_config.result_length,
                        effective_averages,
                    )
                    mapping = rt_execution_info.signal_result_map.get(signal, [])
                    unique_handles = set(mapping)
                    for handle in unique_handles:
                        if handle is None:
                            continue  # unused entries in sparse result vector map to None handle
                        result = self._results.acquired_results[handle]
                        build_partial_result(
                            result, nt_step, raw_results, mapping, handle
                        )

    def _report_step_error(self, nt_step: NtStepKey, rt_section_uid: str, message: str):
        self._results.execution_errors.append(
            (list(nt_step.indices), rt_section_uid, message)
        )
