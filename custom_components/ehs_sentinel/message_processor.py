import logging, traceback, random
from datetime import datetime, timedelta
from homeassistant.helpers.entity import Entity
from .const import PLATFORM_SENSOR

_LOGGER = logging.getLogger(__name__)

DELTA_SOURCES = {
    'LVAR_IN_MINUTES_ACTIVE': (
        'NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_DHW_MODE',
        'NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE'
    ),
    'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM': (
        'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER'
    ),
    'LVAR_IN_TOTAL_GENERATED_POWER': (
        'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_GENERATED_POWER_DHW_MODE',
        'NASA_EHSSENTINEL_DAILY_GENERATED_POWER_HEAT_MODE',
        'NASA_EHSSENTINEL_DAILY_GENERATED_POWER'
    ),
}

COP_MAP = {"NASA_EHSSENTINEL_COP": ("LVAR_IN_GENERATED_POWER_LAST_MINUTE", "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT"),
            "NASA_EHSSENTINEL_TOTAL_COP": ("LVAR_IN_TOTAL_GENERATED_POWER", "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"),
            "NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE": ("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE", "NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE"),
            "NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE": ("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE", "NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"),
            "NASA_EHSSENTINEL_DAILY_COP_DHW_MODE": ("NASA_EHSSENTINEL_DAILY_GENERATED_POWER_DHW_MODE", "NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_DHW_MODE"),
            "NASA_EHSSENTINEL_DAILY_COP_HEAT_MODE": ("NASA_EHSSENTINEL_DAILY_GENERATED_POWER_HEAT_MODE", "NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_HEAT_MODE"),
            "NASA_EHSSENTINEL_DAILY_COP": ("NASA_EHSSENTINEL_DAILY_GENERATED_POWER", "NASA_EHSSENTINEL_DAILY_CONSUMED_POWER")
        }

DAILY_MESSAGES = ['NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_DHW_MODE', 'NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_HEAT_MODE', 'NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE',
                  'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_DHW_MODE', 'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_HEAT_MODE', 'NASA_EHSSENTINEL_DAILY_CONSUMED_POWER',
                  'NASA_EHSSENTINEL_DAILY_GENERATED_POWER_DHW_MODE', 'NASA_EHSSENTINEL_DAILY_GENERATED_POWER_HEAT_MODE', 'NASA_EHSSENTINEL_DAILY_GENERATED_POWER',
                  'NASA_EHSSENTINEL_DAILY_COP_DHW_MODE', 'NASA_EHSSENTINEL_DAILY_COP_HEAT_MODE', 'NASA_EHSSENTINEL_DAILY_COP']

class MessageProcessor:
    """Processes NASA packages and creates sensors in Home Assistant."""

    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator
        self.entities = {}
        self.value_store = {}
        self.dhw_power_store = {'val': 'OFF', 'dt': datetime.now().isoformat()} 
        self.set_mode = None
        self.last_dt = None

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
        dt = datetime.now()

        if self.last_dt is not None:
            if datetime.fromisoformat(self.last_dt).date() < dt.date():
                if self.coordinator.extended_logging:
                    _LOGGER.info(f"New day detected. Resetting daily counters.")
                self.last_dt = dt.isoformat()
                for daily_msg in DAILY_MESSAGES:
                    await self.protocol_message(daily_msg, 0)

        dt = dt.isoformat()

        # Bestimme die Plattform basierend auf den NASA-Optionen
        platform = self._get_platform(msgname)

        ## Spezielle Handhabung für bestimmte Nachrichten

        # Spezielle Handhabung für Operation Status
        if msgname == 'NASA_OUTDOOR_OPERATION_STATUS':
            try:
                await self._handle_operation_status(msgvalue)
            except Exception as e:
                _LOGGER.error(f"Error handling operation status for {msgname}: {e}")
                traceback.print_exc()

        # kalukuliere Minuten/Wattstunden in DHW/HEAT Mode
        if msgname in DELTA_SOURCES:
            _LOGGER.debug(f"Handling mode delta for {msgname} with value {msgvalue}")
            try:
                await self._handle_mode_delta(msgname, msgvalue, dt)
            except Exception as e:
                _LOGGER.error(f"Error handling mode delta for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für DHW Power Modus
        if msgname == 'NASA_DHW_POWER':
            try:
                self._update_mode(msgvalue, dt)
            except Exception as e:
                _LOGGER.error(f"Error updating mode for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für Heat Output
        if msgname in ['LVAR_IN_GENERATED_POWER_LAST_MINUTE']:
            try:
                await self._handle_heat_output(msgname, msgvalue, dt)
            except Exception as e:
                _LOGGER.error(f"Error handling heat output for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für COP Berechnung
        if any(msgname in keys for keys in COP_MAP.values()):
            try:
                await self._handle_cop(msgname, msgvalue, dt)
            except Exception as e:
                _LOGGER.error(f"Error handling COP for {msgname}: {e}")
                traceback.print_exc()

        # SollVL - Wenn Mode AUTO dann gleich sensor.samsung_ehssentinel_intempwaterlawf, wenn HEAT dann sensor.samsung_ehssentinel_indoorsettempwaterout bei Zone 1 und sensor.samsung_ehssentinel_intempwateroutlettargetzone2f bei Zone 2
        if msgname in ('NASA_INDOOR_OPMODE', 'VAR_IN_TEMP_WATER_LAW_F', 'NASA_INDOOR_SETTEMP_WATEROUT', 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 'NASA_POWER_ZONE2', 'NASA_POWER'):
            try:
                await self._handle_sollvl(msgname, msgvalue, dt)
            except Exception as e:
                _LOGGER.error(f"Error handling SollVL for {msgname}: {e}")
                traceback.print_exc()

    
        ## Endgültige Verarbeitung: Normalisieren, im Coordinator aktualisieren und HA-Status aktualisieren
        
        # Normalisiere den Wert, falls erforderlich
        value = self._normalize_value(msgvalue)
        payload = {"value": value, "nasa_name": msgname}

        # Füge nasa_last_seen hinzu, wenn force_refresh aktiviert ist
        if self.coordinator.force_refresh or (msgname.startswith("NASA_EHSSENTINEL_") or 
                                              msgname in ('LVAR_IN_MINUTES_ACTIVE', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER', 'NASA_DHW_POWER')):
            payload["nasa_last_seen"] = datetime.fromisoformat(dt).isoformat(timespec="seconds")

        # Aktualisiere die Daten im Coordinator
        await self.coordinator.update_data_safe(
            {platform: {self._normalize_name(msgname): payload}}
        )

        # Bestätige die Lese- und Schreibvorgänge
        self.coordinator.confirm_write(msgname, msgvalue)
        self.coordinator.confirm_read(msgname)

        self.value_store[msgname] = {'val': msgvalue, 'dt': dt}
        self.last_dt = dt

    async def _handle_heat_output(self, msgname, msgvalue, dt):
        await self.protocol_message("NASA_EHSSENTINEL_HEAT_OUTPUT", msgvalue*1000)  # Umrechnung von kW auf W

    async def _handle_sollvl(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_INDOOR_OPMODE', 'NASA_POWER_ZONE2', 'NASA_POWER']):
            nasa_opmode = self.value_store['NASA_INDOOR_OPMODE']['val'] if msgname != 'NASA_INDOOR_OPMODE' else msgvalue
            nasa_power_zone1 = self.value_store['NASA_POWER']['val'] if msgname != 'NASA_POWER' else msgvalue
            nasa_power_zone2 = self.value_store['NASA_POWER_ZONE2']['val'] if msgname != 'NASA_POWER_ZONE2' else msgvalue
            
            if nasa_opmode.upper() == 'AUTO':
                vl_set = self.value_store.get('VAR_IN_TEMP_WATER_LAW_F', 0)
            elif nasa_opmode.upper() == 'HEAT':
                if nasa_power_zone1 == 'ON':
                    vl_set = self.value_store.get('NASA_INDOOR_SETTEMP_WATEROUT', 0).get('val', 0) if msgname != 'NASA_INDOOR_SETTEMP_WATEROUT' else msgvalue
                elif nasa_power_zone2 == 'ON':
                    vl_set = self.value_store.get('VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 0).get('val', 0) if msgname != 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F' else msgvalue
                else:
                    vl_set = None
            else: 
                vl_set = None

            if vl_set is not None:
                await self.protocol_message("NASA_EHSSENTINEL_CURRENT_TARGET_FLOW_TEMP", round(vl_set, 2))

    async def _handle_cop(self, msgname, msgvalue, dt):
        for cop_sensor, (gen_key, cons_key) in COP_MAP.items():
            if all(k in self.value_store for k in [gen_key, cons_key]):
                gen_val = self.value_store.get(gen_key, {}).get('val', 0) if msgname != gen_key else msgvalue
                cons_val = self.value_store.get(cons_key, {}).get('val', 0) if msgname != cons_key else msgvalue
                if cons_val >= 0 and gen_val is not None:
                    await self.protocol_message(cop_sensor, self._calculate_cop(gen_val, cons_val))

    def _calculate_cop(self, heat_output, power_input):
        if power_input > 0:
            return round(heat_output / power_input, 3)
        return 0

    
    def _update_mode(self, msgvalue, dt):
        if 'NASA_DHW_POWER' in self.value_store:  # Testweise 10% Chance, dass der Modus aktualisiert wird, um die Mode-Delta Berechnung zu testen
            if self.dhw_power_store.get('val') != msgvalue:
                if self.coordinator.extended_logging:
                    _LOGGER.info(f"Updating DHW/HEAT mode to {msgvalue} based on DHW_POWER change from {self.dhw_power_store.get('val')} to {msgvalue}")
                self.dhw_power_store['val'] = msgvalue
                self.dhw_power_store['dt'] = dt
    
    async def _handle_mode_delta(self, msgname, msgvalue, dt):
        # Initialisiere nur den betroffenen Key falls nötig
        if self.value_store.get(msgname, {}).get('val', None) is None:
            if self.coordinator.extended_logging:
                _LOGGER.info(f"Initializing value store for {msgname} as it was not set.")
            for k in [msgname] + list(DELTA_SOURCES[msgname]):  # Alle abhängigen Keys initialisieren
                if self.value_store.get(k, {}).get('val', None) is None:
                    sensor_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name(k), {})
                    if sensor_data.get('value', None) is not None:
                        tmpDt = sensor_data.get('nasa_last_seen', dt)
                        if datetime.fromisoformat(tmpDt).date() == datetime.fromisoformat(dt).date() or k not in DAILY_MESSAGES:  # Nur initialisieren, wenn der letzte Stand von heute ist oder es kein Tageswert ist
                            self.value_store[k] = {
                                'val': sensor_data.get('value', None),
                                'dt': tmpDt
                            }
                            if self.coordinator.extended_logging:
                                _LOGGER.info(f"Initialized value store for {k} with value {sensor_data.get('value', None)} and timestamp {tmpDt}")
                        else: 
                            if self.coordinator.extended_logging:                  
                                _LOGGER.info(f"Setting value for {k} to 0 because last seen date is not today.")
                            await self.protocol_message(k, 0) # Setze Tageswerte auf 0, wenn der letzte Stand von einem anderen Tag ist
                    else:
                        if self.coordinator.extended_logging:
                            _LOGGER.info(f"No value found for {k} during initialization of mode delta handling")

        # Prüfe, ob alle Keys initialisiert sind
        # if not all(k in self.value_store for k in DELTA_SOURCES):
        #     return

        old = self.value_store.get(msgname)
        if self.coordinator.extended_logging:
            _LOGGER.info(f"Handling mode delta for {msgname}. Old value: {old}, New value: {msgvalue}")
        if not old or old['val'] is None:
            return
        
        if 'val' not in self.dhw_power_store:
            return

        old_dt = datetime.fromisoformat(old['dt'])
        new_dt = datetime.fromisoformat(dt)
        delta_time = (new_dt - old_dt).total_seconds()
        delta = round(msgvalue - old['val'], 2)
        if delta < 0 or delta_time <= 0:
            return

        target_dhw, target_heat, daily_dhw, daily_heat, daily = DELTA_SOURCES[msgname]
        try:
            dhw_val = round(self.value_store.get(target_dhw, {}).get('val', 0) or 0, 2)
            heat_val = round(self.value_store.get(target_heat, {}).get('val', 0) or 0, 2)
            daily_dhw_val = round(self.value_store.get(daily_dhw, {}).get('val', 0) or 0, 2)
            daily_heat_val = round(self.value_store.get(daily_heat, {}).get('val', 0) or 0, 2)
            daily_val = round(self.value_store.get(daily, {}).get('val', 0) or 0, 2)
        except Exception as e:
            _LOGGER.error(f"Error retrieving values for mode delta calculation: {e}")
            return

        try:    
            await self.protocol_message(daily, daily_val + delta)
        except Exception as e:
            _LOGGER.error(f"Error updating daily total for {daily}: {e}")
            traceback.print_exc()

        ## Delta-Verteilung je nach Modus
        mode_dt = datetime.fromisoformat(self.dhw_power_store['dt'])

        # Verteilung für DHW/HEAT
        if self.set_mode is not None:
            is_dhw = self.set_mode == 'DHW' # Wenn ein Modus manuell gesetzt wurde, verwende diesen für die Verteilung
        else:
            is_dhw = self.dhw_power_store['val'] == 'ON'
        
        main_target, main_val, main_daily, main_daily_val = (
            (target_dhw, dhw_val, daily_dhw, daily_dhw_val) if is_dhw else (target_heat, heat_val, daily_heat, daily_heat_val)
        )
        sec_target, sec_val, sec_daily, sec_daily_val = (
            (target_heat, heat_val, daily_heat, daily_heat_val) if is_dhw else (target_dhw, dhw_val, daily_dhw, daily_dhw_val)
        )


        if old_dt <= mode_dt < new_dt:
            if self.coordinator.extended_logging:
                _LOGGER.info(f"Splitting delta of {delta} between main target {main_target} and secondary target {sec_target} based on mode change timestamp")
                _LOGGER.info(f"Mode change timestamp: {mode_dt}, Old value timestamp: {old_dt}, Delta time: {delta_time} seconds")
                _LOGGER.info(f"Applying delta of {delta} split into {delta * (mode_dt - old_dt).total_seconds() / delta_time} for main target {main_target} and {delta * (new_dt - mode_dt).total_seconds() / delta_time} for secondary target {sec_target}")
            t1 = (mode_dt - old_dt).total_seconds()
            t2 = delta_time - t1
            d1 = delta * round(t1 / delta_time, 2)
            d2 = delta * round(t2 / delta_time, 2)
            await self.protocol_message(main_target, round(main_val + d1, 2))
            await self.protocol_message(main_daily, round(main_daily_val + d1, 2))
            await self.protocol_message(sec_target, round(sec_val + d2, 2))
            await self.protocol_message(sec_daily, round(sec_daily_val + d2, 2))
        else:
            if self.coordinator.extended_logging:
                _LOGGER.info(f"Applying full delta to main target {main_target} as mode change is more recent than last value change")
                _LOGGER.info(f"Mode change timestamp: {mode_dt}, Old value timestamp: {old_dt}, Delta time: {delta_time} seconds")
                _LOGGER.info(f"Applying delta of {delta} to main target {main_target} with old value {main_val}")
            await self.protocol_message(main_target, round(main_val + delta, 2))
            await self.protocol_message(main_daily, round(main_daily_val + delta, 2))
            

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
    
    async def development_tool(self, tool_name):
        if tool_name == "set_mode_heat":
            self.set_mode = 'HEAT'
            _LOGGER.info(f"Set mode to {self.set_mode}")
        elif tool_name == "set_mode_dhw":
            self.set_mode = 'DHW'
            _LOGGER.info(f"Set mode to {self.set_mode}")
        elif tool_name == "disable_set_mode":
            self.set_mode = None
            _LOGGER.info(f"Disabled DHW/HEAT mode tracking")
        elif tool_name == "reset_daily_counters" or tool_name == "reset_all_counters":
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_DHW_MODE", 0) 
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE_HEAT_MODE", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_MINUTES_ACTIVE", 0) 
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_DHW_MODE", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_CONSUMED_POWER_HEAT_MODE", 0) 
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_CONSUMED_POWER", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_GENERATED_POWER_DHW_MODE", 0) 
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_GENERATED_POWER_HEAT_MODE", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_GENERATED_POWER", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_COP_DHW_MODE", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_COP_HEAT_MODE", 0)
            await self.protocol_message("NASA_EHSSENTINEL_DAILY_COP", 0)
            _LOGGER.info("Reset daily counters for minutes active, consumed power and generated power in both modes")

            if tool_name == "reset_all_counters":
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE", 0) 
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE", 0)
                _LOGGER.info("Reset total counters for minutes active, consumed power and generated power in both modes")
        else:
            _LOGGER.warning(f"Unknown development tool requested: {tool_name}")