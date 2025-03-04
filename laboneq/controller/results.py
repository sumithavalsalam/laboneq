# Copyright 2019 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import ArrayLike

from laboneq.controller.recipe_enums import NtStepKey

if TYPE_CHECKING:
    from laboneq.dsl.result.acquired_result import AcquiredResult
    from laboneq.dsl.result.results import Results


def make_empty_results() -> Results:
    from laboneq.dsl.result.results import Results

    return Results(acquired_results={}, user_func_results={}, execution_errors=[])


def make_acquired_result(
    data: ArrayLike,
    axis_name: list[str | list[str]],
    axis: list[ArrayLike | list[ArrayLike]],
) -> AcquiredResult:
    from laboneq.dsl.result.acquired_result import AcquiredResult

    return AcquiredResult(data, axis_name, axis)


def build_partial_result(
    result: AcquiredResult,
    nt_step: NtStepKey,
    raw_result: Any,
    mapping: list[str],
    handle: str,
):
    result.last_nt_step = list(nt_step.indices)
    if len(np.shape(result.data)) == len(nt_step.indices):
        # No loops in RT, just a single value produced
        for raw_result_idx in range(len(raw_result)):
            if mapping[raw_result_idx % len(mapping)] == handle:
                if len(nt_step.indices) == 0:
                    result.data = raw_result[raw_result_idx]
                else:
                    result.data[nt_step.indices] = raw_result[raw_result_idx]
                break
    else:
        inner_res = result.data
        for index in nt_step.indices:
            inner_res = inner_res[index]
        res_flat = np.ravel(inner_res)
        res_flat_idx = 0
        for raw_result_idx in range(len(raw_result)):
            if mapping[raw_result_idx % len(mapping)] == handle:
                res_flat[res_flat_idx] = raw_result[raw_result_idx]
                res_flat_idx += 1
