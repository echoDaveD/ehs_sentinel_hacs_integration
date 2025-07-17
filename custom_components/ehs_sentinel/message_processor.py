import logging
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

class MessageProcessor:
    """Processes NASA packages and creates sensors in Home Assistant."""
    
    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator
        self.entities = {}
        self.value_store = {}

    async def process_message(self, packet):
        for msg in packet.packet_messages:
            hexmsg = f"0x{msg.packet_message:04x}"
            msgname = self.search_nasa_table(hexmsg)
            if msgname is not None:
                try:
                    msgvalue = self.determine_value(msg.packet_payload, msgname, msg.packet_message_type)
                except Exception:
                    continue
                await self.protocol_message(msg, msgname, msgvalue)

    async def protocol_message(self, msg, msgname, msgvalue):
        
        entity_platform = ''

        if self.coordinator.nasa_repo[msgname]['hass_opts']['writable'] and self.coordinator.writemode:
            entity_platform =self.coordinator.nasa_repo[msgname]['hass_opts']['platform']['type']
        else:
            entity_platform = self.coordinator.nasa_repo[msgname]['hass_opts']['default_platform']

        if isinstance(msgvalue, (int, float)) and not isinstance(msgvalue, bool):
            value = round(msgvalue, 2) if isinstance(msgvalue, float) and "." in f"{msgvalue}" else msgvalue
        else:
            value = msgvalue 
            
        await self.coordinator.update_data_safe({entity_platform: {self._normalize_name(msgname): {"value": value, "nasa_name": msgname}}})
        self.coordinator.confirm_write(msgname) 
        self.value_store[msgname] = msgvalue

        if msgname in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC']:
            if all(k in self.value_store for k in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC']):
                value = round(
                    abs(
                        (self.value_store['NASA_OUTDOOR_TW2_TEMP'] - self.value_store['NASA_OUTDOOR_TW1_TEMP']) *
                        (self.value_store['VAR_IN_FLOW_SENSOR_CALC']/60)
                        * 4190
                    ), 4
                )
                if 0 <= value < 15000:
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_HEAT_OUTPUT", value)

        if msgname in ('NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT'):
            if all(k in self.value_store for k in ['NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']):
                if self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT'] > 0:
                    value = round(
                        (self.value_store['NASA_EHSSENTINEL_HEAT_OUTPUT'] /
                         self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']/1000.), 3)
                    if 0 < value < 20:
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_COP", value)

        if msgname in ('NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER'):
            if all(k in self.value_store for k in ['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER']):
                if self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'] > 0:
                    value = round(
                        self.value_store['LVAR_IN_TOTAL_GENERATED_POWER'] /
                        self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'], 3)
                    if 0 < value < 20:
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_TOTAL_COP", value)

    def search_nasa_table(self, address):
        for key, value in self.coordinator.nasa_repo.items():
            if value['address'].lower() == address:
                return key

    def is_valid_rawvalue(self, rawvalue: bytes) -> bool:
        return all(0x20 <= b <= 0x7E or b in (0x00, 0xFF) for b in rawvalue)

    def determine_value(self, rawvalue, msgname, packet_message_type):
        nasa_repo = self.coordinator.nasa_repo
        if packet_message_type == 3:
            value = ""
            if self.is_valid_rawvalue(rawvalue[1:-1]):
                for byte in rawvalue[1:-1]:
                    if byte != 0x00 and byte != 0xFF:
                        char = chr(byte) if 32 <= byte <= 126 else f"{byte}"
                        value += char
                    else:
                        value += " "
                value = value.strip()
            else:
                value = "".join([f"{int(x)}" for x in rawvalue])

            logging.debug(f"Received String Message: {msgname} with raw value: {rawvalue}/{rawvalue.hex()}/{value}")
        else:
            if 'arithmetic' in nasa_repo[msgname]:
                arithmetic = nasa_repo[msgname]['arithmetic'].replace("value", 'packed_value')
            else:
                arithmetic = ''
            packed_value = int.from_bytes(rawvalue, byteorder='big', signed=True)
            if len(arithmetic) > 0:
                try:
                    value = eval(arithmetic)
                except Exception:
                    value = packed_value
            else:
                value = packed_value
            value = round(value, 3)
            if 'type' in nasa_repo[msgname]:
                if nasa_repo[msgname]['type'] == 'ENUM':
                    if 'enum' in nasa_repo[msgname]:
                        value = nasa_repo[msgname]['enum'][int.from_bytes(rawvalue, byteorder='big')]
                    else:
                        value = f"Unknown enum value: {value}"
        return value
    
    def _normalize_name(self, name):
        prefix_to_remove = ['ENUM_', 'LVAR_', 'NASA_', 'VAR_']
        # remove unnecessary prefixes of name
        for prefix in prefix_to_remove:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break

        name_parts = name.split("_")
        tmpname = name_parts[0].lower()
        # construct new name in CamelCase
        for i in range(1, len(name_parts)):
            tmpname += name_parts[i].capitalize()

        return tmpname