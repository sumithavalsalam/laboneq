# Copyright 2023 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from abc import ABC


class LabOneQSettings(ABC):
    """
    LabOneQSettings is an interface for accessing the settings of the LabOneQ application.
    Note: There are no methods here, this is just a marker interface. A concrete implementation
    will have a property for each setting.
    """

    pass
