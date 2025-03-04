# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import List

from laboneq.core.types.enums import IODirection, IOSignalType
from laboneq.dsl.dsl_dataclass_decorator import classformatter


@classformatter
@dataclass(init=True, repr=True, order=True)
class Port:
    """Abstraction of a port"""

    direction: IODirection
    uid: str = field(default=None)
    connector_labels: List[str] = field(default_factory=list)
    physical_port_ids: List[str] = field(default_factory=list)
    signal_type: IOSignalType = field(default=None)
