import logging
from datetime import datetime
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
                    msgvalue = await self.coordinator.determine_value(msg.packet_payload, msgname, msg.packet_message_type)
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
        
        tmpdict = {"value": value, "nasa_name": msgname}
        if self.coordinator.force_refresh:
            tmpdict["nasa_last_seen"] = datetime.now().isoformat(timespec="seconds")
            
        await self.coordinator.update_data_safe({entity_platform: {self._normalize_name(msgname): tmpdict}})
        self.coordinator.confirm_write(msgname, msgvalue) 
        self.coordinator.confirm_read(msgname) 
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
                    if 0 <= value < 20:
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_COP", value)

        if msgname in ('NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER'):
            if all(k in self.value_store for k in ['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER']):
                if self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'] > 0:
                    value = round(
                        self.value_store['LVAR_IN_TOTAL_GENERATED_POWER'] /
                        self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'], 3)
                    if 0 <= value < 20:
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_TOTAL_COP", value)

    def search_nasa_table(self, address):
        for key, value in self.coordinator.nasa_repo.items():
            if value['address'].lower() == address:
                return key
    
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