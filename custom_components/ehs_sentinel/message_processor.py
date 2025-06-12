from homeassistant.helpers.entity import Entity

class EHSSentinelSensor(Entity):
    """Home Assistant Sensor für EHS Sentinel Werte."""
    def __init__(self, name, value, coordinator):
        self._attr_name = name
        self._state = value
        self.coordinator = coordinator

    @property
    def state(self):
        return self._state

    def update_value(self, value):
        self._state = value
        self.schedule_update_ha_state()

class MessageProcessor:
    """Verarbeitet NASA-Pakete und legt Sensoren in Home Assistant an."""
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
                await self.protocolMessage(msg, msgname, msgvalue)

    async def protocolMessage(self, msg, msgname, msgvalue):
        # Sensor anlegen oder aktualisieren
        if msgname not in self.entities:
            entity = EHSSentinelSensor(msgname, msgvalue, self.coordinator)
            self.entities[msgname] = entity
            await self.hass.helpers.entity_component.async_add_entities([entity])
        else:
            self.entities[msgname].update_value(msgvalue)
        self.value_store[msgname] = msgvalue

        # Beispiel für abgeleitete Werte (wie bisher)
        if msgname in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC']:
            if all(k in self.value_store for k in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC']):
                value = round(
                    abs(
                        (self.value_store['NASA_OUTDOOR_TW2_TEMP'] - self.value_store['NASA_OUTDOOR_TW1_TEMP']) *
                        (self.value_store['VAR_IN_FLOW_SENSOR_CALC']/60)
                        * 4190
                    ), 4
                )
                if 0 < value < 15000:
                    await self.protocolMessage(msg, "NASA_EHSSENTINEL_HEAT_OUTPUT", value)

        if msgname in ('NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT'):
            if all(k in self.value_store for k in ['NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']):
                if self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT'] > 0:
                    value = round(
                        (self.value_store['NASA_EHSSENTINEL_HEAT_OUTPUT'] /
                         self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']/1000.), 3)
                    if 0 < value < 20:
                        await self.protocolMessage(msg, "NASA_EHSSENTINEL_COP", value)

        if msgname in ('NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER'):
            if all(k in self.value_store for k in ['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER']):
                if self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'] > 0:
                    value = round(
                        self.value_store['LVAR_IN_TOTAL_GENERATED_POWER'] /
                        self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'], 3)
                    if 0 < value < 20:
                        await self.protocolMessage(msg, "NASA_EHSSENTINEL_TOTAL_COP", value)

    def search_nasa_table(self, address):
        # Hier muss die NASA_REPO als dict im Coordinator liegen!
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