# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Any, Dict

from laboneq.dsl.calibration.calibration_item import CalibrationItem
from laboneq.dsl.dsl_dataclass_decorator import classformatter


def _sanitize_key(key: Any) -> str:
    try:
        return key.path
    except AttributeError as error:
        if not isinstance(key, str):
            raise TypeError("Key must be a string.") from error
        return key


@classformatter
@dataclass(init=True, repr=True, order=True)
class Calibration:
    """Calibration object containing a dict of :class:`~.CalibrationItem`.

    The dictionary has the path i.e. UID to the :py:class:`~.Calibratable` object as
    key and the actual :py:class:`~.CalibrationItem` object as value.
    """

    calibration_items: Dict[str, CalibrationItem] = field(default_factory=dict)

    def __post_init__(self):
        self.calibration_items = {
            _sanitize_key(k): v for k, v in self.calibration_items.items()
        }

    def __getitem__(self, key):
        return self.calibration_items[_sanitize_key(key)]

    def __setitem__(self, key, value):
        self.calibration_items[_sanitize_key(key)] = value

    def __delitem__(self, key):
        del self.calibration_items[_sanitize_key(key)]

    def __iter__(self):
        return iter(self.calibration_items)

    def __len__(self):
        return len(self.calibration_items)

    def items(self):
        return self.calibration_items.items()

    def keys(self):
        return self.calibration_items.keys()

    def values(self):
        return self.calibration_items.values()

    @staticmethod
    def load(filename):
        """Load calibration data from file.

        The file is in JSON format, as generated via :meth:`save()`.

        Args:
            filename: The filename to load data from.
        """
        # TODO ErC: Error handling
        from ..serialization import Serializer

        return Serializer.from_json_file(filename, Calibration)

    def save(self, filename):

        """Save calibration data to file.

        The file is written in JSON format.

        Args:
            filename: The filename to save data to.
        """
        from ..serialization import Serializer

        # TODO ErC: Error handling
        Serializer.to_json_file(self, filename)
