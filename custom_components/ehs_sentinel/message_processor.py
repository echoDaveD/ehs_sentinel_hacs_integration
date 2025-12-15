import logging
import time
from datetime import datetime
from homeassistant.helpers.entity import Entity
from .const import PLATFORM_SENSOR

_LOGGER = logging.getLogger(__name__)

class MessageProcessor:
    """Processes NASA packages and creates sensors in Home Assistant."""
    
    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator
        self.entities = {}
        self.value_store = {}
        self.dhw_modes = {}
        self.history_timestamp = {}

    async def process_message(self, packet):
        for msg in packet.packet_messages:
            hexmsg = f"0x{msg.packet_message:04x}"
            msgname = self.search_nasa_table(hexmsg)
            if msgname is not None:
                try:
                    msgvalue = await self.coordinator.determine_value(msg.packet_payload, msgname, msg.packet_message_type)
                except Exception:
                    _LOGGER.error(f"Error determining value for message {msgname} with payload {msg.packet_payload}")
                    _LOGGER.error(f"Packet details: {packet}")
                    continue
                _LOGGER.debug(f"Processing message {msgname} with value {msgvalue}")
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

        """
            SollVL - Wenn Mode AUTO dann gleich sensor.samsung_ehssentinel_intempwaterlawf, wenn HEAT dann sensor.samsung_ehssentinel_indoorsettempwaterout bei Zone 1 und sensor.samsung_ehssentinel_intempwateroutlettargetzone2f bei Zone 2
            Minutes in DHW Mode
            Minutes in HEAT Mode
            Total Power Consumed in Heat Mode
            Total Power Generated in Heat Mode
            Total Power Consumed in DHW Mode
            Total Power Generated in DHW Mode
            Total COP in Heat Mode
            Total COP in DHW Mode
        """

        if msgname == 'NASA_OUTDOOR_OPERATION_STATUS':
            if 'NASA_OUTDOOR_OPERATION_STATUS' in self.value_store:
                
                if self.value_store['NASA_OUTDOOR_OPERATION_STATUS'] == 'OP_STOP' and msgvalue == 'OP_SAFETY':
                    _LOGGER.debug(f"Operation Status changed from {self.value_store['NASA_OUTDOOR_OPERATION_STATUS']} to {msgvalue}")
                    counter_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name('NASA_EHSSENTINEL_START_COUNTER'), {})
                    _LOGGER.debug(f"Current Start Counter Data: {counter_data}")
                    tmpVal = 0
                    if counter_data:
                        current_count = counter_data.get('value', 0)
                        if current_count not in [None, '', 'undefined']:
                            tmpVal = current_count + 1
                        else:
                            tmpVal = 1
                    else:
                        tmpVal = 1
                            
                    _LOGGER.info(f"Incremented Start Counter to: {tmpVal}")
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_START_COUNTER", tmpVal)

                elif self.value_store['NASA_OUTDOOR_OPERATION_STATUS'] == 'OP_NORMAL' and msgvalue == 'OP_DEICE':
                    _LOGGER.info(f"Operation Status changed from {self.value_store['NASA_OUTDOOR_OPERATION_STATUS']} to {msgvalue}")
                    counter_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name('NASA_EHSSENTINEL_DEFROST_COUNTER'), {})
                    _LOGGER.info(f"Current Start Counter Data: {counter_data}")
                    tmpVal = 0
                    if counter_data:
                        current_count = counter_data.get('value', 0)
                        if current_count not in [None, '', 'undefined']:
                            tmpVal = current_count + 1
                        else:
                            tmpVal = 1
                    else:
                        tmpVal = 1

                    _LOGGER.info(f"Incremented Defrost Counter to: {tmpVal}")
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_DEFROST_COUNTER", tmpVal)

        # Calculate Minutes/consumedPower/generated power in DHW and HEAT Mode
        if msgname in ('NASA_DHW_POWER', 'LVAR_IN_MINUTES_ACTIVE', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER'):
            if all(k in self.value_store for k in ['NASA_DHW_POWER', 'LVAR_IN_MINUTES_ACTIVE', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER']):

                if msgname == 'NASA_DHW_POWER':
                    ts = time.time()

                    # DHW modus geht aus, dann Minuten und Power für HEAT zähler speichern
                    if self.value_store['NASA_DHW_POWER'] == 'ON' and msgvalue == 'OFF':
                        
                        # Initialisieren der History Struktur für neues HEAT intervall
                        self.history_timestamp['HEAT'] = {'first_ts': ts, 'last_ts': ts,
                                                        'start_minutes': self.value_store['LVAR_IN_MINUTES_ACTIVE'],
                                                        'start_power_consumed': self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'],
                                                        'start_power_generated': self.value_store['LVAR_IN_TOTAL_GENERATED_POWER'],
                                                        'last_minutes': self.value_store['LVAR_IN_MINUTES_ACTIVE'],
                                                        'last_power_consumed': self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'],
                                                        'last_power_generated': self.value_store['LVAR_IN_TOTAL_GENERATED_POWER']}
                        
                    # DHW modus geht an, dann Minuten und Power für DHW zähler speichern
                    elif self.value_store['NASA_DHW_POWER'] == 'OFF' and msgvalue == 'ON':

                        # Initialisieren der History Struktur für neues DHW intervall
                        self.history_timestamp['DHW'] = {'first_ts': ts, 'last_ts': ts,
                                                        'start_minutes': self.value_store['LVAR_IN_MINUTES_ACTIVE'],
                                                        'start_power_consumed': self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'],
                                                        'start_power_generated': self.value_store['LVAR_IN_TOTAL_GENERATED_POWER'],
                                                        'last_minutes': self.value_store['LVAR_IN_MINUTES_ACTIVE'],
                                                        'last_power_consumed': self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM'],
                                                        'last_power_generated': self.value_store['LVAR_IN_TOTAL_GENERATED_POWER']}
                
                dhw_mode = self.value_store['NASA_DHW_OPERATION_MODE']
                if dhw_mode not in self.dhw_modes:
                    self.dhw_modes[dhw_mode] = True
                    if dhw_mode == 'HEAT':
                        minutes = self.value_store.get('NASA_DHW_TIME_IN_OPERATION_MODE_HEAT', 0)
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_DHW_MODE_MINUTES", minutes)
                    elif dhw_mode == 'DHW':
                        minutes = self.value_store.get('NASA_DHW_TIME_IN_OPERATION_MODE_DHW', 0)
                        await self.protocol_message(msg, "NASA_EHSSENTINEL_DHW_MODE_MINUTES", minutes)

        self.value_store[msgname] = msgvalue

        if msgname in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC', 'NASA_OUTDOOR_OPERATION_STATUS']:
            if all(k in self.value_store for k in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC', 'NASA_OUTDOOR_OPERATION_STATUS']):
                value = round(
                    abs(
                        (self.value_store['NASA_OUTDOOR_TW2_TEMP'] - self.value_store['NASA_OUTDOOR_TW1_TEMP']) *
                        (self.value_store['VAR_IN_FLOW_SENSOR_CALC']/60)
                        * 4190
                    ), 4
                )
                if 0 <= value < 15000 and self.value_store['NASA_OUTDOOR_OPERATION_STATUS'] != "OP_STOP":
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_HEAT_OUTPUT", value)
                else:
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_HEAT_OUTPUT", 0)

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
        
        # SollVL - Wenn Mode AUTO dann gleich sensor.samsung_ehssentinel_intempwaterlawf, wenn HEAT dann sensor.samsung_ehssentinel_indoorsettempwaterout bei Zone 1 und sensor.samsung_ehssentinel_intempwateroutlettargetzone2f bei Zone 2
        if msgname in ('NASA_INDOOR_OPMODE', 'VAR_IN_TEMP_WATER_LAW_F', 'NASA_INDOOR_SETTEMP_WATEROUT', 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 'NASA_POWER_ZONE2', 'NASA_POWER'):
            if all(k in self.value_store for k in ['NASA_INDOOR_OPMODE', 'NASA_POWER_ZONE2', 'NASA_POWER']):
                if self.value_store['NASA_INDOOR_OPMODE'] == 'AUTO':
                    vl_set = self.value_store.get('VAR_IN_TEMP_WATER_LAW_F', 0)
                elif self.value_store['NASA_INDOOR_OPMODE'] == 'HEAT':
                    if self.value_store['NASA_POWER'] == 'ON':
                        vl_set = self.value_store.get('NASA_INDOOR_SETTEMP_WATEROUT', 0)
                    elif self.value_store['NASA_POWER_ZONE2'] == 'ON':
                        vl_set = self.value_store.get('VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 0)
                    else:
                        vl_set = None
                else: 
                    vl_set = None

                if vl_set is not None:
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_CURRENT_TARGET_FLOW_TEMP", vl_set)
        
    
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