# server_with_full_gui_no_imports.py
import socket
import threading
import time
import struct # Для упаковки/распаковки байтов, если нужно будет
import serial


# --- ВСТАВКА КОДА ИЗ demo_protocol_utils.py ---
ORDER = '>'  # Порядок байтов (Big-Endian)
PROTOCOL_START_HEADER = b'\xDE\xAD'  # Используем имя, подходящее для клиента/сервера
HEADER_VALUE = struct.unpack('>H', PROTOCOL_START_HEADER)[0]
MESSAGE_FORMAT = f'{ORDER}HffHHHHHHHHHHHHHBBBBBBBBBB' # Формат сообщения (46 байт)
MESSAGE_LENGTH = struct.calcsize(MESSAGE_FORMAT) # Вычисляем длину: 46 байт

# --- Конфигурация ---
SERVER_IP = '192.168.5.221'  # Принимать подключения на любом интерфейсе
SERVER_PORT = 31415
PACKET_SIZE_CLIENT_TO_SERVER = 10
# PACKET_SIZE_SERVER_TO_CLIENT теперь определяется MESSAGE_LENGTH из вставленного кода (46 байт)
# START_HEADER теперь определяется из вставленного кода (b'\xDE\xAD')
HEARTBEAT_TIMEOUT_S = 0.50
SEND_INTERVAL_MS = 50
RECV_TIMEOUT_S = 0.04 # Для неблокирующего получения пакета от клиента в основном цикле
# --- Логгирование ---
LOGGING_ENABLED = 0  # Установите 1 для включения отладочных сообщений, 0 для отключения
# --------------------

IN_PORT = "COM5"
DEFAULT_BAUD = 115200


def pack_message(linear_speed, angular_speed, servo_positions, lift_height, reserved_bytes=None):
    """
    Упаковывает данные в байты по заданному формату протокола.
    Args:
        linear_speed (float): Линейная скорость.
        angular_speed (float): Угловая скорость.
        servo_positions (list[int]): Список из 12 значений позиций серв (0-65535).
        lift_height (int): Высота подъёмника (0-65535).
        reserved_bytes (list[int], optional): Список из 10 значений резервных байт (0-255).
                                              По умолчанию [0] * 10.

    Returns:
        bytes: Байтовое представление сообщения.
               Возвращает None, если входные данные некорректны.
    """
    if len(servo_positions) != 12:
        print(f"pack_message error: Expected 12 servo positions, got {len(servo_positions)}")
        return None
    # Проверка диапазона для серв и подъёмника (unsigned short)
    for i, pos in enumerate(servo_positions):
        if not 0 <= pos <= 65535:
            print(f"pack_message error: Servo position {i} out of range (0-65535): {pos}")
            return None
    if not 0 <= lift_height <= 65535:
        print(f"pack_message error: Lift height out of range (0-65535): {lift_height}")
        return None

    if reserved_bytes is None:
        reserved_bytes = [0] * 10
    if len(reserved_bytes) != 10:
        print(f"pack_message error: Expected 10 reserved bytes, got {len(reserved_bytes)}")
        return None
    # Проверка диапазона для резервных байт (unsigned char)
    for i, byte_val in enumerate(reserved_bytes):
        if not 0 <= byte_val <= 255:
            print(f"pack_message error: Reserved byte {i} out of range (0-255): {byte_val}")
            return None

    try:
        # Упаковываем всё за один вызов struct.pack
        packed_data = struct.pack(
            MESSAGE_FORMAT,
            HEADER_VALUE,           # 2 байта
            linear_speed,           # 4 байта (float)
            angular_speed,          # 4 байта (float)
            *servo_positions,       # 12 * 2 байта (unsigned short)
            lift_height,             # 2 байта (unsigned short)
            *reserved_bytes         # 10 * 1 байт (unsigned char)
        )
        return packed_data
    except struct.error as e:
        print(f"pack_message struct error: {e}")
        return None


def find_header(sock, packet_size, header):
    """Поиск заголовка и чтение оставшихся байт."""
    buffer = b""
    header_len = len(header)
    while True:
        chunk = sock.recv(header_len - len(buffer))
        if not chunk:
            return None # Соединение закрыто
        buffer += chunk
        if len(buffer) >= header_len:
            if buffer[:header_len] == header:
                remaining_bytes_needed = packet_size - header_len
                if remaining_bytes_needed > 0:
                    remaining_chunk = sock.recv(remaining_bytes_needed)
                    if len(remaining_chunk) < remaining_bytes_needed:
                        return None # Неполный пакет или соединение закрыто
                    full_packet = buffer + remaining_chunk
                else:
                    full_packet = buffer
                return full_packet
            else:
                # Сдвигаем буфер на 1 байт, если заголовок не найден
                buffer = buffer[1:]

# --- Глобальный словарь данных сервера и Lock ---
SERVER_DATA_LOCK = threading.Lock() # Добавляем блокировку
SERVER_DATA = {
    'linear_speed': 0.0, # Начальное значение
    'angular_speed': 0.0,
    'servo_positions': [1000] * 12, # 12 серво, начальное значение 1000
    'lift_height': 32000, # Начальное значение
    'reserved': [0] * 10 # Зарезервировано
}
# --------------------------------------------------

def server_main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((SERVER_IP, SERVER_PORT))
    server_socket.listen(1) # Ожидаем максимум 1 соединение
    print(f"Сервер слушает на {SERVER_IP}:{SERVER_PORT}")

    client_socket = None
    last_received_packet_time = 0
    is_client_active = False
    heartbeat_timeout = HEARTBEAT_TIMEOUT_S

    def send_packets_loop():
        nonlocal client_socket, is_client_active
        while True:
            if is_client_active and client_socket:
                try:
                    # --- Читаем данные ПОД БЛОКИРОВКОЙ ---
                    with SERVER_DATA_LOCK:
                        packed_data = pack_message(
                            SERVER_DATA['linear_speed'],
                            SERVER_DATA['angular_speed'],
                            SERVER_DATA['servo_positions'],
                            SERVER_DATA['lift_height'],
                            SERVER_DATA['reserved']
                        )
                    # --- Конец блокировки ---
                    if packed_data is None:
                         if LOGGING_ENABLED:
                             print("[ERROR] Не удалось упаковать данные сервера.")
                         is_client_active = False
                         client_socket.close()
                         client_socket = None
                         break

                    #  packed_data содержит 46 байт, начинающихся с b'\xDE\xAD'
                    # Отправляем эти 46 байт напрямую, так как find_header на клиенте будет искать b'\xDE\xAD'
                    client_socket.sendall(packed_data)
                    if LOGGING_ENABLED:
                        print(f"[DEBUG] Сервер отправил 46 байт (упакованные данные).")
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    if LOGGING_ENABLED:
                        print(f"[ERROR] Ошибка отправки пакета клиенту: {e}")
                    is_client_active = False
                    client_socket.close()
                    client_socket = None
                    break
            elif not is_client_active:
                   # Если клиент неактивен, ждем новое подключение
                 # Основной цикл handle_client_loop будет ждать accept
                 time.sleep(0.1) # Небольшая задержка, чтобы не грузить CPU
                 continue

            time.sleep(SEND_INTERVAL_MS / 1000.0)

    def handle_client_loop():
        nonlocal client_socket, last_received_packet_time, is_client_active,  server_socket
        send_thread = None

        while True:
            if not is_client_active:
                print("Сервер ожидает подключение клиента...")
                try:
                    client_socket, addr = server_socket.accept()
                    print(f"Подключен клиент: {addr}")
                    client_socket.settimeout(RECV_TIMEOUT_S) # Устанавливаем таймаут на recv
                    last_received_packet_time = time.time()
                    is_client_active   = True
                    # Запускаем поток отправки пакетов только после подключения
                    if send_thread is None or not send_thread.is_alive():
                         send_thread = threading.Thread(target=send_packets_loop, daemon=True)
                         send_thread.start()
                except socket.timeout:
                     # accept не использует timeout, но на всякий случай
                     continue
                except Exception as e:
                     print(f"[ERROR] Ошибка при подключении клиента: {e}")
                     continue
            else:
                # Клиент активен, проверяем таймаут
                current_time = time.time()
                if current_time - last_received_packet_time  > heartbeat_timeout:
                    print("[INFO] Таймаут клиента! Отключаю соединение.")
                    is_client_active = False
                    if client_socket:
                        client_socket.close()
                        client_socket = None
                    # Цикл вернётся к ожиданию accept
                    continue

                # Пытаемся получить пакет от клиента (10 байт, старый формат)
                try:
                    packet = find_header(client_socket, PACKET_SIZE_CLIENT_TO_SERVER, PROTOCOL_START_HEADER) # Используем заголовок из вставленного кода
                    if packet:
                        if LOGGING_ENABLED:
                             print(f"[DEBUG] Сервер получил 10 байт: {packet[:10]}")
                        last_received_packet_time = time.time() # Обновляем время получения
                    else:
                        # find_header вернул None -> соединение закрыто
                        print("[INFO] Клиент отключился (соединение закрыто).")
                        is_client_active = False
                        if client_socket:
                            client_socket.close()
                            client_socket = None
                         # Цикл вернётся к ожиданию accept
                        continue
                except socket.timeout:
                    # recv вернул timeout - это нормально, просто не  получили пакет вовремя
                    pass # Не обновляем last_received_packet_time
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    if LOGGING_ENABLED:
                        print(f"[ERROR] Ошибка получения от клиента: {e}")
                    is_client_active = False
                    if client_socket:
                        client_socket.close()
                        client_socket = None
                    # Цикл вернётся к ожиданию accept
                    continue

                time.sleep(0.01) # Небольшая задержка, чтобы не грузить CPU в цикле ожидания пакета

    try:
        handle_client_loop()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        if client_socket:
            client_socket.close()
        server_socket.close()


class ServoController:
    PACKET_HEADER = b'?$'
    # PACKET_SET_POSES = 0x10
    PACKET_GET_POSES = 0x11
    PACKET_SEND_POSES = 0x12
    # PACKET_SET_LIFT = 0x10
    PACKET_GET_BUTTONS = 0x20
    # PACKET_SEND_BUTTONS = 0x21
    # PACKET_HUMAN_MODE = ord('H')

    def __init__(self):
        self.serial = None
        self.error = ""
        self.last_error = ""
        self._state = 'WAIT_HEADER'
        self._header_pos = 0
        self._packet_type = 0
        self._packet_len = 0
        self._packet_data = bytearray()
        self._lock = threading.Lock()

    def connect(self, port: str, timeout: float = 0.05) -> bool:
        try:
            self.serial = serial.Serial(port=port, baudrate=DEFAULT_BAUD, timeout=timeout)
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            time.sleep(0.1)
            self.serial.write(b'PACKET_MODE\n')
            time.sleep(0.1)
            self.serial.reset_input_buffer()
            self.last_error = ""
            return True
        except serial.SerialException as e:
            self.error = str(e)
            self.serial = None
            return False

    def is_connected(self) -> bool:
        return self.serial is not None and self.serial.is_open

    def _write_packet(self, packet_type: int, data: bytes = bytes()) -> bool:
        if not self.is_connected():
            return False
        with self._lock:
            try:
                packet = self.PACKET_HEADER
                packet += struct.pack("<B", packet_type)
                packet += struct.pack("<H", len(data))
                packet += data
                self.serial.write(packet)
                return True
            except serial.SerialException as e:
                self.last_error = str(e)
                return False

    def _read_packet(self, timeout: float = 0.01):
        if not self.is_connected():
            return None
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                b = self.serial.read(1)
                if not b:
                    continue
                byte = b[0]
                if self._state == 'WAIT_HEADER':
                    if byte == self.PACKET_HEADER[0]:
                        self._header_pos = 1
                        self._state = 'WAIT_HEADER2'
                    else:
                        self._header_pos = 0
                elif self._state == 'WAIT_HEADER2':
                    if byte == self.PACKET_HEADER[1]:
                        self._state = 'WAIT_TYPE'
                    else:
                        self._state = 'WAIT_HEADER'
                        self._header_pos = 0
                elif self._state == 'WAIT_TYPE':
                    self._packet_type = byte
                    self._state = 'WAIT_LEN_LOW'
                elif self._state == 'WAIT_LEN_LOW':
                    self._packet_len = byte
                    self._state = 'WAIT_LEN_HIGH'
                elif self._state == 'WAIT_LEN_HIGH':
                    self._packet_len |= (byte << 8)
                    if 0 < self._packet_len < 512:
                        self._packet_data = bytearray()
                        self._state = 'WAIT_DATA'
                    else:
                        self._state = 'WAIT_HEADER'
                        self._header_pos = 0
                elif self._state == 'WAIT_DATA':
                    self._packet_data.append(byte)
                    if len(self._packet_data) == self._packet_len:
                        result = (self._packet_type, bytes(self._packet_data))
                        self._state = 'WAIT_HEADER'
                        self._header_pos = 0
                        return result
            except serial.SerialException as e:
                self.last_error = str(e)
                return None
        return None

    def read_pos_batch(self, servo_ids: list[int]) -> list[int] | None:
        data = bytes(servo_ids)
        if not self._write_packet(self.PACKET_GET_POSES, data):
            return None
        packet = self._read_packet()
        while packet is None:
            packet = self._read_packet()
        if packet and packet[0] == self.PACKET_SEND_POSES:
            data = packet[1]
            if len(data) >= len(servo_ids) * 2:
                positions = []
                for i in range(len(servo_ids)):
                    pos = struct.unpack("<H", data[i*2:(i+1)*2])[0]
                    positions.append(pos)
                return positions
        return None

    def write_pos_batch(self, ids: list[int], poses: list[int], speed: int, accel: int):
        if len(poses) != len(ids):
            return
        
        data = struct.pack("<HH", speed, accel)
        for id, pos in zip(ids, poses):
            data += struct.pack("<BH", id, pos)
        self._write_packet(self.PACKET_SET_POSES, data)
    
    def read_buttons(self) -> list[bool]:
        self._write_packet(self.PACKET_GET_BUTTONS, b'd')

        packet = self._read_packet()
        while packet is None:
            # print("no packet")
            packet = self._read_packet()

        if packet[0] == self.PACKET_SEND_BUTTONS:
            data = packet[1]
            if len(data) != 6:
                    return None
            return struct.unpack("<BBBBBB", data)  


def data_updating():
    servos_in = ServoController()
    print("Connecting to Arduino...")
    if not servos_in.connect(IN_PORT):
        print(f"Failed: {servos_in.error}")
        return
    print("Connected!")

    servo_ids = list(range(1, 13))

    print("Start cycle")

    height = 500
    last_time_update = time.time()

    servo_ids = list(range(1, 13))

    while True:        
        time_gap = time.time() - last_time_update
        if time_gap < 0.1:
            time.sleep(0.005)

        else:
            last_time_update += time_gap
            
            positions = servos_in.read_pos_batch(servo_ids)
            if positions:
                buttons = servos_in.read_buttons()
                if buttons[0]:
                    height += 50
                elif buttons[1]:
                    height -= 50
                
                with SERVER_DATA_LOCK:
                    for i in range(12):
                        SERVER_DATA['servo_positions'][i] = positions[i]
                    SERVER_DATA['lift_height'] = height
                    SERVER_DATA['angular_speed'] = buttons[2]*0.3-buttons[4]*0.3
                    SERVER_DATA['linear_speed'] = buttons[3]*0.3-buttons[5]*0.3      
                    
                
# --- Точка входа ---
if __name__ == "__main__":
    # Запускаем сервер в фоновом потоке
    server_thread = threading.Thread(target=server_main, daemon=True)
    server_thread.start()
    time.sleep(2)
    data_updating()
