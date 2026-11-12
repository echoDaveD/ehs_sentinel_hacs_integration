import logging, traceback, random
import asyncio
from datetime import datetime, timedelta
from homeassistant.helpers.entity import Entity
from .const import PLATFORM_SENSOR, DOMAIN

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
        self._logger = coordinator._logger
        self.entities = {}
        self.value_store = {}
        self.dhw_power_store = {'val': 'OFF', 'dt': datetime.now().isoformat()} 
        self.set_mode = None
        self.last_dt = None
        self.determine_value_store = {}
        self._software_versions_logged = False
        self._missing_mode_totals = set()
        self._mode_total_baselines_pending = set()
        self._mode_energy_baselines_pending = set()
        self._daily_baselines_pending = set()
        self._mode_history = []
        self._cop_operation_status = None
        self._cop_generated_power = None
        self._cop_consumed_power = None
        self._protocol_lock = asyncio.Lock()
        self._protocol_owner = None

    async def process_message(self, packet):
        for msg in packet.packet_messages:
            hexmsg = f"0x{msg.packet_message:04x}"
            msgname = self.search_nasa_table(
                hexmsg,
                packet.packet_source_address_class.name
                if packet.packet_source_address_class is not None else None,
            )
            if msgname is not None:
                invalid_raw_values = self.coordinator.nasa_repo[msgname].get(
                    'invalid_raw_values', []
                )
                if msg.packet_payload.hex().lower() in invalid_raw_values:
                    self._logger.debug(
                        "Ignoring invalid raw value for %s: raw=%s",
                        msgname,
                        msg.packet_payload.hex(),
                    )
                    continue

                try:
                    msgvalue = await self.coordinator.determine_value(msg.packet_payload, msgname, msg.packet_message_type)
                except Exception:
                    if msgname in self.determine_value_store.keys():
                        self.determine_value_store[msgname] += 1
                        if self.determine_value_store[msgname] % 10 == 0:
                            self._logger.warning(f"Error determining value for message {msgname} has occurred {self.determine_value_store[msgname]} times")
                    else:
                        self.determine_value_store[msgname] = 1
                        self._logger.warning(f"Error determining value for message {msgname} with payload {msg.packet_payload}")
                        self._logger.warning(f"Packet details: {packet}")
                    continue

                self._logger.debug(f"Processing message {msgname} with value {msgvalue}")
                await self.protocol_message(msgname, msgvalue)
                self._log_software_versions_if_ready()

    def _log_software_versions_if_ready(self, force=False):
        if self._software_versions_logged and not force:
            return

        names = (
            "NASA_MICOM_CODE_OUTDOOR",
            "NASA_MICOM_CODE_INDOOR",
            "NASA_EEPROM_CODE",
        )
        versions = {
            name: self.value_store.get(name, {}).get("val")
            for name in names
        }
        missing = [name for name, value in versions.items() if value is None]
        response = {
            "outdoor_main_micom": versions["NASA_MICOM_CODE_OUTDOOR"],
            "indoor_main_micom": versions["NASA_MICOM_CODE_INDOOR"],
            "outdoor_eeprom": versions["NASA_EEPROM_CODE"],
            "missing": missing,
        }
        if missing:
            if force:
                self._logger.warning(
                    "Cannot log software versions yet; values not received: %s",
                    ", ".join(missing),
                )
            return response

        self._logger.info(
            "Software versions detected: Outdoor Main MICOM=%s; Indoor Main MICOM=%s; Outdoor EEPROM=%s",
            versions["NASA_MICOM_CODE_OUTDOOR"],
            versions["NASA_MICOM_CODE_INDOOR"],
            versions["NASA_EEPROM_CODE"],
        )
        self._software_versions_logged = True
        return response

    async def protocol_message(self, msgname, msgvalue):
        await self._run_serialized(self._protocol_message, msgname, msgvalue)

    async def _run_serialized(self, operation, *args):
        current_task = asyncio.current_task()
        if self._protocol_owner is current_task:
            await operation(*args)
            return

        async with self._protocol_lock:
            self._protocol_owner = current_task
            try:
                await operation(*args)
            finally:
                self._protocol_owner = None

    async def _protocol_message(self, msgname, msgvalue):
        if not self.coordinator.should_process_nasa_name(msgname):
            self.coordinator.confirm_write(msgname, msgvalue)
            self.coordinator.confirm_read(msgname)
            return

        dt = datetime.now()

        if self.last_dt is not None:
            if datetime.fromisoformat(self.last_dt).date() < dt.date():
                if self.coordinator.extended_logging:
                    self._logger.info(f"New day detected. Resetting daily counters.")
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
                self._logger.error(f"Error handling operation status for {msgname}: {e}")
                traceback.print_exc()

        # kalukuliere Minuten/Wattstunden in DHW/HEAT Mode
        if msgname in DELTA_SOURCES:
            self._logger.debug(f"Handling mode delta for {msgname} with value {msgvalue}")
            try:
                await self._handle_mode_delta(msgname, msgvalue, dt)
            except Exception as e:
                self._logger.error(f"Error handling mode delta for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für DHW Power Modus
        if msgname == 'NASA_DHW_VALVE':
            try:
                self._update_mode(msgvalue, dt)
            except Exception as e:
                self._logger.error(f"Error updating mode for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für Heat Output
        if msgname in ['LVAR_IN_GENERATED_POWER_LAST_MINUTE']:
            try:
                await self._handle_heat_output(msgname, msgvalue, dt)
            except Exception as e:
                self._logger.error(f"Error handling heat output for {msgname}: {e}")
                traceback.print_exc()

        # Spezielle Handhabung für COP Berechnung
        if msgname == "NASA_OUTDOOR_OPERATION_STATUS" or any(msgname in keys for keys in COP_MAP.values()):
            try:
                await self._handle_cop(msgname, msgvalue, dt)
            except Exception as e:
                self._logger.error(f"Error handling COP for {msgname}: {e}")
                traceback.print_exc()

        # SollVL - Wenn Mode AUTO dann gleich sensor.samsung_ehssentinel_intempwaterlawf, wenn HEAT dann sensor.samsung_ehssentinel_indoorsettempwaterout bei Zone 1 und sensor.samsung_ehssentinel_intempwateroutlettargetzone2f bei Zone 2
        if msgname in ('NASA_INDOOR_OPMODE', 'VAR_IN_TEMP_WATER_LAW_F', 'NASA_INDOOR_SETTEMP_WATEROUT', 'VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 'NASA_POWER_ZONE2', 'NASA_POWER'):
            try:
                await self._handle_sollvl(msgname, msgvalue, dt)
            except Exception as e:
                self._logger.error(f"Error handling SollVL for {msgname}: {e}")
                traceback.print_exc()

    
        ## Endgültige Verarbeitung: Normalisieren, im Coordinator aktualisieren und HA-Status aktualisieren
        
        # Normalisiere den Wert, falls erforderlich
        value = self._normalize_value(msgvalue)
        payload = {"value": value, "nasa_name": msgname}

        # Füge nasa_last_seen hinzu, wenn force_refresh aktiviert ist
        if self.coordinator.force_refresh or (msgname.startswith("NASA_EHSSENTINEL_") or 
                                              msgname in ('LVAR_IN_MINUTES_ACTIVE', 'NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM', 'LVAR_IN_TOTAL_GENERATED_POWER', 'NASA_DHW_VALVE')):
            payload["nasa_last_seen"] = datetime.fromisoformat(dt).isoformat(timespec="seconds")

        # Aktualisiere die Daten im Coordinator
        await self.coordinator.update_data_safe(
            {platform: {self._normalize_name(msgname): payload}}
        )

        # Bestätige die Lese- und Schreibvorgänge
        self.coordinator.confirm_write(msgname, msgvalue)
        self.coordinator.confirm_read(msgname)

        self.value_store[msgname] = {'val': msgvalue, 'dt': dt}
        if msgname in DELTA_SOURCES:
            self._prune_mode_history()
        self.last_dt = dt

    async def _handle_heat_output(self, msgname, msgvalue, dt):
        await self.protocol_message("NASA_EHSSENTINEL_HEAT_OUTPUT", msgvalue*1000)  # Umrechnung von kW auf W

    async def _handle_sollvl(self, msgname, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_INDOOR_OPMODE', 'NASA_POWER_ZONE2', 'NASA_POWER']):
            nasa_opmode = self.value_store['NASA_INDOOR_OPMODE']['val'] if msgname != 'NASA_INDOOR_OPMODE' else msgvalue
            nasa_power_zone1 = self.value_store['NASA_POWER']['val'] if msgname != 'NASA_POWER' else msgvalue
            nasa_power_zone2 = self.value_store['NASA_POWER_ZONE2']['val'] if msgname != 'NASA_POWER_ZONE2' else msgvalue

            def _stored_value(key, fallback):
                if msgname == key:
                    return msgvalue
                return self.value_store.get(key, {}).get('val', fallback)
            
            if str(nasa_opmode).upper() == 'AUTO':
                vl_set = _stored_value('VAR_IN_TEMP_WATER_LAW_F', 0)
            elif str(nasa_opmode).upper() in ['HEAT', 'COOL']:
                if nasa_power_zone1 == 'ON':
                    vl_set = _stored_value('NASA_INDOOR_SETTEMP_WATEROUT', 0)
                elif nasa_power_zone2 == 'ON':
                    vl_set = _stored_value('VAR_IN_TEMP_WATER_OUTLET_TARGET_ZONE2_F', 0)
                else:
                    vl_set = None
            else: 
                vl_set = None

            if vl_set is not None:
                await self.protocol_message("NASA_EHSSENTINEL_CURRENT_TARGET_FLOW_TEMP", round(vl_set, 2))

    async def _handle_cop(self, msgname, msgvalue, dt):
        if msgname == "NASA_OUTDOOR_OPERATION_STATUS" or msgname in COP_MAP["NASA_EHSSENTINEL_COP"]:
            await self._handle_instantaneous_cop(msgname, msgvalue)
            return

        for cop_sensor, (gen_key, cons_key) in COP_MAP.items():
            if msgname not in (gen_key, cons_key):
                continue
            gen_val = self.value_store.get(gen_key, {}).get('val') if msgname != gen_key else msgvalue
            cons_val = self.value_store.get(cons_key, {}).get('val') if msgname != cons_key else msgvalue
            cop = None
            if gen_val is not None and cons_val is not None and cons_val >= 0:
                cop = self._calculate_cop(gen_val, cons_val)
            await self.protocol_message(cop_sensor, cop)

    async def async_reset_cop(self):
        await self.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", None)

    async def _handle_instantaneous_cop(self, msgname, msgvalue):
        active_statuses = ("OP_NORMAL", "OP_SAFETY", "OP_DEICE")
        if msgname == "NASA_OUTDOOR_OPERATION_STATUS":
            if msgvalue != self._cop_operation_status:
                self._cop_generated_power = None
                self._cop_consumed_power = None
            self._cop_operation_status = msgvalue
        elif self._cop_operation_status in active_statuses:
            if msgname == "LVAR_IN_GENERATED_POWER_LAST_MINUTE":
                self._cop_generated_power = msgvalue
            elif msgname == "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT":
                self._cop_consumed_power = msgvalue

        cop = None
        reason = "operation status is not active"
        if self._cop_operation_status == "OP_STOP":
            cop = 0
            reason = "operation stopped"
        elif (
            self._cop_operation_status in active_statuses
            and self._cop_generated_power is not None
            and self._cop_consumed_power is not None
            and self._cop_consumed_power > 0
        ):
            cop = self._calculate_cop(self._cop_generated_power, self._cop_consumed_power)
            reason = "both values are available and consumed power is positive"
        elif self._cop_operation_status in active_statuses:
            reason = "missing power value or non-positive consumed power"

        if self.coordinator.extended_logging:
            self._logger.info(
            "Instantaneous COP check: input=%s value=%s status=%s generated=%s "
            "(received_now=%s) consumed=%s (received_now=%s) cop=%s reason=%s",
            msgname,
            msgvalue,
            self._cop_operation_status,
            self._cop_generated_power,
            msgname == "LVAR_IN_GENERATED_POWER_LAST_MINUTE",
            self._cop_consumed_power,
            msgname == "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT",
            cop,
            reason,
        )
        await self.protocol_message("NASA_EHSSENTINEL_COP", cop)

    def _calculate_cop(self, heat_output, power_input):
        if power_input > 0:
            return round(heat_output / power_input, 3)
        return 0

    
    def _update_mode(self, msgvalue, dt):
        if all(k in self.value_store for k in ['NASA_DHW_VALVE', 'ENUM_IN_FSV_3011']):
            tmpval = 'ON' if msgvalue == 'TANK' else 'OFF'
            if self.dhw_power_store.get('val') != tmpval:
                self._mode_history.append((
                    datetime.fromisoformat(dt), self.dhw_power_store['val'] == 'ON', tmpval == 'ON',
                ))
                if self.coordinator.extended_logging:
                    self._logger.info(f"Updating DHW/HEAT mode to {msgvalue} based on DHW_POWER change from {self.dhw_power_store.get('val')} to {msgvalue}")
                self.dhw_power_store['val'] = tmpval
                self.dhw_power_store['dt'] = dt

    def _mode_durations(self, start, end):
        current_dhw = self.dhw_power_store['val'] == 'ON'
        if self.set_mode is not None or self.value_store['ENUM_IN_FSV_3011']['val'] == 'No':
            mode = self.set_mode == 'DHW' if self.set_mode is not None else current_dhw
            return {mode: (end - start).total_seconds(), not mode: 0}

        changes = self._mode_history or [(
            datetime.fromisoformat(self.dhw_power_store['dt']), not current_dhw, current_dhw,
        )]
        durations = {True: 0, False: 0}
        mode = changes[0][1]
        cursor = start
        for timestamp, previous_mode, next_mode in changes:
            if timestamp <= start:
                mode = next_mode
            elif timestamp <= end:
                durations[mode] += (timestamp - cursor).total_seconds()
                cursor = timestamp
                mode = next_mode
            else:
                break
        durations[mode] += (end - cursor).total_seconds()
        return durations

    def _prune_mode_history(self):
        timestamps = [self.value_store.get(key, {}).get('dt') for key in DELTA_SOURCES]
        if all(timestamps):
            earliest = min(datetime.fromisoformat(timestamp) for timestamp in timestamps)
            self._mode_history = [change for change in self._mode_history if change[0] > earliest]
    
    async def _initialize_mode_energy_totals(self, dt):
        skip_delta = self._mode_total_baselines_pending
        for targets in DELTA_SOURCES.values():
            for key in targets[:2]:
                stored = self.value_store.get(key, {})
                if stored.get('val') is not None:
                    continue
                sensor_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name(key), {})
                value = sensor_data.get('value')
                if value is None:
                    if not sensor_data.get('_total_is_new', False):
                        if key not in self._missing_mode_totals:
                            self._logger.warning(
                                "Total counter %s has no restored value; leaving it unavailable instead of resetting it",
                                key,
                            )
                        self._missing_mode_totals.add(key)
                        continue
                    value = 0
                    skip_delta.add(key)
                if key in self._missing_mode_totals:
                    skip_delta.add(key)
                    self._missing_mode_totals.discard(key)
                self.value_store[key] = {
                    'val': value,
                    'dt': stored.get('dt') or sensor_data.get('nasa_last_seen') or dt,
                }
                if sensor_data.get('_total_is_new', False) and sensor_data.get('value') is None:
                    await self.protocol_message(key, 0)
        for cop_sensor in ('NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE', 'NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE'):
            if any(self.value_store.get(key, {}).get('val') is None for key in COP_MAP[cop_sensor]):
                if cop_sensor not in self.value_store or self.value_store[cop_sensor]['val'] is not None:
                    await self.protocol_message(cop_sensor, None)

    async def _handle_mode_delta(self, msgname, msgvalue, dt):
        await self._initialize_mode_energy_totals(dt)
        skip_targets = self._mode_total_baselines_pending.intersection(DELTA_SOURCES[msgname][:2])
        self._mode_total_baselines_pending.difference_update(skip_targets)
        skip_total_delta = msgname in self._mode_energy_baselines_pending
        self._mode_energy_baselines_pending.discard(msgname)
        skip_daily_delta = msgname in self._daily_baselines_pending
        self._daily_baselines_pending.discard(msgname)

        # Initialisiere nur den betroffenen Key falls nötig
        if self.value_store.get(msgname, {}).get('val', None) is None:
            if self.coordinator.extended_logging:
                self._logger.info(f"Initializing value store for {msgname} as it was not set.")
            for k in [msgname] + list(DELTA_SOURCES[msgname]):  # Alle abhängigen Keys initialisieren
                if self.value_store.get(k, {}).get('val', None) is None:
                    sensor_data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._normalize_name(k), {})
                    if sensor_data.get('value', None) is not None:
                        tmpDt = sensor_data.get('nasa_last_seen') or dt
                        if datetime.fromisoformat(tmpDt).date() == datetime.fromisoformat(dt).date() or k not in DAILY_MESSAGES:  # Nur initialisieren, wenn der letzte Stand von heute ist oder es kein Tageswert ist
                            self.value_store[k] = {
                                'val': sensor_data.get('value', None),
                                'dt': tmpDt
                            }
                            if self.coordinator.extended_logging:
                                self._logger.info(f"Initialized value store for {k} with value {sensor_data.get('value', None)} and timestamp {tmpDt}")
                        else: 
                            if self.coordinator.extended_logging:                  
                                self._logger.info(f"Setting value for {k} to 0 because last seen date is not today.")
                            await self.protocol_message(k, 0) # Setze Tageswerte auf 0, wenn der letzte Stand von einem anderen Tag ist
                    else:
                        if self.coordinator.extended_logging:
                            self._logger.info(f"No value found for {k} during initialization of mode delta handling")

        # Prüfe, ob alle Keys initialisiert sind
        # if not all(k in self.value_store for k in DELTA_SOURCES):
        #     return

        old = self.value_store.get(msgname)
        if self.coordinator.extended_logging:
            self._logger.info(f"Handling mode delta for {msgname}. Old value: {old}, New value: {msgvalue}")
        if not old or old['val'] is None:
            return
        
        if 'val' not in self.dhw_power_store:
            return
        
        if 'ENUM_IN_FSV_3011' not in self.value_store:
            return

        old_dt = datetime.fromisoformat(old['dt'])
        new_dt = datetime.fromisoformat(dt)
        delta_time = (new_dt - old_dt).total_seconds()
        delta = round(msgvalue - old['val'], 2)
        if delta < 0 or delta_time <= 0:
            return
        total_delta = 0 if skip_total_delta else delta
        daily_delta = 0 if skip_daily_delta else delta
        day_start = new_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if old_dt < day_start:
            daily_delta = round(daily_delta * (new_dt - day_start).total_seconds() / delta_time, 2)

        target_dhw, target_heat, daily_dhw, daily_heat, daily = DELTA_SOURCES[msgname]
        try:
            dhw_val = self.value_store.get(target_dhw, {}).get('val')
            heat_val = self.value_store.get(target_heat, {}).get('val')
            daily_dhw_val = round(self.value_store.get(daily_dhw, {}).get('val', 0) or 0, 2)
            daily_heat_val = round(self.value_store.get(daily_heat, {}).get('val', 0) or 0, 2)
            daily_val = round(self.value_store.get(daily, {}).get('val', 0) or 0, 2)
        except Exception as e:
            self._logger.error(f"Error retrieving values for mode delta calculation: {e}")
            return

        try:    
            await self.protocol_message(daily, daily_val + daily_delta)
        except Exception as e:
            self._logger.error(f"Error updating daily total for {daily}: {e}")
            traceback.print_exc()

        durations = self._mode_durations(old_dt, new_dt)
        today_start = max(old_dt, day_start)
        daily_durations = self._mode_durations(today_start, new_dt)
        daily_seconds = (new_dt - today_start).total_seconds()
        total_dhw_delta = round(total_delta * durations[True] / delta_time, 2)
        total_heat_delta = round(total_delta - total_dhw_delta, 2)
        daily_dhw_delta = round(daily_delta * daily_durations[True] / daily_seconds, 2) if daily_seconds > 0 else 0
        daily_heat_delta = round(daily_delta - daily_dhw_delta, 2)
        if dhw_val is not None:
            await self.protocol_message(target_dhw, round(dhw_val + (0 if target_dhw in skip_targets else total_dhw_delta), 2))
        if heat_val is not None:
            await self.protocol_message(target_heat, round(heat_val + (0 if target_heat in skip_targets else total_heat_delta), 2))
        await self.protocol_message(daily_dhw, round(daily_dhw_val + daily_dhw_delta, 2))
        await self.protocol_message(daily_heat, round(daily_heat_val + daily_heat_delta, 2))
            

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
    
    def search_nasa_table(self, address, source_address_class=None):
        fallback = None
        for key, value in self.coordinator.nasa_repo.items():
            if value['address'].lower() == address:
                configured_source = value.get('source_address_class')
                if configured_source == source_address_class:
                    return key
                if configured_source is None:
                    fallback = key
        return fallback
    
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
        await self._run_serialized(self._development_tool, tool_name)

    async def _development_tool(self, tool_name):
        if tool_name == "set_mode_heat":
            self.set_mode = 'HEAT'
            self._logger.info(f"Set mode to {self.set_mode}")
        elif tool_name == "set_mode_dhw":
            self.set_mode = 'DHW'
            self._logger.info(f"Set mode to {self.set_mode}")
        elif tool_name == "disable_set_mode":
            self.set_mode = None
            self._logger.info(f"Disabled DHW/HEAT mode tracking")
        elif tool_name == "reset_daily_counters" or tool_name == "reset_all_counters":
            self._daily_baselines_pending.update(DELTA_SOURCES)
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
            self._logger.info("Reset daily counters for minutes active, consumed power and generated power in both modes")

            if tool_name == "reset_all_counters":
                self._mode_energy_baselines_pending.update(DELTA_SOURCES)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE", 0) 
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE", 0)
                await self.protocol_message("NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE", 0)
                self._logger.info("Reset total counters for minutes active, consumed power and generated power in both modes")
        else:
            self._logger.warning(f"Unknown development tool requested: {tool_name}")