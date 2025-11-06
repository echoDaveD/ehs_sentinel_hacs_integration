import logging
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

        if msgname == 'NASA_OUTDOOR_OPERATION_STATUS':
            if 'NASA_OUTDOOR_OPERATION_STATUS' in self.value_store:
                if self.value_store['NASA_OUTDOOR_OPERATION_STATUS'] == 'OP_STOP' and msgvalue == 'OP_SAFETY':
                    counter_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name('NASA_EHSSENTINEL_START_COUNTER'), {})
                    tmpVal = 0
                    if counter_data:
                        current_count = counter_data.get('value', 0)
                        tmpVal = current_count + 1
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_START_COUNTER", tmpVal)

                elif self.value_store['NASA_OUTDOOR_OPERATION_STATUS'] == 'OP_NORMAL' and msgvalue == 'OP_DEICE':
                    counter_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name('NASA_EHSSENTINEL_DEFROST_COUNTER'), {})
                    tmpVal = 0
                    if counter_data:
                        current_count = counter_data.get('value', 0)
                        tmpVal = current_count + 1
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_DEFROST_COUNTER", tmpVal)

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

        if msgname in ('NASA_OUTDOOR_OUT_TEMP', 'VAR_IN_FSV_2011', 'VAR_IN_FSV_2012', 'VAR_IN_FSV_2021', 'VAR_IN_FSV_2022', 'VAR_IN_TEMP_WATER_LAW_TARGET_F'):
            if all(k in self.value_store for k in ['NASA_OUTDOOR_OUT_TEMP', 'VAR_IN_FSV_2011', 'VAR_IN_FSV_2012', 'VAR_IN_FSV_2021', 'VAR_IN_FSV_2022', 'VAR_IN_TEMP_WATER_LAW_TARGET_F']):
                out_temp = self.value_store['NASA_OUTDOOR_OUT_TEMP']
                at_max = self.value_store['VAR_IN_FSV_2011']
                at_min = self.value_store['VAR_IN_FSV_2012']
                vl_max = self.value_store['VAR_IN_FSV_2021']
                vl_min = self.value_store['VAR_IN_FSV_2022']
                shift = self.value_store.get('VAR_IN_TEMP_WATER_LAW_TARGET_F', 0.0)

                vl_set = self.compute_supply_temp(out_temp, at_max, at_min, vl_max, vl_min, shift)
                if vl_set is not None:
                    await self.protocol_message(msg, "NASA_EHSSENTINEL_CURRENT_TARGET_FLOW_TEMP", vl_set)
        
    def compute_supply_temp(self, out_temp, at_max, at_min, vl_max, vl_min, shift=0.0):
        """Berechnet Ziel-Vorlauftemperatur (linear zwischen zwei Punkten) und wendet Verschiebung an.
        - out_temp: aktuelle Außentemperatur
        - at_max: Außen-Temperatur für vl_max
        - at_min: Außen-Temperatur für vl_min
        - vl_max: Vorlauftemperatur bei at_max
        - vl_min: Vorlauftemperatur bei at_min
        - shift: Verschiebung (positive -> höher)
        Rückgabe: float (gerundet auf 2 Nachkommastellen) oder None bei ungültigen Eingaben.
        """
        try:
            # nötige Werte in Floats konvertieren
            T_out = float(out_temp)
            AT_max = float(at_max)
            AT_min = float(at_min)
            VL_max = float(vl_max)
            VL_min = float(vl_min)
            shift_v = float(shift) if shift is not None else 0.0
        except (TypeError, ValueError):
            return None

        # Vermeidung Division durch Null
        if AT_max == AT_min:
            return None

        # lineare Interpolation / Extrapolation
        slope = (VL_max - VL_min) / (AT_max - AT_min)
        vl_target = VL_min + slope * (T_out - AT_min)

        # Verschiebung anwenden
        vl_target += shift_v

        # Begrenze auf das Intervall [min(VL_min, VL_max), max(...)]
        if vl_target < VL_min:
            vl_target = VL_min
        elif vl_target > VL_max:
            vl_target = VL_max

        return round(vl_target, 1)
    
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