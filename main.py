import asyncio
from threading import Thread
from flask import Flask, request, jsonify
from bleak import BleakClient, BleakScanner

# BLE settings
SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_1 = "0000ae10-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_2 = "0000ae04-0000-1000-8000-00805f9b34fb"

# Device documentation: http://www.sinilink.com/ins/bluetooth/XY-AP100H/XY-AP100H-EN.pdf
# Device link: https://aliexpress.ru/item/1005005861406008.html

app = Flask(__name__)

# Asynchronous event loop
background_loop = asyncio.new_event_loop()

# Start the event loop in a separate thread
def start_background_loop():
    asyncio.set_event_loop(background_loop)
    background_loop.run_forever()

thread = Thread(target=start_background_loop, daemon=True)
thread.start()

# Run an asynchronous task from a synchronous context
def run_async_task(coro, *args):
    future = asyncio.run_coroutine_threadsafe(coro(*args), background_loop)
    return future.result()

# Connect to a BLE device
async def connect_ble(mac_address):
    client_ble = BleakClient(mac_address)
    await client_ble.connect()
    print(f"Connected to BLE device: {mac_address}")
    return client_ble

# Set volume
async def set_volume_async(mac_address, volume):
    if not (1 <= volume <= 31):  # Ensure volume is within the allowed range
        raise ValueError("Volume must be between 1 and 31.")

    client_ble = await connect_ble(mac_address)
    try:
        data = bytearray([0x7e, 0x0f, 0x1d, volume, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        checksum = sum(data) & 0xFF  # Checksum calculation using & 0xFF
        data.append(checksum)
        print(f"Sending volume command: {' '.join(format(x, '02X') for x in data)}")  # Log the command
        await client_ble.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
        print(f"Volume set to {volume}")
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")

# Get volume level using notifications (without a global variable)
async def get_volume_async(mac_address):
    volume_level = None

    def notify_volume_callback(sender, data):
        nonlocal volume_level
        print(f"Notification received from {sender}: {data}")
        if len(data) > 5:
            volume_level = data[5]  # Example: Volume level is in the 6th byte
            print(f"Updated volume level: {volume_level}")

    client_ble = await connect_ble(mac_address)
    try:
        # Enable notifications for the second characteristic
        await client_ble.start_notify(CHARACTERISTIC_UUID_2, notify_volume_callback)
        print("Notifications enabled for volume")

        # Request data from the first characteristic to trigger notifications
        await client_ble.read_gatt_char(CHARACTERISTIC_UUID_1)
        print("Requesting data from the first characteristic to trigger volume notifications")

        # Wait some time to receive a notification
        await asyncio.sleep(2)

        # Stop notifications
        await client_ble.stop_notify(CHARACTERISTIC_UUID_2)
        print("Notifications stopped")

        return volume_level
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")

# Get selected input
async def get_input_async(mac_address):
    client_ble = await connect_ble(mac_address)
    try:
        input_data = await client_ble.read_gatt_char(CHARACTERISTIC_UUID_1)
        print(f"Raw input data: {input_data}")

        if len(input_data) < 5:
            print(f"Error: Insufficient data length for input. Got: {len(input_data)}")
            return None

        input_type = input_data[4]  # Assume selected input is in the 5th byte
        print(f"Current input type: {input_type}")

        return input_type
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")

# Switch inputs
async def handle_input_async(mac_address, input_code):
    client_ble = await connect_ble(mac_address)
    try:
        # Form the command to switch input
        data = bytearray([0x7e, 0x05, input_code, 0x00])
        checksum = sum(data) & 0xFF  # Checksum calculation using & 0xFF
        data.append(checksum)  # Add the checksum to the end
        print(f"Sending input switch command: {' '.join(format(x, '02X') for x in data)}")  # Log the command

        # Send the command to the device
        await client_ble.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
        print(f"Input switch command sent: {input_code} (hex: {hex(input_code)})")
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")

# Scan BLE devices
async def scan_ble_devices():
    devices = await BleakScanner.discover()
    return [{"address": device.address, "name": device.name or "Unknown"} for device in devices]

# HTTP endpoints
@app.route('/', methods=['GET'])
def http_scan_ble_devices():
    try:
        devices = run_async_task(scan_ble_devices)
        return jsonify({"status": "success", "devices": devices})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/set_volume', methods=['GET'])
def http_set_volume():
    mac_address = request.args.get('mac', type=str)
    volume = request.args.get('volume', type=int)
    if not mac_address:
        return jsonify({"error": "MAC address is required"}), 400
    if volume is None:
        return jsonify({"error": "Volume must be provided"}), 400
    try:
        run_async_task(set_volume_async, mac_address, volume)
        return jsonify({"status": "success", "message": f"Volume set to {volume} for {mac_address}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/status', methods=['GET'])
def http_get_status():
    mac_address = request.args.get('mac', type=str)
    if not mac_address:
        return jsonify({"error": "MAC address is required"}), 400

    try:
        # Get volume and input
        volume = run_async_task(get_volume_async, mac_address)
        input_type = run_async_task(get_input_async, mac_address)

        # Convert input code to string value
        input_names = {
            0x16: "aux",
            0x14: "bt",
            0x17: "sndcard",
            0x04: "usb"
        }

        input_name = input_names.get(input_type, "Unknown")

        return jsonify({
            "status": "success",
            "mac_address": mac_address,
            "volume": volume,
            "input": input_name
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/set_input', methods=['GET'])
def http_set_input():
    mac_address = request.args.get('mac', type=str)
    input_type = request.args.get('input', type=str)

    # Validate input type
    if not mac_address:
        return jsonify({"error": "MAC address is required"}), 400
    if input_type not in ["aux", "bt", "sndcard", "usb"]:
        return jsonify({"error": "Input must be 'aux', 'bt', 'sndcard', or 'usb'"}), 400

    # Assign code for different input types
    input_codes = {
        "aux": 0x16,
        "bt": 0x14,
        "sndcard": 0x15,
        "usb": 0x04
    }

    try:
        input_code = input_codes[input_type]
        run_async_task(handle_input_async, mac_address, input_code)
        return jsonify({"status": "success", "message": f"Input set to {input_type} for {mac_address}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Start Flask
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)