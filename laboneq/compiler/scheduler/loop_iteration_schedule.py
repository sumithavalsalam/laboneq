# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Dict, Iterator, List

from attrs import asdict, define, evolve

from laboneq.compiler.common.compiler_settings import CompilerSettings
from laboneq.compiler.common.event_type import EventType
from laboneq.compiler.scheduler.section_schedule import SectionSchedule


@define(kw_only=True, slots=True)
class LoopIterationSchedule(SectionSchedule):
    """Schedule of a single iteration of a loop (sweep or average)"""

    iteration: int
    sweep_parameters: List[Dict]
    num_repeats: int
    shadow: bool

    def __attrs_post_init__(self):
        # We always "steal" the data from a SectionSchedule which has already done
        # all the hard work in its own __attrs_post_init__().
        pass

    def generate_event_list(
        self,
        start: int,
        max_events: int,
        id_tracker: Iterator[int],
        expand_loops,
        settings: CompilerSettings,
    ) -> List[Dict]:
        assert self.length is not None
        assert self.absolute_start is not None
        common = {
            "section_name": self.section,
            "iteration": self.iteration,
            "num_repeats": self.num_repeats,
            "nesting_level": 0,
        }
        end = start + self.length

        max_events -= len(self.sweep_parameters)
        if self.iteration == 0:
            max_events -= 1

        # we'll add one LOOP_STEP_START, LOOP_STEP_END, LOOP_ITERATION_END each
        max_events -= 3

        children_events = self.children_events(
            start, max_events, settings, id_tracker, expand_loops
        )

        event_list = [
            dict(event_type=EventType.LOOP_STEP_START, time=start, **common),
            *[
                dict(
                    event_type=EventType.PARAMETER_SET,
                    time=start,
                    section_name=self.section,
                    parameter={"id": param["id"]},
                    iteration=self.iteration,
                    value=param["values"][self.iteration],
                )
                for param in self.sweep_parameters
            ],
            *[e for l in children_events for e in l],
            dict(event_type=EventType.LOOP_STEP_END, time=end, **common),
            *(
                [dict(event_type=EventType.LOOP_ITERATION_END, time=end, **common)]
                if self.iteration == 0
                else []
            ),
        ]

        if self.shadow:
            for e in event_list:
                e["shadow"] = True
        return event_list

    def compressed_iteration(self, iteration: int):
        """Make a copy of this schedule, but replace ``iteration`` and set the
        ``shadow`` flag."""
        return evolve(self, iteration=iteration, shadow=True)

    @classmethod
    def from_section_schedule(
        cls, schedule: SectionSchedule, iteration, num_repeats, shadow, sweep_parameters
    ):
        """Down-cast from SectionSchedule."""
        return cls(
            **asdict(schedule, recurse=False),
            iteration=iteration,
            num_repeats=num_repeats,
            shadow=shadow,
            sweep_parameters=sweep_parameters,
        )

    def __hash__(self):
        return super().__hash__()
