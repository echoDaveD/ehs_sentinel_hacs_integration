import sys
import types
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIRECTORY = REPOSITORY_ROOT / "custom_components" / "ehs_sentinel"

# Load only the protocol package so importing it does not initialize Home Assistant.
for package_name, package_path in (
	("custom_components", REPOSITORY_ROOT / "custom_components"),
	("custom_components.ehs_sentinel", COMPONENT_DIRECTORY),
):
	package = types.ModuleType(package_name)
	package.__path__ = [str(package_path)]
	sys.modules[package_name] = package

from custom_components.ehs_sentinel.nasa_packet import NASAPacket


print("Parsing NASA packet...")
# This is request SerialNumber 1 = SerialNumber of ODU 
string_bytearray = ['0x32', '0x00', '0x1A', '0x80', '0xFF', '0x00', '0x10', '0x00', '0x00', '0xC0', '0x11', '0x07', '0x01', '0x06', '0x07', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x91', '0x1B', '0x34']

# This is request SerialNumber 2 = SerialNumber of IDU
#string_bytearray =  ['0x32', '0x00', '0x1A', '0x80', '0xFF', '0x00', '0x20', '0x00', '0x00', '0xC0', '0x11', '0xB0', '0x01', '0x06', '0x07', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0x00', '0xBD', '0xBC', '0x34']

print("Byte array to parse:", string_bytearray)
packet = NASAPacket()
packet.parse(bytearray((int(b, 16) for b in string_bytearray)))
print("Parsed NASA packet:")
print(packet)