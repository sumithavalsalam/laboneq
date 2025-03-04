# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from laboneq import dsl
from laboneq.core.exceptions import LabOneQException
from laboneq.core.types.enums import SectionAlignment
from laboneq.core.validators import validating_allowed_values
from laboneq.dsl.enums import (
    AcquisitionType,
    AveragingMode,
    ExecutionType,
    RepetitionMode,
)
from laboneq.dsl.experiment.pulse import Pulse

from ..dsl_dataclass_decorator import classformatter
from .acquire import Acquire
from .call import Call
from .delay import Delay
from .operation import Operation
from .play_pulse import PlayPulse
from .reserve import Reserve
from .set import Set
from .utils import id_generator

if TYPE_CHECKING:
    from .. import Parameter


@classformatter
@dataclass(init=True, repr=True, order=True)
class Section:
    """Representation of a section. A section is a logical concept that groups multiple operations into a single entity
    that can be though of a container. A section can either contain other sections or a list of operations (but not both
    at the same time). Operations within a section can be aligned in various ways (left, right). Sections can have a offset
    and/or a predefined length, and they can be specified to play after another section.

    .. versionchanged:: 2.0.0
        Removed `offset` member variable.
    """

    #: Unique identifier of the section.
    uid: str = field(default=None)

    #: Alignment of operations and subsections within this section.
    alignment: SectionAlignment = field(default=SectionAlignment.LEFT)

    execution_type: Optional[ExecutionType] = field(default=None)

    #: Minimal length of the section in seconds. The scheduled section might be slightly longer, as its length is rounded to the next multiple of the section timing grid.
    length: Optional[float] = field(default=None)

    #: Play after the section with the given ID.
    play_after: Optional[Union[str, List[str]]] = field(default=None)

    #: List of children. Each child may be another section or an operation.
    children: List[Union[Section, dsl.experiment.operation.Operation]] = field(
        default_factory=list, compare=False
    )

    #: Optional trigger pulses to play during this section. See :meth:`~.Experiment.section`.
    trigger: Dict[str, Dict] = field(default_factory=dict)

    #: Whether to escalate to the system grid even if tighter alignment is possible.
    #: See :meth:`~.Experiment.section`.
    on_system_grid: Optional[bool] = field(default=False)

    def __post_init__(self):
        if self.uid is None:
            self.uid = id_generator("s")

    def add(self, section: Union[Section, Operation, Set]):
        """Add a subsection or operation to the section.

        Args:
            section: Item that is added.
        """
        self.children.append(section)

    @property
    def sections(self) -> Tuple[Section]:
        """A list of subsections of this section"""
        return tuple([s for s in self.children if isinstance(s, Section)])

    @property
    def operations(self) -> Tuple[Operation]:
        """A list of operations in the section.

        Note that there may be other children of a section which are not operations but subsections."""
        return tuple([s for s in self.children if isinstance(s, Operation)])

    def set(self, path: str, value: Any):
        """Set the value of an instrument node.

        Args:
            path: Path to the node whose value should be set.
            value: Value that should be set.
        """
        self.add(Set(path=path, value=value))

    def play(
        self,
        signal,
        pulse,
        amplitude=None,
        phase=None,
        increment_oscillator_phase=None,
        set_oscillator_phase=None,
        length=None,
        pulse_parameters: Optional[Dict[str, Any]] = None,
        precompensation_clear: Optional[bool] = None,
        marker: Optional[Dict[str, Any]] = None,
    ):
        """Play a pulse on a signal.

        Args:
            signal: Signal the pulse should be played on.
            pulse: Pulse that should be played on the signal.
            amplitude: Amplitude of the pulse that should be played.
            phase: Phase of the pulse that should be played.
            pulse_parameters: Dictionary with user pulse function parameters (re)binding.
            precompensation_clear: Clear the precompensation filter during the pulse.
            marker: Instruction for playing marker signals along with the pulse
        """
        self.add(
            PlayPulse(
                signal,
                pulse,
                amplitude=amplitude,
                phase=phase,
                increment_oscillator_phase=increment_oscillator_phase,
                set_oscillator_phase=set_oscillator_phase,
                length=length,
                pulse_parameters=pulse_parameters,
                precompensation_clear=precompensation_clear,
                marker=marker,
            )
        )

    def reserve(self, signal):
        """Operation to reserve a signal for the active section.
        Reserving an experiment signal in a section means that if there is no
        operation defined on that signal, it is not available for other sections
        as long as the active section is scoped.

        Args:
            signal: Signal that should be reserved.
        """
        self.add(Reserve(signal))

    def acquire(
        self,
        signal: str,
        handle: str,
        kernel: Pulse = None,
        length: float = None,
        pulse_parameters: Optional[Dict[str, Any]] = None,
    ):
        """Acquisition of results of a signal.

        Args:
            signal: Unique identifier of the signal where the result should be acquired.
            handle: Unique identifier of the handle that will be used to access the acquired result.
            kernel: Pulse base used for the acquisition.
            length: Integration length (only valid in spectroscopy mode).
            pulse_parameters: Dictionary with user pulse function parameters (re)binding.
        """
        self.add(
            Acquire(
                signal=signal,
                handle=handle,
                kernel=kernel,
                length=length,
                pulse_parameters=pulse_parameters,
            )
        )

    def measure(
        self,
        acquire_signal: str,
        handle: str,
        integration_kernel: Optional[Pulse] = None,
        integration_kernel_parameters: Optional[Dict[str, Any]] = None,
        integration_length: Optional[float] = None,
        measure_signal: Optional[str] = None,
        measure_pulse: Optional[Pulse] = None,
        measure_pulse_length: Optional[float] = None,
        measure_pulse_parameters: Optional[Dict[str, Any]] = None,
        measure_pulse_amplitude: Optional[float] = None,
        acquire_delay: Optional[float] = None,
        reset_delay: Optional[float] = None,
    ):
        """
        Execute a measurement.

        Unifies the optional playback of a measurement pulse, the acquisition of the return signal and an optional delay after the signal acquisition.

        For pulsed spectroscopy, set `integration_length` and either `measure_pulse` or `measure_pulse_length`.
        For CW spectroscopy, set only `integration_length` and do not specify the measure signal.
        For all other measurements, set either length or pulse for both the measure pulse and integration kernel.

        Args:

            acquire_signal: A string that specifies the signal for the data acquisition.
            handle: A string that specifies the handle of the acquired results.
            integration_kernel: An optional Pulse object that specifies the kernel for integration.
            integration_kernel_parameters: An optional dictionary that contains pulse parameters for the integration kernel.
            integration_length: An optional float that specifies the integration length.
            measure_signal: An optional string that specifies the signal to measure.
            measure_pulse: An optional Pulse object that specifies the readout pulse for measurement.

                If this parameter is not supplied, no pulse will be played back for the measurement,
                which enables CW spectroscopy on SHFQA instruments.

            measure_pulse_length: An optional float that specifies the length of the measurement pulse.
            measure_pulse_parameters: An optional dictionary that contains parameters for the measurement pulse.
            measure_pulse_amplitude: An optional float that specifies the amplitude of the measurement pulse.
            acquire_delay: An optional float that specifies the delay between the acquisition and the measurement.
            reset_delay: An optional float that specifies the delay after the acquisition to allow for state relaxation or signal processing.
        """
        if not isinstance(acquire_signal, str):
            raise TypeError("`acquire_signal` must be a string.")

        if measure_signal is None:
            self.acquire(
                signal=acquire_signal,
                handle=handle,
                length=integration_length,
            )

        elif isinstance(measure_signal, str):
            self.play(
                signal=measure_signal,
                pulse=measure_pulse,
                amplitude=measure_pulse_amplitude,
                length=measure_pulse_length,
                pulse_parameters=measure_pulse_parameters,
            )

            if acquire_delay is not None:
                self.delay(
                    signal=acquire_signal,
                    time=acquire_delay,
                )

            self.acquire(
                signal=acquire_signal,
                handle=handle,
                kernel=integration_kernel,
                length=integration_length,
                pulse_parameters=integration_kernel_parameters,
            )

        if reset_delay is not None:
            self.delay(
                signal=acquire_signal,
                time=reset_delay,
            )

    def delay(
        self,
        signal: str,
        time: Union[float, Parameter],
        precompensation_clear: Optional[bool] = None,
    ):
        """Adds a delay on the signal with a specified time.

        Args:
            signal: Unique identifier of the signal where the delay should be applied.
            time: Duration of the delay.
            precompensation_clear: Clear the precompensation filter during the delay.
        """
        self.add(
            Delay(signal=signal, time=time, precompensation_clear=precompensation_clear)
        )

    def call(self, func_name, **kwargs):
        """Function call.

        Args:
            func_name (Union[str, Callable]): Function that should be called.
            kwargs: Arguments of the function call.
        """
        self.add(Call(func_name=func_name, **kwargs))


@classformatter
@dataclass(init=True, repr=True, order=True)
class AcquireLoopNt(Section):
    """Near time acquire loop."""

    #: Averaging method. One of sequential, cyclic and single_shot.
    averaging_mode: AveragingMode = field(default=AveragingMode.CYCLIC)
    #: Number of loops.
    count: int = field(default=None)
    execution_type: ExecutionType = field(default=ExecutionType.NEAR_TIME)


@classformatter
@dataclass(init=True, repr=True, order=True)
class AcquireLoopRt(Section):
    """Real time acquire loop."""

    #: Type of the acquisition. One of integration trigger, spectroscopy, discrimination, demodulation and RAW. The default acquisition type is INTEGRATION.
    acquisition_type: AcquisitionType = field(default=AcquisitionType.INTEGRATION)
    #: Averaging method. One of sequential, cyclic and single_shot.
    averaging_mode: AveragingMode = field(default=AveragingMode.CYCLIC)
    #: Number of loops.
    count: int = field(default=None)
    execution_type: ExecutionType = field(default=ExecutionType.REAL_TIME)
    #: Repetition method. One of fastest, constant and auto.
    repetition_mode: RepetitionMode = field(default=RepetitionMode.FASTEST)
    #: The repetition time, when :py:attr:`repetition_mode` is :py:attr:`~.RepetitionMode.CONSTANT`
    repetition_time: float = field(default=None)
    #: When True, reset all oscillators at the start of every step.
    reset_oscillator_phase: bool = field(default=False)

    def __post_init__(self):
        super().__post_init__()
        if self.repetition_mode == RepetitionMode.CONSTANT:
            if self.repetition_time is None:
                raise LabOneQException(
                    f"AcquireLoopRt with uid {self.uid} has RepetitionMode.CONSTANT but repetition_time is not set"
                )


@classformatter
@dataclass(init=True, repr=True, order=True)
class Sweep(Section):
    """Sweep loops. Sweeps are used to sample through a range of parameter values."""

    #: Parameters that should be swept.
    parameters: List[Parameter] = field(default_factory=list)
    #: When True, reset all oscillators at the start of every step.
    reset_oscillator_phase: bool = field(default=False)
    #: When non-zero, split the sweep into N chunks.
    chunk_count: int = field(default=1)


@validating_allowed_values(
    {
        "alignment": [SectionAlignment.LEFT],
        "execution_type": [ExecutionType.REAL_TIME],
    }
)
@classformatter
@dataclass(init=True, repr=True, order=True)
class Match(Section):
    """Execute one of the child branches depending on feedback result."""

    #: Handle from which to obtain results
    handle: str = ""

    #: Whether to go via the PQSC (False) or SHFQC (True)
    local: bool = False

    def add(self, case: Case):
        """Add a branch to which to switch.

        Args:
            case: Branch that is added.
        """
        if not isinstance(case, Case):
            raise LabOneQException(
                f"Trying to add section to section {self.uid} which is not of type 'Case'."
            )
        if any(c.state == case.state for c in self.sections):
            raise LabOneQException(
                f"A branch which matches {case.state} already exists."
            )
        super().add(case)


@validating_allowed_values(
    {
        "alignment": [SectionAlignment.LEFT],
        "execution_type": [ExecutionType.REAL_TIME],
    }
)
@classformatter
@dataclass(init=True, repr=True, order=True)
class Case(Section):
    """Branch in a match/case statement"""

    state: int = 0

    def add(self, obj):
        if not isinstance(obj, (PlayPulse, Delay)):
            raise LabOneQException(
                f"Trying to add object to section {self.uid}. Only ``play`` and ``delay`` are allowed."
            )
        super().add(obj)

    @classmethod
    def from_section(cls, section, state):
        """Down-cast from Section."""
        return cls(**section.__dict__, state=state)  # type: ignore
