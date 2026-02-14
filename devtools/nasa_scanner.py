import asyncio
import argparse
import logging
from tracemalloc import start
import pandas as pd
import os
from datetime import datetime
from custom_components.ehs_sentinel.nasa_packet import NASAPacket, AddressClassEnum, PacketType, DataType
from custom_components.ehs_sentinel.nasa_message import NASAMessage
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

_LOGGER = logging.getLogger(__name__)

OUTPUT_FILE = "scan_results.csv"
MAX_RETRIES = 2
TIMEOUT = 10

results = {}
pending_futures = {}
progress_total = 0
progress_done = 0
progress_start_time = 0
shutdown_requested = False

# python -m devtools.nasa_scanner --ip 172.19.2.240 --port 4196 --start 0x4000 --end 0x9000 --workers 80 --ignore-ff --ignore-zero --only-changed --no-empty-column
# python -m devtools.nasa_scanner --ip 172.19.2.240 --port 4196 --start 0x4000 --end 0x4050 --workers 5 --ignore-ff --only-changed
# python -m devtools.nasa_scanner --ip 172.19.2.240 --port 4196 --workers 5 --rescan-from-file --only-changed

# -------------------------------------------------
# MOST WANTED
# FSV 4054: 0x4090
# FSV 4062:
# FSV 4063:
# FSV 6011:
# FSV 6022:
# FSV 6031:
# FSV 6041:

# -------------------------------------------------
# Argumente
# -------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--ignore-ff", action="store_true")
    parser.add_argument("--ignore-zero", action="store_true")
    parser.add_argument("--only-changed", action="store_true")
    parser.add_argument("--no-empty-column", action="store_true")
    parser.add_argument("--rescan-from-file",action="store_true",help="Scan only addresses that already exist in the output file")
    return parser.parse_args()


# -------------------------------------------------
# MAIN
# -------------------------------------------------
async def main():
    global progress_total, progress_done, progress_start_time, shutdown_requested

    args = parse_args()

    start = int(args.start, 16) if args.start else 0x0000
    end = int(args.end, 16) if args.end else 0x9999

    progress_total = end - start + 1
    progress_done = 0
    progress_start_time = time.time()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    _LOGGER.info(f"Starting scan at {timestamp} with {args.workers} workers...")

    reader = writer = None
    workers = []
    read_task = None
    progress_task = None

    try:
        reader, writer = await asyncio.open_connection(args.ip, args.port)

        read_task = asyncio.create_task(tcp_reader(reader))

        queue = asyncio.Queue()

        addresses = []

        if args.rescan_from_file:
            if not os.path.exists(OUTPUT_FILE):
                _LOGGER.error("Output file does not exist. Cannot rescan.")
                return

            df_old = pd.read_csv(OUTPUT_FILE, dtype=str).fillna("")

            if "address" not in df_old.columns:
                _LOGGER.error("No 'address' column found in file.")
                return

            for addr_str in df_old["address"]:
                try:
                    addresses.append(int(addr_str, 16))
                except ValueError:
                    _LOGGER.warning(f"Invalid address format in file: {addr_str}")

            _LOGGER.info(f"Rescanning {len(addresses)} addresses from file.")
        else:
            addresses = list(range(start, end + 1))

        progress_total = len(addresses)

        for addr in addresses:
            await queue.put(addr)

        workers = [
            asyncio.create_task(worker(queue, writer, args, i))
            for i in range(args.workers)
        ]

        progress_task = asyncio.create_task(progress_monitor())

        await queue.join()

    except asyncio.CancelledError:
        pass

    except KeyboardInterrupt:
        shutdown_requested = True
        _LOGGER.warning("Scan aborted by user (CTRL+C)")

    finally:
        _LOGGER.info("Shutting down gracefully...")

        if progress_task:
            progress_task.cancel()

        for w in workers:
            w.cancel()

        if read_task:
            read_task.cancel()

        # kleinen Moment warten, damit letzte Pakete noch verarbeitet werden
        await asyncio.sleep(0.5)

        if writer:
            writer.close()
            await writer.wait_closed()

        _LOGGER.info("Writing results to file...")
        save_results(timestamp, args)
        _LOGGER.info("Shutdown complete.")



# -------------------------------------------------
# Worker
# -------------------------------------------------
async def worker(queue, writer, args, worker_id):
    _LOGGER.info(f"Worker {worker_id} started")
    while True:
        address = await queue.get()
        if shutdown_requested:
            queue.task_done()
            return
        _LOGGER.info(f"Worker {worker_id} processing address 0x{address:04X}")
        try:
            value = await read_with_retry(address, writer)
            if value is not None:
                if filter_value(value, args):
                    results[address] = value
        except Exception as e:
            _LOGGER.debug(f"Error {hex(address)}: {e}")
        finally:
            global progress_done
            progress_done += 1
            queue.task_done()

async def progress_monitor():
    while True:
        try:
            await asyncio.sleep(1)

            elapsed = time.time() - progress_start_time
            speed = progress_done / elapsed if elapsed > 0 else 0
            percent = (progress_done / progress_total) * 100 if progress_total else 0
            remaining = progress_total - progress_done
            eta = remaining / speed if speed > 0 else 0

            _LOGGER.info(
                f"[{progress_done}/{progress_total}] "
                f"{percent:.2f}% | "
                f"{speed:.1f} addr/s | "
                f"ETA: {eta:.1f}s",
                end="\r"
            )

        except asyncio.CancelledError:
            print()  # Zeilenumbruch am Ende
            break

# -------------------------------------------------
# Retry Logic
# -------------------------------------------------
async def read_with_retry(address, writer):
    for attempt in range(MAX_RETRIES):
        future = asyncio.get_event_loop().create_future()
        pending_futures[address] = future

        packet = build_read_packet(address)
        writer.write(packet.to_raw())
        await writer.drain()

        try:
            value = await asyncio.wait_for(future, timeout=TIMEOUT)
            return value
        except asyncio.TimeoutError:
            await asyncio.sleep(1 * (attempt + 1))
    
    _LOGGER.warning(f"Address 0x{address:04X} failed after {MAX_RETRIES} attempts")

    return None


# -------------------------------------------------
# Filter
# -------------------------------------------------
def filter_value(value: bytes, args):
    if args.ignore_ff and all(b == 0xFF for b in value):
        return False
    if args.ignore_zero and all(b == 0x00 for b in value):
        return False
    return True


# -------------------------------------------------
# TCP Reader
# -------------------------------------------------
async def tcp_reader(reader: asyncio.StreamReader):
    buffer = bytearray()

    START_SEQ = b"\x32\x00"
    END_BYTE = 0x34

    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break

            buffer.extend(chunk)

            while True:
                # --- Start suchen ---
                start_index = buffer.find(START_SEQ)
                if start_index == -1:
                    # nichts brauchbares im Buffer
                    buffer.clear()
                    break

                # Falls Müll vor Start steht -> abschneiden
                if start_index > 0:
                    del buffer[:start_index]

                # Mindestens 3 Bytes für Längenfeld?
                if len(buffer) < 3:
                    break

                # Paketgröße bestimmen
                packet_size = ((buffer[1] << 8) | buffer[2]) + 2

                # Komplettes Paket schon da?
                if len(buffer) < packet_size:
                    break

                packet = buffer[:packet_size]

                # Endbyte prüfen
                if packet[-1] != END_BYTE:
                    # ungültiges Paket → 1 Byte weiter
                    del buffer[0]
                    continue

                # Paket verarbeiten
                await process_complete_packet(packet)

                # Paket aus Buffer entfernen
                del buffer[:packet_size]

    except asyncio.CancelledError:
        pass
    except Exception as e:
        _LOGGER.error(f"tcp_reader error: {e}")

async def process_complete_packet(raw_packet: bytes):
    try:
        nasa_packet = NASAPacket()
        nasa_packet.parse(raw_packet)

        for msg in nasa_packet.packet_messages:
            addr = msg.packet_message

            if addr in pending_futures:
                pending_futures[addr].set_result(msg.packet_payload)
                del pending_futures[addr]

    except Exception as e:
        _LOGGER.error(f"Packet parse failed: {e}")

# -------------------------------------------------
# Save Logic mit Diff-System
# -------------------------------------------------
def save_results(timestamp, args):
    df_new = pd.DataFrame({
        "address": [f"0x{a:04X}" for a in results.keys()],
        timestamp: [f"0x{v.hex().upper()}" for v in results.values()]
    })

    if os.path.exists(OUTPUT_FILE):
        df_old = pd.read_csv(OUTPUT_FILE, dtype=str).fillna("")

        if args.only_changed:
            last_col = df_old.columns[-1]
            df_merge = pd.merge(df_old, df_new, on="address", how="outer")

            changed = df_merge[last_col].fillna("") != df_merge[timestamp].fillna("")
            if changed.any():
                _LOGGER.info("\nDetected changes:")
                for idx in df_merge[changed].index:
                    addr = df_merge.loc[idx, "address"]
                    old_val = df_merge.loc[idx, last_col] or "—"
                    new_val = df_merge.loc[idx, timestamp] or "—"
                    _LOGGER.info(f"CHANGE {addr}: {old_val} -> {new_val}")
            df_merge.loc[~changed, timestamp] = ""

            if args.no_empty_column and not changed.any():
                _LOGGER.info("No changes detected. No column added.")
                return

            df_merge.to_csv(OUTPUT_FILE, index=False)
        else:
            df_merge = pd.merge(df_old, df_new, on="address", how="outer")
            df_merge.to_csv(OUTPUT_FILE, index=False)
    else:
        df_new.to_csv(OUTPUT_FILE, index=False)

    _LOGGER.info("Results saved.")


# -------------------------------------------------
# Packet Builder
# -------------------------------------------------
def build_read_packet(address):
    packet = NASAPacket()
    packet.set_packet_source_address_class(AddressClassEnum.JIGTester)
    packet.set_packet_source_channel(255)
    packet.set_packet_source_address(0)
    packet.set_packet_dest_address_class(AddressClassEnum.BroadcastSetLayer)
    packet.set_packet_dest_channel(0)
    packet.set_packet_dest_address(32)
    packet.set_packet_information(True)
    packet.set_packet_version(2)
    packet.set_packet_retry_count(0)
    packet.set_packet_type(PacketType.Normal)
    packet.set_packet_data_type(DataType.Read)
    packet.set_packet_number(1)

    msg = NASAMessage()
    msg.set_packet_message(address)
    msg.set_packet_payload_raw(b"\x00\x00")

    packet.set_packet_messages([msg])
    return packet


# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
