# Copyright 2022 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterable

from laboneq._observability.tracing import trace

_logger = logging.getLogger(__name__)


@dataclass
class Node:
    path: str
    values: list[Any] = field(default_factory=list)
    last: Any | None = None

    def flush(self):
        self.values.clear()

    def peek(self) -> Any | None:
        return None if len(self.values) == 0 else self.values[0]

    def pop(self) -> Any | None:
        return None if len(self.values) == 0 else self.values.pop(0)

    def get_last(self) -> Any | None:
        return self.last

    def append(self, val: dict[str, Any]):
        self.values.extend(val["value"])
        self.last = self.values[-1]


def _is_expected(val: Any, expected: list[Any | None]) -> bool:
    for e in expected:
        if e is None:
            # No specific value expected, any update matches
            return True
        if isinstance(e, FloatWithTolerance) and math.isclose(
            val, e.val, abs_tol=e.abs_tol
        ):
            # Float with given tolerance
            return True
        if val == e:
            # Otherwise exact match
            return True
    return False


class NodeMonitor:
    def __init__(self, daq):
        self._daq = daq
        self._nodes: dict[str, Node] = {}

    def _log_missing_node(self, path: str):
        _logger.warning(
            "Internal error: Node %s is not registered for monitoring", path
        )

    def _get_node(self, path: str) -> Node:
        node = self._nodes.get(path)
        if node is None:
            self._log_missing_node(path)
            return Node(path)
        return node

    def reset(self):
        self.stop()
        self._nodes.clear()

    def add_nodes(self, paths: list[str]):
        for path in paths:
            if path not in self._nodes:
                self._nodes[path] = Node(path)

    def start(self):
        all_paths = [p for p in self._nodes.keys()]
        if len(all_paths) > 0:
            self._daq.subscribe(all_paths)

    def fetch(self, paths: list[str]):
        for path in paths:
            self._daq.getAsEvent(path)

    def stop(self):
        self._daq.unsubscribe("*")
        self.flush()

    def poll(self):
        while True:
            data = self._daq.poll(1e-6, 100, flat=True)
            if len(data) == 0:
                break
            for path, val in data.items():
                self._get_node(path).append(val)

    def flush(self):
        self._daq.sync()
        for node in self._nodes.values():
            node.flush()

    def peek(self, path: str) -> Any | None:
        return self._get_node(path).peek()

    def pop(self, path: str) -> Any | None:
        return self._get_node(path).pop()

    def get_last(self, path: str) -> Any | None:
        return self._get_node(path).get_last()

    def check_last_for_conditions(self, conditions: dict[str, Any]) -> str:
        for path, expected in conditions.items():
            if path not in self._nodes:
                self._log_missing_node(path)
                return path
            # expected may be None, single value or a list
            all_expected = expected if isinstance(expected, Iterable) else [expected]
            val = self.get_last(path)
            if val is None:
                return path
            if not _is_expected(val, all_expected):
                return path
        return None

    def poll_and_check_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        self.poll()
        remaining = {}
        for path, expected in conditions.items():
            if path not in self._nodes:
                self._log_missing_node(path)
                continue
            # expected may be None, single value or a list
            all_expected = expected if isinstance(expected, Iterable) else [expected]
            while True:
                val = self.pop(path)
                if val is None:
                    # No further updates for the path,
                    # keep condition as is for the next check iteration
                    remaining[path] = expected
                    break
                if _is_expected(val, all_expected):
                    break
        return remaining


class MultiDeviceHandlerBase:
    def __init__(self):
        self._conditions: dict[NodeMonitor, dict[str, Any]] = {}

    def add(self, target: NodeMonitor, conditions: dict[str, Any]):
        daq_conditions: dict[str, Any] = self._conditions.setdefault(target, {})
        daq_conditions.update(conditions)


class ConditionsChecker(MultiDeviceHandlerBase):
    """Non-blocking checker, ensures all conditions for multiple
    devices are fulfilled. Uses the last known node values, no additional
    polling for updates!

    This class must be prepared in same way as the AllRepliesWaiter,
    see AllRepliesWaiter for details.
    """

    def check_all(self) -> tuple[str, Any]:
        for node_monitor, daq_conditions in self._conditions.items():
            failed_path = node_monitor.check_last_for_conditions(daq_conditions)
            if failed_path is not None:
                return failed_path, daq_conditions[failed_path]
        return None, None


class ResponseWaiter(MultiDeviceHandlerBase):
    """Parallel waiting for responses from multiple devices over multiple
    connections.

    Usage:
    ======

    daqA = zhinst.core.ziDAQServer('serverA', ...)
    daqB = zhinst.core.ziDAQServer('serverB', ...)

    # One NodeMonitor per data server connection
    monitorA = NodeMonitor(daqA)
    monitorB = NodeMonitor(daqB)

    dev1_monitor = monitorA # dev1 connected via serverA
    dev2_monitor = monitorA # dev2 connected via serverA
    dev3_monitor = monitorB # dev3 connected via serverB
    #...

    dev1_conditions = {
        "/dev1/path1": 5,
        "/dev1/path2": 0,
    }
    dev2_conditions = {
        "/dev2/path1": 3,
        "/dev2/path2": 0,
        # ...
    }
    dev3_conditions = {
        # ...
    }
    #...

    # Register all required conditions with binding to the respective
    # NodeMonitor.
    response_waiter = ResponseWaiter()
    response_waiter.add(target=dev1_monitor, conditions=dev1_conditions)
    response_waiter.add(target=dev2_monitor, conditions=dev2_conditions)
    response_waiter.add(target=dev3_monitor, conditions=dev3_conditions)
    # ...

    # Wait until all the nodes given in the registered conditions return
    # respective values. The call returns 'True' immediately, once all
    # expected responses are received. Times out after 'timeout' seconds (float),
    # returning 'False' in this case.
    if not response_waiter.wait_all(timeout=0.5):
        raise RuntimeError("Expected responses still not received after 2 seconds")
    """

    def __init__(self):
        super().__init__()
        self._timer = time.time

    @trace("wait-for-all-nodes", disable_tracing_during=True)
    def wait_all(self, timeout: float) -> bool:
        start = self._timer()
        while True:
            remaining: dict[NodeMonitor, dict[str, Any]] = {}
            for node_monitor, daq_conditions in self._conditions.items():
                daq_remaining = node_monitor.poll_and_check_conditions(daq_conditions)
                if len(daq_remaining) > 0:
                    remaining[node_monitor] = daq_remaining
            if len(remaining) == 0:
                return True
            if self._timer() - start > timeout:
                return False
            self._conditions = remaining

    def remaining(self) -> dict[str, Any]:
        all_conditions: dict[str, Any] = {}
        for daq_conditions in self._conditions.values():
            all_conditions.update(daq_conditions)
        return all_conditions

    def remaining_str(self) -> str:
        return "\n".join([f"{p}={v}" for p, v in self.remaining().items()])


class NodeControlKind(Enum):
    Condition = auto()
    Command = auto()
    Response = auto()
    Prepare = auto()


@dataclass
class FloatWithTolerance:
    val: float
    abs_tol: float


@dataclass
class NodeControlBase:
    path: str
    value: Any
    kind: NodeControlKind = None

    @property
    def raw_value(self):
        return (
            self.value.val if isinstance(self.value, FloatWithTolerance) else self.value
        )


@dataclass
class Condition(NodeControlBase):
    """Represents a condition to be fulfilled. Condition node may not
    necessarily receive an update after executing Command(s), if it has
    already the right value, for instance extref freq, but still must be
    verified."""

    def __post_init__(self):
        self.kind = NodeControlKind.Condition


@dataclass
class Command(NodeControlBase):
    """Represents a command node. The node will be set, if conditions
    are not fulfilled. Also treated as a response and a condition."""

    def __post_init__(self):
        self.kind = NodeControlKind.Command


@dataclass
class Response(NodeControlBase):
    """Represents a response, expected in return to
    the executed Command(s). Also treated as a condition."""

    def __post_init__(self):
        self.kind = NodeControlKind.Response


@dataclass
class Prepare(NodeControlBase):
    """Represents a command node, that has to be set only as
    a preparation before the main Command(s), but shouldn't be touched
    or be in a specific state otherwise."""

    def __post_init__(self):
        self.kind = NodeControlKind.Prepare


def _filter_nodes(
    nodes: list[NodeControlBase], filter: list[NodeControlKind]
) -> list[NodeControlBase]:
    return [n for n in nodes if n.kind in filter]


def filter_commands(nodes: list[NodeControlBase]) -> list[NodeControlBase]:
    return _filter_nodes(nodes, [NodeControlKind.Prepare, NodeControlKind.Command])


def filter_responses(nodes: list[NodeControlBase]) -> list[NodeControlBase]:
    return _filter_nodes(nodes, [NodeControlKind.Command, NodeControlKind.Response])


def filter_conditions(nodes: list[NodeControlBase]) -> list[NodeControlBase]:
    return _filter_nodes(
        nodes,
        [NodeControlKind.Condition, NodeControlKind.Command, NodeControlKind.Response],
    )
