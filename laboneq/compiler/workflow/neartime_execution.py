# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from builtins import frozenset
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from numpy.typing import ArrayLike

from laboneq.compiler.scheduler.parameter_store import ParameterStore
from laboneq.compiler.workflow import rt_linker
from laboneq.compiler.workflow.realtime_compiler import (
    RealtimeCompiler,
    RealtimeCompilerOutput,
)
from laboneq.compiler.workflow.rt_linker import CombinedRealtimeCompilerOutput
from laboneq.executor.executor import (
    ExecRT,
    ExecutorBase,
    LoopFlags,
    LoopingMode,
    Sequence,
)


@dataclass
class IterationStep:
    #: The index of this iteration (aka the iteration in this loop)
    index: int

    #: The values of the near-time parameters for this iteration, not including
    #: parameters from the parent loop
    parameter_values: Dict[str, Any]


@dataclass
class IterationStack:
    _stack: List[IterationStep] = field(default_factory=list)

    def push(self, index: int, parameter_values: Dict[str, Any]):
        self._stack.append(IterationStep(index, parameter_values))

    def pop(self):
        return self._stack.pop()

    def nt_loop_indices(self):
        return tuple(step.index for step in self._stack)

    def nt_parameter_values(self):
        return {k: v for step in self._stack for k, v in step.parameter_values.items()}

    def set_parameter_value(self, name: str, value: Any):
        self._stack[-1].parameter_values[name] = value


def legacy_execution_program():
    """Near-time seqc reloading not supported in JSON mode.

    Use dummy execution to emulate legacy behaviour."""

    # `None` as placeholder is acceptable here. Currently the executor requires none of
    # these.
    return ExecRT(
        count=1, body=Sequence(), uid="", acquisition_type=None, averaging_mode=None
    )


class NtCompilerExecutor(ExecutorBase):
    def __init__(self, rt_compiler: RealtimeCompiler):
        super().__init__(looping_mode=LoopingMode.EXECUTE)
        self._rt_compiler = rt_compiler
        self._iteration_stack = IterationStack()

        self._compiler_output_by_param_values: Dict[
            frozenset, RealtimeCompilerOutput
        ] = {}
        self._last_compiler_output: Optional[RealtimeCompilerOutput] = None
        self._required_parameters: Optional[Set[str]] = None
        self._combined_compiler_output: Optional[CombinedRealtimeCompilerOutput] = None

    def set_sw_param_handler(
        self,
        name: str,
        index: int,
        value: float,
        axis_name: str,
        values: ArrayLike,
    ):
        super().set_sw_param_handler(name, index, value, axis_name, values)
        self._iteration_stack.set_parameter_value(name, value)

    @contextmanager
    def for_loop_handler(self, count: int, index: int, loop_flags: LoopFlags):
        self._iteration_stack.push(index, {})
        # the name & value will be set by the set_sw_param_handler

        yield

        self._iteration_stack.pop()

    @contextmanager
    def rt_handler(
        self,
        _count: int,
        _uid: str,
        _averaging_mode,
        _acquisition_type,
    ):
        if self._required_parameters is not None:
            # We already know what subset of the near-time parameters are required
            # by the real-time sequence. If we already have a compiler output for
            # that state, we can skip the compilation.
            requested_values = self._frozen_required_parameters()
            if requested_values in self._compiler_output_by_param_values:
                new_compiler_output = self._compiler_output_by_param_values[
                    requested_values
                ]

                self._last_compiler_output = new_compiler_output
                self._combined_compiler_output.total_execution_time += (
                    new_compiler_output.total_execution_time
                )
                yield
                return

        # We don't have a compiler output for this state yet, so we need to compile
        parameter_store = ParameterStore(self._iteration_stack.nt_parameter_values())
        tracker = parameter_store.create_tracker()
        new_compiler_output = self._rt_compiler.run(parameter_store)

        if self._required_parameters is None:
            self._required_parameters = tracker.queries()
        else:
            assert self._required_parameters == tracker.queries()

        requested_values = self._frozen_required_parameters()

        self._compiler_output_by_param_values[requested_values] = new_compiler_output

        # Assemble the combined compiler output
        if self._combined_compiler_output is None:
            self._combined_compiler_output = rt_linker.from_single_run(
                new_compiler_output,
                list(self._iteration_stack.nt_loop_indices()),
            )
        else:
            rt_linker.merge_compiler_runs(
                self._combined_compiler_output,
                new_compiler_output,
                self._last_compiler_output,
                list(self._iteration_stack.nt_loop_indices()),
            )
            self._combined_compiler_output.total_execution_time += (
                new_compiler_output.total_execution_time
            )

        self._last_compiler_output = new_compiler_output

        yield

    def _frozen_required_parameters(self):
        return frozenset(
            (k, v)
            for k, v in self._iteration_stack.nt_parameter_values().items()
            if k in self._required_parameters
        )

    def combined_compiler_output(self):
        return self._combined_compiler_output
