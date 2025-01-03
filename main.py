import asyncio
from threading import Thread
from flask import Flask, request, jsonify
from bleak import BleakClient, BleakScanner

# Настройки BLE
SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_1 = "0000ae10-0000-1000-8000-00805f9b34fb"  # Для входов
CHARACTERISTIC_UUID_2 = "0000ae04-0000-1000-8000-00805f9b34fb"  # Для громкости

app = Flask(__name__)

# Асинхронный цикл событий
background_loop = asyncio.new_event_loop()


# Запуск цикла событий в отдельном потоке
def start_background_loop():
    asyncio.set_event_loop(background_loop)
    background_loop.run_forever()


thread = Thread(target=start_background_loop, daemon=True)
thread.start()


# Выполнение асинхронной задачи из синхронного контекста
def run_async_task(coro, *args):
    future = asyncio.run_coroutine_threadsafe(coro(*args), background_loop)
    return future.result()


# Подключение к BLE устройству
async def connect_ble(mac_address):
    client_ble = BleakClient(mac_address)
    await client_ble.connect()
    print(f"Connected to BLE device: {mac_address}")
    return client_ble


# Установка громкости
async def set_volume_async(mac_address, volume):
    if not (1 <= volume <= 31):  # Убедимся, что громкость в допустимом диапазоне
        raise ValueError("Volume must be between 1 and 31.")

    client_ble = await connect_ble(mac_address)
    try:
        data = bytearray([0x7e, 0x0f, 0x1d, volume, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        checksum = sum(data) & 0xFF  # Контрольная сумма с использованием & 0xFF
        data.append(checksum)
        print(f"Sending volume command: {' '.join(format(x, '02X') for x in data)}")  # Логирование команды
        await client_ble.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
        print(f"Volume set to {volume}")
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")


# Получение уровня громкости с использованием уведомлений (без глобальной переменной)
async def get_volume_async(mac_address):
    volume_level = None

    def notify_volume_callback(sender, data):
        nonlocal volume_level
        print(f"Notification received from {sender}: {data}")
        if len(data) > 5:
            volume_level = data[5]  # Пример, что громкость в 6-м байте данных
            print(f"Updated volume level: {volume_level}")

    client_ble = await connect_ble(mac_address)
    try:
        # Включаем уведомления для второй характеристики
        await client_ble.start_notify(CHARACTERISTIC_UUID_2, notify_volume_callback)
        print(f"Notifications enabled for volume")

        # Запрашиваем данные из первой характеристики, чтобы инициировать уведомления
        await client_ble.read_gatt_char(CHARACTERISTIC_UUID_1)
        print(f"Requesting data from the first characteristic to trigger volume notifications")

        # Подождем некоторое время, чтобы получить уведомление
        await asyncio.sleep(2)

        # Останавливаем уведомления
        await client_ble.stop_notify(CHARACTERISTIC_UUID_2)
        print(f"Notifications stopped")

        return volume_level
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")


# Получение выбранного входа
async def get_input_async(mac_address):
    client_ble = await connect_ble(mac_address)
    try:
        input_data = await client_ble.read_gatt_char(CHARACTERISTIC_UUID_1)
        print(f"Raw input data: {input_data}")

        if len(input_data) < 5:
            print(f"Error: Insufficient data length for input. Got: {len(input_data)}")
            return None

        input_type = input_data[4]  # Предполагаем, что выбранный вход находится в 5-м байте
        print(f"Current input type: {input_type}")

        return input_type
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")


# Переключение входов
async def handle_input_async(mac_address, input_code):
    client_ble = await connect_ble(mac_address)
    try:
        # Формируем команду для переключения входа
        data = bytearray([0x7e, 0x05, input_code, 0x00])
        checksum = sum(data) & 0xFF  # Контрольная сумма с использованием & 0xFF
        data.append(checksum)  # Добавляем контрольную сумму в конец
        print(f"Sending input switch command: {' '.join(format(x, '02X') for x in data)}")  # Логирование команды

        # Отправляем команду на устройство
        await client_ble.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
        print(f"Input switch command sent: {input_code} (hex: {hex(input_code)})")
    finally:
        await client_ble.disconnect()
        print("Disconnected from BLE device")


# Сканирование BLE устройств
async def scan_ble_devices():
    devices = await BleakScanner.discover()
    return [{"address": device.address, "name": device.name or "Unknown"} for device in devices]


# HTTP эндпоинты
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
        # Получаем громкость и вход
        volume = run_async_task(get_volume_async, mac_address)
        input_type = run_async_task(get_input_async, mac_address)

        # Преобразуем код входа в строковое значение
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

    # Проверка на правильность введенного типа входа
    if not mac_address:
        return jsonify({"error": "MAC address is required"}), 400
    if input_type not in ["aux", "bt", "sndcard", "usb"]:
        return jsonify({"error": "Input must be 'aux', 'bt', 'sndcard', or 'usb'"}), 400

    # Присвоение кода для разных типов входа
    input_codes = {
        "aux": 0x16,
        "bt": 0x14,
        "sndcard": 0x15,
        "usb": 0x04  # Для USB, используем 0x04
    }

    try:
        input_code = input_codes[input_type]
        run_async_task(handle_input_async, mac_address, input_code)
        return jsonify({"status": "success", "message": f"Input set to {input_type} for {mac_address}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Запуск Flask
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)