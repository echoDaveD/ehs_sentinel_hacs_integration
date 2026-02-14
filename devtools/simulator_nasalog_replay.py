import asyncio
import re
from datetime import datetime
import argparse
import sys

HOST = "0.0.0.0"        # Server-IP
PORT = 5020              # Modbus-ähnlicher Port (502 ist Standard, aber oft belegt)

# Logzeile zu (timestamp, bytes) parsen
def parse_log_line(line):
    match = re.match(r"\[(\d{4}-\d{2}-\d{2}), (\d{2}:\d{2}:\d{2}\.\d{3})\] ([0-9A-Fa-f ]+)$", line.strip())
    if match:
        # Datum ignorieren, nur Zeit verwenden
        timestr = match.group(2)
        hexstr = match.group(3)
        # Zeit als Sekunden seit Mitternacht
        t = datetime.strptime(timestr, "%H:%M:%S.%f")
        seconds = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
        pkt = bytes(int(b, 16) for b in hexstr.split())
        return (seconds, pkt)
    return None

def load_packets(logfile, start_time=None, end_time=None):
    packets = []
    with open(logfile, "r", encoding="utf-8") as f:
        for line in f:
            parsed = parse_log_line(line)
            if parsed:
                pkt_time, pkt = parsed
                if start_time is not None and pkt_time < start_time:
                    continue
                if end_time is not None and pkt_time > end_time:
                    continue
                packets.append((pkt_time, pkt))
    return packets

async def replay_packets(writer, packets):
    while True:
        start = asyncio.get_event_loop().time()
        for i, (pkt_time, pkt) in enumerate(packets):
            now = asyncio.get_event_loop().time()
            if i == 0:
                base = pkt_time
                send_at = start
            else:
                send_at = start + (pkt_time - base)
            wait = send_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            writer.write(pkt)
            await writer.drain()
        # Nach dem letzten Paket: von vorne beginnen

async def handle_client(reader, writer, packets):
    addr = writer.get_extra_info('peername')
    print(f"Client connected: {addr}")
    try:
        await replay_packets(writer, packets)
    except Exception as e:
        print(f"Client error: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception as close_err:
            print(f"Error while closing connection: {close_err}")
        print(f"Client disconnected: {addr}")


def parse_time_to_seconds(timestr):
    # timestr: "HH:MM:SS" or "HH:MM:SS.sss"
    try:
        t = datetime.strptime(timestr, "%H:%M:%S.%f")
    except ValueError:
        t = datetime.strptime(timestr, "%H:%M:%S")
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6

async def main():
    parser = argparse.ArgumentParser(description="TCP Packet Replay Simulator")
    parser.add_argument("logfile", help="Pfad zur Logdatei")
    parser.add_argument("--start", help="Startzeit (HH:MM:SS[.sss])", default=None)
    parser.add_argument("--end", help="Endzeit (HH:MM:SS[.sss])", default=None)
    args = parser.parse_args()

    start_time = parse_time_to_seconds(args.start) if args.start else None
    end_time = parse_time_to_seconds(args.end) if args.end else None

    packets = load_packets(args.logfile, start_time, end_time)
    if not packets:
        print("No packets found in log file (or in selected time range)!")
        return
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, packets), HOST, PORT
    )
    print(f"Async simulator listening on {HOST}:{PORT}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        print("Starting TCP packet replay simulator...")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Simulator stopped.")
