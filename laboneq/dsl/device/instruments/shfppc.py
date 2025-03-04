# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass

from laboneq.core.types.enums.io_direction import IODirection
from laboneq.dsl.device import Port

from ...dsl_dataclass_decorator import classformatter
from ...enums import IOSignalType
from .zi_standard_instrument import ZIStandardInstrument


@classformatter
@dataclass(init=True, repr=True, order=True)
class SHFPPC(ZIStandardInstrument):
    """Class representing a ZI SHFPPC instrument."""

    @property
    def ports(self):
        """Ports that are part of this instrument."""
        outputs = []
        for ch in range(4):
            outputs.append(
                Port(
                    IODirection.OUT,
                    uid=f"PPCHANNELS/{ch}",
                    signal_type=IOSignalType.PPC,
                    physical_port_ids=[f"{ch}"],
                    connector_labels=[f"Signal Output {ch+1}"],
                )
            )
        return outputs
