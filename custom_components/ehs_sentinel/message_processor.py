import logging
import time
from datetime import datetime
from homeassistant.helpers.entity import Entity
from .const import PLATFORM_SENSOR

_LOGGER = logging.getLogger(__name__)
DELTA_SOURCES = {
    'LVAR_IN_MINUTES_ACTIVE': (
        'NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_HEAT_MODE',
    ),
    'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM': (
        'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE',
    ),
    'LVAR_IN_TOTAL_GENERATED_POWER': (
        'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE',
    ),
}

class MessageProcessor:
    """Processes NASA packages and creates sensors in Home Assistant."""

    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator
        self.entities = {}
        self.value_store = {}
        self.history_totals = {}

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
                await self.protocol_message(msgname, msgvalue)

    async def protocol_message(self, msgname, msgvalue):
        dt = datetime.now().isoformat()
        # Bestimme die Plattform basierend auf den NASA-Optionen
        platform = self._get_platform(msgname)

        # Normalisiere den Wert, falls erforderlich
        value = self._normalize_value(msgvalue)
        payload = {"value": value, "nasa_name": msgname}

        # Füge nasa_last_seen hinzu, wenn force_refresh aktiviert ist
        if self.coordinator.force_refresh:
            payload["nasa_last_seen"] = datetime.fromisoformat(dt).isoformat(timespec="seconds")

        # Aktualisiere die Daten im Coordinator
        await self.coordinator.update_data_safe(
            {platform: {self._normalize_name(msgname): payload}}
        )

        # Bestätige die Lese- und Schreibvorgänge
        self.coordinator.confirm_write(msgname, msgvalue)
        self.coordinator.confirm_read(msgname)

        ## Spezielle Handhabung für bestimmte Nachrichten

        # Spezielle Handhabung für Operation Status
        if msgname == 'NASA_OUTDOOR_OPERATION_STATUS':
            await self._handle_operation_status(msgvalue)

        # kalukuliere Minuten/Wattstunden in DHW/HEAT Mode
        if msgname in DELTA_SOURCES:
            await self._handle_mode_delta(msgname, msgvalue, dt)

        # Spezielle Handhabung für DHW Power Modus
        if msgname == 'NASA_DHW_POWER':
            self._update_mode(msgvalue, dt)

        # Spezielle Handhabung für Heat Output
        if msgname in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC', 'NASA_OUTDOOR_OPERATION_STATUS']:
            await self._handle_heat_output(msgname, msgvalue, dt)

        # Spezielle Handhabung für COP Berechnung
        if msgname in ('NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT'):
            await self._handle_current_cop(msgname, msgvalue, dt)
        
        # Spezielle Handhabung für Total COP Berechnung
        if msgname in ('NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 
                       'LVAR_IN_TOTAL_GENERATED_POWER', 
                       'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE', 
                       'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE',
                       'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE',
                       'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE'):
            await self._handle_total_cop(msgname, msgvalue, dt)
        
        # SollVL - Wenn Mode AUTO dann gleich sensor.samsung_ehssentinel_intempwaterlawf, wenn HEAT dann sensor.samsung_ehssentinel_indoorsettempwaterout bei Zone 1 und sensor.samsung_ehssentinel_intempwateroutlettargetzone2f bei Zone 2
        if msgname in ('NASA_INDOOR_OPMODE', 'VAR_IN_TEMP_WATER_LAW_F', 'NASA_INDOOR_SETTEMP_WATEROUT', 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 'NASA_POWER_ZONE2', 'NASA_POWER'):
            await self._handle_sollvl(msgname, msgvalue, dt)

        self.value_store[msgname] = {'val': msgvalue, 'dt': dt}

    async def _handle_heat_output(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_OUTDOOR_TW2_TEMP', 'NASA_OUTDOOR_TW1_TEMP', 'VAR_IN_FLOW_SENSOR_CALC', 'NASA_OUTDOOR_OPERATION_STATUS']):
            tw2_temp = self.value_store['NASA_OUTDOOR_TW2_TEMP']['val'] if msgname != 'NASA_OUTDOOR_TW2_TEMP' else msgvalue
            tw1_temp = self.value_store['NASA_OUTDOOR_TW1_TEMP']['val'] if msgname != 'NASA_OUTDOOR_TW1_TEMP' else msgvalue
            flow_sensor_calc = self.value_store['VAR_IN_FLOW_SENSOR_CALC']['val'] if msgname != 'VAR_IN_FLOW_SENSOR_CALC' else msgvalue
            op_status = self.value_store['NASA_OUTDOOR_OPERATION_STATUS']['val'] if msgname != 'NASA_OUTDOOR_OPERATION_STATUS' else msgvalue
            value = round(
                abs(
                    (tw2_temp - tw1_temp) *
                    (flow_sensor_calc/60)
                    * 4190
                ), 4
            )
            if 0 <= value < 15000 and op_status != "OP_STOP":
                await self.protocol_message("NASA_EHSSENTINEL_HEAT_OUTPUT", value)
            else:
                await self.protocol_message("NASA_EHSSENTINEL_HEAT_OUTPUT", 0)

    async def _handle_sollvl(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_INDOOR_OPMODE', 'NASA_POWER_ZONE2', 'NASA_POWER']):
            nasa_opmode = self.value_store['NASA_INDOOR_OPMODE']['val'] if msgname != 'NASA_INDOOR_OPMODE' else msgvalue
            nasa_power_zone1 = self.value_store['NASA_POWER']['val'] if msgname != 'NASA_POWER' else msgvalue
            nasa_power_zone2 = self.value_store['NASA_POWER_ZONE2']['val'] if msgname != 'NASA_POWER_ZONE2' else msgvalue
            
            if nasa_opmode == 'AUTO':
                vl_set = self.value_store.get('VAR_IN_TEMP_WATER_LAW_F', 0)
            elif nasa_opmode == 'HEAT':
                if nasa_power_zone1 == 'ON':
                    vl_set = self.value_store.get('NASA_INDOOR_SETTEMP_WATEROUT', 0).get('val', 0) if msgname != 'NASA_INDOOR_SETTEMP_WATEROUT' else msgvalue
                elif nasa_power_zone2 == 'ON':
                    vl_set = self.value_store.get('VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 0).get('val', 0) if msgname != 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F' else msgvalue
                else:
                    vl_set = None
            else: 
                vl_set = None

            if vl_set is not None:
                await self.protocol_message("NASA_EHSSENTINEL_CURRENT_TARGET_FLOW_TEMP", vl_set)

    async def _handle_total_cop(self, msgname, msgvalue, dt):
        if msgname in ['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER'] and all(k in self.value_store for k in ['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER']):
            wattmeter_accum = self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM']['val'] if msgname != 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM' else msgvalue
            total_generated_power = self.value_store['LVAR_IN_TOTAL_GENERATED_POWER']['val'] if msgname != 'LVAR_IN_TOTAL_GENERATED_POWER' else msgvalue
            if wattmeter_accum > 0:
                value = round(
                    total_generated_power /
                    wattmeter_accum, 3)
                if 0 <= value < 20:
                    await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP", value)

        if msgname in ['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE', 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE'] and all(k in self.value_store for k in ['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE', 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE']):
            consumed_dhw = self.value_store['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE']['val'] if msgname != 'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE' else msgvalue
            generated_dhw = self.value_store['NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE']['val'] if msgname != 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE' else msgvalue
            if consumed_dhw > 0:
                value = round(
                    generated_dhw /
                    consumed_dhw, 3)
                if 0 <= value < 20:
                    await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE", value)
        
        if msgname in ['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE', 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE'] and all(k in self.value_store for k in ['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE', 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE']):
            consumed_heat = self.value_store['NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE']['val'] if msgname != 'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE' else msgvalue
            generated_heat = self.value_store['NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE']['val'] if msgname != 'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE' else msgvalue
            if consumed_heat > 0:
                value = round(
                    generated_heat /
                    consumed_heat, 3)
                if 0 <= value < 20:
                    await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE", value)

    async def _handle_current_cop(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_EHSSENTINEL_HEAT_OUTPUT', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']):
            wattmeter_all_unit = self.value_store['NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT']['val'] if msgname != 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT' else msgvalue
            heat_output = self.value_store['NASA_EHSSENTINEL_HEAT_OUTPUT']['val'] if msgname != 'NASA_EHSSENTINEL_HEAT_OUTPUT' else msgvalue
            if wattmeter_all_unit > 0:
                value = round(
                    (heat_output /
                        wattmeter_all_unit/1000.), 3)
                if 0 <= value < 20:
                    await self.protocol_message("NASA_EHSSENTINEL_COP", value)
    
    def _update_mode(self, msgvalue, dt):
        if 'NASA_DHW_POWER' in self.value_store:
            old = self.value_store.get('NASA_DHW_POWER', {}).get('val')
            self.history_totals['dt'] = dt

            if old == 'ON' and msgvalue == 'OFF':
                self.history_totals['MODE'] = 'DHW2HEAT'
            elif old == 'OFF' and msgvalue == 'ON':
                self.history_totals['MODE'] = 'HEAT2DHW'
            elif msgvalue == 'ON':
                self.history_totals['MODE'] = 'DHW'
            else:
                self.history_totals['MODE'] = 'HEAT'
    
    async def _handle_mode_delta(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in DELTA_SOURCES):
            old = self.value_store.get(msgname)
            if not old:
                return

            old_dt = datetime.fromisoformat(old['dt'])
            delta_time = (datetime.fromisoformat(dt) - old_dt).total_seconds()
            delta = msgvalue - old['val']

            if delta <= 0 or delta_time <= 0:
                return

            target_dhw, target_heat = DELTA_SOURCES[msgname]
            mode = self.history_totals['MODE']
            mode_dt = datetime.fromisoformat(self.history_totals['dt'])

            dhw_val = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name(target_dhw), {}).get('value', 0) or 0
            heat_val = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name(target_heat), {}).get('value', 0) or 0

            if mode in ('DHW2HEAT', 'HEAT2DHW') and old_dt < mode_dt < datetime.fromisoformat(dt):
                t1 = (mode_dt - old_dt).total_seconds()
                t2 = delta_time - t1

                d1 = delta * (t1 / delta_time)
                d2 = delta * (t2 / delta_time)

                if mode == 'DHW2HEAT':
                    await self.protocol_message(target_dhw, dhw_val + d1)
                    await self.protocol_message(target_heat, heat_val + d2)
                else:
                    await self.protocol_message(target_heat, heat_val + d1)
                    await self.protocol_message(target_dhw, dhw_val + d2)

            elif mode == 'DHW':
                await self.protocol_message(target_dhw, dhw_val + delta)
            elif mode == 'HEAT':
                await self.protocol_message(target_heat, heat_val + delta)
    
    async def _handle_operation_status(self, msgvalue):
        if 'NASA_OUTDOOR_OPERATION_STATUS' in self.value_store:
            old = self.value_store.get('NASA_OUTDOOR_OPERATION_STATUS', {}).get('val')

            if old == 'OP_STOP' and msgvalue == 'OP_SAFETY':
                await self._increment_counter("NASA_EHSSENTINEL_START_COUNTER")

            elif old == 'OP_NORMAL' and msgvalue == 'OP_DEICE':
                await self._increment_counter("NASA_EHSSENTINEL_DEFROST_COUNTER")

    async def _increment_counter(self, counter_name):
        current = (
            self.coordinator.data
            .get(PLATFORM_SENSOR, {})
            .get(self._normalize_name(counter_name), {})
            .get('value', 0)
        ) or 0
        if current in [None, '', 'undefined']:
            current = 0
        
        await self.protocol_message(counter_name, current + 1)
    
    def _get_platform(self, msgname):
        opts = self.coordinator.nasa_repo[msgname]['hass_opts']
        if opts['writable'] and self.coordinator.writemode:
            return opts['platform']['type']
        return opts['default_platform']

    def _normalize_value(self, value):
        if isinstance(value, float):
            return round(value, 2)
        return value
    
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