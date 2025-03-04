# Copyright 2023 Zurich Instruments AG
# SPDX-License-Identifier: Apache-2.0


from typing import Any as AnyDSL

from laboneq.core.types.enums.io_direction import IODirection as IODirectionDSL
from laboneq.core.types.enums.io_signal_type import IOSignalType as IOSignalTypeDSL
from laboneq.core.types.enums.port_mode import PortMode as PortModeDSL
from laboneq.core.types.enums.reference_clock_source import (
    ReferenceClockSource as ReferenceClockSourceDSL,
)
from laboneq.data.setup_description import Any as AnyDATA
from laboneq.data.setup_description import Connection as ConnectionDATA
from laboneq.data.setup_description import Instrument as HDAWGDATA
from laboneq.data.setup_description import Instrument as InstrumentDATA
from laboneq.data.setup_description import Instrument as PQSCDATA
from laboneq.data.setup_description import Instrument as SHFQADATA
from laboneq.data.setup_description import Instrument as SHFSGDATA
from laboneq.data.setup_description import Instrument as UHFQADATA
from laboneq.data.setup_description import IODirection as IODirectionDATA
from laboneq.data.setup_description import IOSignalType as IOSignalTypeDATA
from laboneq.data.setup_description import LogicalSignal as LogicalSignalDATA
from laboneq.data.setup_description import LogicalSignalGroup as LogicalSignalGroupDATA
from laboneq.data.setup_description import PhysicalChannel as PhysicalChannelDATA
from laboneq.data.setup_description import (
    PhysicalChannelType as PhysicalChannelTypeDATA,
)
from laboneq.data.setup_description import Port as PortDATA
from laboneq.data.setup_description import PortMode as PortModeDATA
from laboneq.data.setup_description import QuantumElement as QuantumElementDATA
from laboneq.data.setup_description import Qubit as QubitDATA
from laboneq.data.setup_description import (
    ReferenceClockSource as ReferenceClockSourceDATA,
)
from laboneq.data.setup_description import Server as DataServerDATA
from laboneq.data.setup_description import Server as ServerDATA
from laboneq.data.setup_description import Setup as DeviceSetupDATA
from laboneq.dsl.device.connection import Connection as ConnectionDSL
from laboneq.dsl.device.device_setup import DeviceSetup as DeviceSetupDSL
from laboneq.dsl.device.instrument import Instrument as InstrumentDSL
from laboneq.dsl.device.instruments.hdawg import HDAWG as HDAWGDSL
from laboneq.dsl.device.instruments.pqsc import PQSC as PQSCDSL
from laboneq.dsl.device.instruments.shfqa import SHFQA as SHFQADSL
from laboneq.dsl.device.instruments.shfsg import SHFSG as SHFSGDSL
from laboneq.dsl.device.instruments.uhfqa import UHFQA as UHFQADSL
from laboneq.dsl.device.io_units.logical_signal import LogicalSignal as LogicalSignalDSL
from laboneq.dsl.device.io_units.physical_channel import (
    PhysicalChannel as PhysicalChannelDSL,
)
from laboneq.dsl.device.io_units.physical_channel import (
    PhysicalChannelType as PhysicalChannelTypeDSL,
)
from laboneq.dsl.device.logical_signal_group import (
    LogicalSignalGroup as LogicalSignalGroupDSL,
)
from laboneq.dsl.device.ports import Port as PortDSL
from laboneq.dsl.device.server import Server as ServerDSL
from laboneq.dsl.device.servers.data_server import DataServer as DataServerDSL
from laboneq.dsl.quantum.qubits import QuantumElement as QuantumElementDSL
from laboneq.dsl.quantum.qubits import Qubit as QubitDSL
from laboneq.implementation.legacy_adapters.dynamic_converter import convert_dynamic

# converter functions for data type package 'setup_description'
#  AUTOGENERATED, DO NOT EDIT
from .post_process_setup_description import post_process


def get_converter_function_setup_description(orig):
    converter_function_directory = {
        ConnectionDSL: convert_Connection,
        DataServerDSL: convert_DataServer,
        DeviceSetupDSL: convert_DeviceSetup,
        HDAWGDSL: convert_HDAWG,
        InstrumentDSL: convert_Instrument,
        LogicalSignalDSL: convert_LogicalSignal,
        LogicalSignalGroupDSL: convert_LogicalSignalGroup,
        PQSCDSL: convert_PQSC,
        PhysicalChannelDSL: convert_PhysicalChannel,
        PortDSL: convert_Port,
        QuantumElementDSL: convert_QuantumElement,
        QubitDSL: convert_Qubit,
        SHFQADSL: convert_SHFQA,
        SHFSGDSL: convert_SHFSG,
        ServerDSL: convert_Server,
        UHFQADSL: convert_UHFQA,
    }
    return converter_function_directory.get(orig)


def convert_IODirection(orig: IODirectionDSL):
    return (
        next(e for e in IODirectionDATA if e.name == orig.name)
        if orig is not None
        else None
    )


def convert_IOSignalType(orig: IOSignalTypeDSL):
    return (
        next(e for e in IOSignalTypeDATA if e.name == orig.name)
        if orig is not None
        else None
    )


def convert_PhysicalChannelType(orig: PhysicalChannelTypeDSL):
    return (
        next(e for e in PhysicalChannelTypeDATA if e.name == orig.name)
        if orig is not None
        else None
    )


def convert_PortMode(orig: PortModeDSL):
    return (
        next(e for e in PortModeDATA if e.name == orig.name)
        if orig is not None
        else None
    )


def convert_ReferenceClockSource(orig: ReferenceClockSourceDSL):
    return (
        next(e for e in ReferenceClockSourceDATA if e.name == orig.name)
        if orig is not None
        else None
    )


def convert_Connection(orig: ConnectionDSL):
    if orig is None:
        return None
    retval = ConnectionDATA()
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_DataServer(orig: DataServerDSL):
    if orig is None:
        return None
    retval = DataServerDATA()
    retval.api_level = orig.api_level
    retval.host = orig.host
    retval.leader_uid = orig.leader_uid
    retval.port = convert_dynamic(
        orig.port,
        source_type_hint=AnyDSL,
        target_type_hint=AnyDATA,
        orig_is_collection=False,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_DeviceSetup(orig: DeviceSetupDSL):
    if orig is None:
        return None
    retval = DeviceSetupDATA()
    retval.instruments = convert_dynamic(
        orig.instruments,
        source_type_hint=InstrumentDSL,
        target_type_hint=InstrumentDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.logical_signal_groups = convert_dynamic(
        orig.logical_signal_groups,
        source_type_hint=LogicalSignalGroupDSL,
        target_type_hint=LogicalSignalGroupDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.servers = convert_dynamic(
        orig.servers,
        source_type_hint=DataServerDSL,
        target_type_hint=ServerDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.uid = orig.uid
    retval.calibration = orig.get_calibration()
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_HDAWG(orig: HDAWGDSL):
    if orig is None:
        return None
    retval = HDAWGDATA()
    retval.address = orig.address
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_Instrument(orig: InstrumentDSL):
    if orig is None:
        return None
    retval = InstrumentDATA()
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_LogicalSignal(orig: LogicalSignalDSL):
    if orig is None:
        return None
    retval = LogicalSignalDATA()
    retval.direction = convert_IODirection(orig.direction)
    retval.name = orig.name
    retval.path = orig.path
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_LogicalSignalGroup(orig: LogicalSignalGroupDSL):
    if orig is None:
        return None
    retval = LogicalSignalGroupDATA()
    retval.logical_signals = convert_dynamic(
        orig.logical_signals,
        source_type_string="Dict",
        target_type_string="Dict[str,LogicalSignal]",
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_PQSC(orig: PQSCDSL):
    if orig is None:
        return None
    retval = PQSCDATA()
    retval.address = orig.address
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_PhysicalChannel(orig: PhysicalChannelDSL):
    if orig is None:
        return None
    retval = PhysicalChannelDATA()
    retval.type = convert_PhysicalChannelType(orig.type)
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_Port(orig: PortDSL):
    if orig is None:
        return None
    retval = PortDATA()
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_QuantumElement(orig: QuantumElementDSL):
    if orig is None:
        return None
    retval = QuantumElementDATA()
    retval.parameters = orig.parameters
    retval.signals = orig.signals
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_Qubit(orig: QubitDSL):
    if orig is None:
        return None
    retval = QubitDATA()
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_SHFQA(orig: SHFQADSL):
    if orig is None:
        return None
    retval = SHFQADATA()
    retval.address = orig.address
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_SHFSG(orig: SHFSGDSL):
    if orig is None:
        return None
    retval = SHFSGDATA()
    retval.address = orig.address
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_Server(orig: ServerDSL):
    if orig is None:
        return None
    retval = ServerDATA()
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )


def convert_UHFQA(orig: UHFQADSL):
    if orig is None:
        return None
    retval = UHFQADATA()
    retval.address = orig.address
    retval.connections = convert_dynamic(
        orig.connections,
        source_type_hint=ConnectionDSL,
        target_type_hint=ConnectionDATA,
        orig_is_collection=True,
        conversion_function_lookup=get_converter_function_setup_description,
    )
    retval.interface = orig.interface
    retval.uid = orig.uid
    return post_process(
        orig,
        retval,
        conversion_function_lookup=get_converter_function_setup_description,
    )
