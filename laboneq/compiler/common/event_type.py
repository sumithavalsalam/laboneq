# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0


class EventType:
    SECTION_START = "SECTION_START"
    SECTION_END = "SECTION_END"
    PLAY_START = "PLAY_START"
    PLAY_END = "PLAY_END"
    ACQUIRE_START = "ACQUIRE_START"
    ACQUIRE_END = "ACQUIRE_END"
    LOOP_STEP_START = "LOOP_STEP_START"
    LOOP_STEP_BODY_START = "LOOP_STEP_BODY_START"
    LOOP_STEP_END = "LOOP_STEP_END"
    LOOP_ITERATION_END = "LOOP_ITERATION_END"
    LOOP_END = "LOOP_END"
    PARAMETER_SET = "PARAMETER_SET"
    DELAY_START = "DELAY_START"
    DELAY_END = "DELAY_END"
    RESET_PRECOMPENSATION_FILTERS = "RESET_PRECOMPENSATION_FILTERS"
    INITIAL_RESET_HW_OSCILLATOR_PHASE = "INITIAL_RESET_HW_OSCILLATOR_PHASE"
    RESET_HW_OSCILLATOR_PHASE = "RESET_HW_OSCILLATOR_PHASE"
    RESET_SW_OSCILLATOR_PHASE = "RESET_SW_OSCILLATOR_PHASE"
    SKELETON = "SKELETON"
    RIGHT_ALIGNED_COLLECTOR = "RIGHT_ALIGNED_COLLECTOR"
    INCREMENT_OSCILLATOR_PHASE = "INCREMENT_OSCILLATOR_PHASE"
    SET_OSCILLATOR_PHASE = "SET_OSCILLATOR_PHASE"
    SET_OSCILLATOR_FREQUENCY_START = "SET_OSCILLATOR_FREQUENCY_START"
    SET_OSCILLATOR_FREQUENCY_END = "SET_OSCILLATOR_FREQUENCY_END"
    SPECTROSCOPY_END = "SPECTROSCOPY_END"
    SUBSECTION_START = "SUBSECTION_START"
    SUBSECTION_END = "SUBSECTION_END"
    SECTION_SKELETON = "SECTION_SKELETON"
    RELATIVE_TIMING = "RELATIVE_TIMING"
