import enum

# from homeassistant.exceptions import HomeAssistantError
from pymodbus.client.sync import ModbusTcpClient as ModbusClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder, BinaryPayloadBuilder

# from .const import (
#     JSON_NAME, JSON_MANUFACTURER, JSON_MODEL, JSON_SERIAL_NUMBER,
#     JSON_POWER_CONSUMED_WATTS, JSON_FIRMWARE_VERSION
# )

POWERTAG_LINK_SLAVE_ID = 255

decoder = lambda registers: BinaryPayloadDecoder.fromRegisters(
    registers, byteorder=Endian.Big, wordorder=Endian.Big
)


# def handle_error(result):
#     if result.status_code == 401:
#         raise InvalidAuth()
#
#     if result.status_code == 404:
#         error = result.json()['error']
#         if error['code'] == 'Base.1.0.GeneralError' and 'RedFish attribute is disabled' in \
#                 error['@Message.ExtendedInfo'][0]['Message']:
#             raise RedfishConfig()
#
#     if result.status_code != 200:
#         raise CannotConnect(result.text)

class Phase(enum.Enum):
    a = 0
    b = 2
    c = 4


class LineVoltage(enum.Enum):
    a_b = 0
    b_c = 2
    c_a = 4
    a_n = 8
    b_n = 10
    c_n = 12


class LinkDeviceStatus:
    def __init__(self, bitmask):
        lower_mask = bitmask & 0b1111
        if lower_mask == 0b0001:
            self.status = "Start-up"
        elif lower_mask == 0b0010:
            self.status = "Operating"
        elif lower_mask == 0b0100:
            self.status = "Downgraded"
        elif lower_mask == 0b1000:
            self.status = "Failure"

        self.e2prom_error = (bitmask & 0b0010_0000_00000000) != 0
        self.ram_error = (bitmask & 0b0100_0000_00000000) != 0
        self.flash_error = (bitmask & 0b1000_0000_00000000) != 0


class AlarmStatus:
    def __init__(self, bitmask):
        lower_mask = bitmask & 0b1111_1111

        self.has_alarm = lower_mask != 0
        self.alarm_voltage_loss = (lower_mask & 0b0000_0001) != 0
        self.alarm_current_overload = (lower_mask & 0b0000_0010) != 0
        # self.alarm_reserved = (lower_mask & 0b0000_0100) != 0
        self.alarm_overload_45_percent = (lower_mask & 0b0000_1000) != 0
        self.alarm_load_current_loss = (lower_mask & 0b0001_0000) != 0
        self.alarm_overvoltage = (lower_mask & 0b0010_0000) != 0
        self.alarm_undervoltage = (lower_mask & 0b0100_0000) != 0
        self.alarm_battery_low = (lower_mask & 0b1000_0000) != 0


class DeviceUsage(enum.Enum):
    main_incomer = 1
    sub_head_of_group = 2
    heating = 3
    cooling = 4
    hvac = 5
    ventilation = 6
    lighting = 7
    office_equipment = 8
    cooking = 9
    food_refrigeration = 10
    elevators = 11
    computers = 12
    renewable_energy_production = 13
    genset = 14
    compressed_air = 15
    vapor = 16
    machine = 17
    process = 18
    water = 19
    other_sockets = 20
    other = 21


class PhaseSequence(enum.Enum):
    a = 1
    b = 2
    c = 3
    abc = 4
    acb = 5
    bca = 6
    bac = 7
    cab = 8
    cba = 9


class Position(enum.Enum):
    not_configured = 0
    top = 1
    bottom = 2


def product_type(product_code: int):
    if product_code == 41:
        return "PowerTag Acti9 M631P (A9MEM1520)"
    elif product_code == 42:
        return "PowerTag Acti9 M631P+N Top (A9MEM1521)"
    elif product_code == 43:
        return "PowerTag Acti9 M631P+N Bottom (A9MEM1522)"
    elif product_code == 44:
        return "PowerTag Acti9 M633P (A9MEM1540)"
    elif product_code == 45:
        return "PowerTag Acti9 M633P+N Top (A9MEM1541)"
    elif product_code == 46:
        return "PowerTag Acti9 M633P+N Bottom(A9MEM1542)"
    elif product_code == 81:
        return "PowerTag Acti9 F631P+N (A9MEM1560)"
    elif product_code == 82:
        return "PowerTag Acti9 P631P+N Top (A9MEM1561)"
    elif product_code == 83:
        return "PowerTag Acti9 P631P+N Bottom(A9MEM1562)"
    elif product_code == 84:
        return "PowerTag Acti9 P631P+N Bottom(A9MEM1563)"
    elif product_code == 85:
        return "PowerTag Acti9 F633P+N (A9MEM1570)"
    elif product_code == 86:
        return "PowerTag Acti9 P633P+N Top (A9MEM1571)"
    elif product_code == 87:
        return "PowerTag Acti9 P633P+N Bottom(A9MEM1572)"
    elif product_code == 88:
        return "3P-250A (LVSMC13)"
    elif product_code == 89:
        return "3P-630A (LVSMC23)"
    elif product_code == 92:
        return "PowerTag NSX 3P-250 A (LV434020)"
    elif product_code == 93:
        return "PowerTag NSX 4P-250 A (LV434021)"
    elif product_code == 94:
        return "PowerTag NSX 3P-630 A (LV434022)"
    elif product_code == 95:
        return "PowerTag NSX 4P-630 A (LV434023)"
    elif product_code == 96:
        return "PowerTag Acti9 M633P 230V (A9MEM1543)"
    elif product_code == 97:
        return "PowerTag Acti9 C 2DI 230V (A9XMC2D3)"
    elif product_code == 98:
        return "PowerTag Acti9 C IO 230V (A9XMC1D3)"
    elif product_code == 101:
        return "PowerTag Acti9 F63 1P+N 110V (A9MEM1564)"
    elif product_code == 102:
        return "PowerTag Acti9 F63 3P (A9MEM1573)"
    elif product_code == 103:
        return "PowerTag Acti9 F63 3P+N 110/230V (A9MEM1574)"
    else:
        return "UNKNOWN ID %d" % product_code


class SchneiderModbus:
    def __init__(self, host, port=502, timeout=5):
        self.client = ModbusClient(host, port, timeout=timeout)
        self.client.connect()

    def get_identification(self):
        return self.__read_string(0x64, 6, POWERTAG_LINK_SLAVE_ID, 11)

    def get_hardware_version(self):
        return self.__read_string(0x6A, 3, POWERTAG_LINK_SLAVE_ID, 6)

    def get_software_version(self):
        return self.__read_string(0x6D, 3, POWERTAG_LINK_SLAVE_ID, 6)

    def get_status(self):
        register = self.__read(0x70, 1, POWERTAG_LINK_SLAVE_ID)
        return LinkDeviceStatus(register[0])

    def get_current_phase(self, power_tag_index: int, phase: Phase):
        return self.__read_float_32(0xBB7 + phase.value, power_tag_index)

    def get_voltage(self, power_tag_index: int, line_voltage: LineVoltage):
        return self.__read_float_32(0xBCB + line_voltage.value, power_tag_index)

    def get_active_power(self, power_tag_index: int, phase: Phase):
        return self.__read_float_32(0xBED + phase.value, power_tag_index)

    def get_total_active_power(self, power_tag_index: int):
        return self.__read_float_32(0xBF3, power_tag_index)

    def get_total_apparent_power(self, power_tag_index: int):
        return self.__read_float_32(0xC03, power_tag_index)

    def get_power_factor(self, power_tag_index: int):
        return self.__read_float_32(0xC0B, power_tag_index)

    def get_total_active_energy_delivered_and_received(self, power_tag_index: int):
        return self.__read_int_64(0xC83, power_tag_index)

    def is_alarm_valid(self, power_tag_index: int):
        return (self.__read_int_16(0xCE1, power_tag_index) & 0b1) != 0

    def get_alarm(self, power_tag_index: int):
        return AlarmStatus(self.__read_int_16(0xCE3, power_tag_index))

    def get_rms_current(self, power_tag_index: int, phase: Phase):
        return self.__read_float_32(0xCE5 + phase.value, power_tag_index)

    def get_load_operating_time(self, power_tag_index: int):
        return self.__read_int_32(0xCEB, power_tag_index)

    def get_active_power_threshold(self, power_tag_index: int):
        return self.__read_float_32(0xCED, power_tag_index)

    def get_name(self, power_tag_index: int):
        return self.__read_string(0x7918, 10, power_tag_index, 20)

    def get_circuit(self, power_tag_index: int):
        return self.__read_string(0x7922, 3, power_tag_index, 5)

    def get_usage(self, power_tag_index: int):
        return DeviceUsage(self.__read_int_16(0x7925, power_tag_index))

    def get_phase_sequence(self, power_tag_index: int):
        return PhaseSequence(self.__read_int_16(0x7927, power_tag_index))

    def get_position(self, power_tag_index: int):
        return Position(self.__read_int_16(0x7927, power_tag_index))

    def get_circuit_diagnostic(self, power_tag_index: int):
        return Position(self.__read_int_16(0x7928, power_tag_index))

    def get_rated_current(self, power_tag_index: int):
        return self.__read_int_16(0x7929, power_tag_index)

    def get_product_type(self, power_tag_index: int):
        return product_type(self.__read_int_16(0x7930, power_tag_index))

    def get_slave_address(self, power_tag_index: int):
        return self.__read_int_16(0x7931, power_tag_index)

    def get_rf_id(self, power_tag_index: int):
        return self.__read_int_64(0x7932, power_tag_index)

    def get_vendor_name(self, power_tag_index: int):
        return self.__read_string(0x7944, 16, power_tag_index, 32)

    def get_product_code(self, power_tag_index: int):
        return self.__read_string(0x7954, 16, power_tag_index, 32)

    def get_firmware_version(self, power_tag_index: int):
        return self.__read_string(0x7964, 6, power_tag_index, 12)

    def get_hardware_version2(self, power_tag_index: int):
        return self.__read_string(0x796A, 6, power_tag_index, 12)

    def get_serial_number(self, power_tag_index: int):
        return self.__read_string(0x7970, 10, power_tag_index, 20)

    def get_product_range(self, power_tag_index: int):
        return self.__read_string(0x797A, 8, power_tag_index, 16)

    def get_product_model(self, power_tag_index: int):
        return self.__read_string(0x7982, 8, power_tag_index, 16)

    def get_product_family(self, power_tag_index: int):
        return self.__read_string(0x798A, 8, power_tag_index, 16)

    def is_rf_communication_valid(self, power_tag_index: int):
        return self.__read_int_16(0x79A8, power_tag_index) != 0

    def is_wireless_communication_valid(self, power_tag_index: int):
        return self.__read_int_16(0x79A9, power_tag_index) != 0

    def get_packet_error_rate(self, power_tag_index: int):
        return self.__read_float_32(0x79B4, power_tag_index)

    def get_radio_signal_strength_indicator(self, power_tag_index: int):
        return self.__read_float_32(0x79B6, power_tag_index)

    def get_link_quality(self, power_tag_index: int):
        return self.__read_int_16(0x79B8, power_tag_index)

    def __read(self, address, count, unit):
        return self.client.read_holding_registers(address, count, unit=unit).registers

    def __read_string(self, address, count, unit, string_length):
        registers = self.__read(address, count, unit)
        ascii_bytes = decoder(registers).decode_string(string_length)
        filtered_ascii_bytes = bytes(filter(lambda b: b != 0, list(ascii_bytes)))
        return bytes.decode(filtered_ascii_bytes)

    def __read_float_32(self, address, unit):
        registers = self.__read(address, 2, unit)
        return decoder(registers).decode_32bit_float()

    def __read_int_16(self, address, unit):
        registers = self.__read(address, 1, unit)
        return decoder(registers).decode_16bit_int()

    def __read_int_32(self, address, unit):
        registers = self.__read(address, 2, unit)
        return decoder(registers).decode_32bit_int()

    def __read_int_64(self, address, unit):
        registers = self.__read(address, 4, unit)
        return decoder(registers).decode_64bit_int()

    # def get_power_usage(self):
    #     result = self.get_path(drac_powercontrol_path)
    #     handle_error(result)
    #
    #     power_results = result.json()
    #     return power_results[JSON_POWER_CONSUMED_WATTS]
    #
    # def get_device_info(self):
    #     result = self.get_path(drac_chassis_path)
    #     handle_error(result)
    #
    #     chassis_results = result.json()
    #     return {
    #         JSON_NAME: chassis_results[JSON_NAME],
    #         JSON_MANUFACTURER: chassis_results[JSON_MANUFACTURER],
    #         JSON_MODEL: chassis_results[JSON_MODEL],
    #         JSON_SERIAL_NUMBER: chassis_results[JSON_SERIAL_NUMBER]
    #     }
    #
    # def get_firmware_version(self):
    #     result = self.get_path(drac_managers_path)
    #     handle_error(result)
    #
    #     manager_results = result.json()
    #     return manager_results[JSON_FIRMWARE_VERSION]
    #
    # def get_path(self, path):
    #     return requests.get(protocol + self.host + path, auth=self.auth, verify=False)


# class CannotConnect(HomeAssistantError):
#     """Error to indicate we cannot connect."""
#
#
# class InvalidAuth(HomeAssistantError):
#     """Error to indicate there is invalid auth."""
#
#
# class RedfishConfig(HomeAssistantError):
#     """Error to indicate that Redfish was not properly configured"""


client = SchneiderModbus("192.168.1.39", 502, 5)

print(client.get_identification())
print(client.get_hardware_version())
print(client.get_software_version())
print(client.get_status().status)

print(client.get_current_phase(1, Phase.a))
print(client.get_current_phase(1, Phase.b))
print(client.get_current_phase(1, Phase.c))

print(client.get_voltage(1, LineVoltage.a_b))
print(client.get_voltage(1, LineVoltage.b_c))
print(client.get_voltage(1, LineVoltage.c_a))
print(client.get_voltage(1, LineVoltage.a_n))
print(client.get_voltage(1, LineVoltage.b_n))
print(client.get_voltage(1, LineVoltage.c_n))

print(client.get_active_power(1, Phase.a))
print(client.get_active_power(1, Phase.b))
print(client.get_active_power(1, Phase.c))
print(client.get_total_active_power(1))
print(client.get_total_apparent_power(1))
print(client.get_power_factor(1))

print(client.get_total_active_energy_delivered_and_received(1))
print(client.is_alarm_valid(1))
print(client.get_alarm(1).has_alarm)

print(client.get_rms_current(1, Phase.a))
print(client.get_rms_current(1, Phase.b))
print(client.get_rms_current(1, Phase.c))

print(client.get_load_operating_time(1))
print(client.get_active_power_threshold(1))

print(client.get_name(1))
print(client.get_circuit(1))
print(client.get_usage(1))
print(client.get_phase_sequence(1))
print(client.get_position(1))
print(client.get_circuit_diagnostic(1))
print(client.get_rated_current(1))
print(client.get_product_type(1))
print(client.get_slave_address(1))
print(client.get_rf_id(1))

print(client.get_vendor_name(1))
print(client.get_product_code(1))
print(client.get_firmware_version(1))
print(client.get_hardware_version2(1))
print(client.get_serial_number(1))
print(client.get_product_range(1))
print(client.get_product_model(1))
print(client.get_product_family(1))

print(client.is_rf_communication_valid(1))
print(client.is_wireless_communication_valid(1))
print(client.get_packet_error_rate(1))
print(client.get_radio_signal_strength_indicator(1))
print(client.get_link_quality(1))