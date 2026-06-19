# client_tcp_no_imports.py
import socket
import time
import struct
import threading

# --- ВСТАВКА КОДА ИЗ demo_protocol_utils.py ---
ORDER = '>'  # Порядок байтов (Big-Endian)
PROTOCOL_START_HEADER = b'\xDE\xAD'  # Используем имя, подходящее для клиента/сервера
HEADER_VALUE = struct.unpack('>H', PROTOCOL_START_HEADER)[0]
MESSAGE_FORMAT = f'{ORDER}HffHHHHHHHHHHHHHBBBBBBBBBB' # Формат сообщения (46 байт)
MESSAGE_LENGTH = struct.calcsize(MESSAGE_FORMAT) # Вычисляем длину: 46 байт

def unpack_message(data):
    """
    Распаковывает байты в структурированные данные по заданному формату протокола.
    Args:
        data (bytes): Байтовое сообщение для распаковки.

    Returns:
        dict or None: Словарь с распакованными данными или None в случае ошибки.
                      {'header': int, 'linear_speed': float, 'angular_speed': float,
                       'servo_positions': list[int], 'lift_height': int, 'reserved': list[int]}
    """
    if len(data) != MESSAGE_LENGTH:
        print(f"unpack_message error: Expected {MESSAGE_LENGTH} bytes, got {len(data)}")
        return None

    try:
        unpacked = struct.unpack(MESSAGE_FORMAT, data)
    except struct.error as e:
        print(f"unpack_message struct error: {e}")
        return None

    header = unpacked[0]
    if header != HEADER_VALUE:
        print(f"unpack_message error: Expected header 0x{HEADER_VALUE:04X}, got 0x{header:04X}")
        return None

    linear_speed = unpacked[1]
    angular_speed = unpacked[2]
    servo_positions = list(unpacked[3:15])
    lift_height = unpacked[15]
    reserved = list(unpacked[16:])

    return {
        'header': header,
        'linear_speed': linear_speed,
        'angular_speed': angular_speed,
        'servo_positions': servo_positions,
        'lift_height': lift_height,
        'reserved': reserved
    }
# --- КОНЕЦ ВСТАВКИ ---

# --- Конфигурация ---
SERVER_IP = '192.168.0.221' # Замените на IP вашего сервера
# SERVER_IP = '77.37.184.204'  # Принимать подключения на любом интерфейсе # <- Комментируем или удаляем дубль
SERVER_PORT = 31415
PACKET_SIZE_CLIENT_TO_SERVER = 10
# PACKET_SIZE_SERVER_TO_CLIENT теперь определяется MESSAGE_LENGTH из вставленного кода (46 байт)
# START_HEADER теперь определяется из вставленного кода (b'\xDE\xAD')
HEARTBEAT_TIMEOUT_S = 0.5 # Максимальное время между получением пакетов от сервера
SEND_INTERVAL_MS = 50
RECV_TIMEOUT_S = 0.04 # Для recv с таймаутом
# --- Логгирование ---
LOGGING_ENABLED = 0  # Установите 1 для включения отладочных сообщений, 0 для отключения
# --------------------

def find_header(sock, packet_size, header):
    """Поиск заголовка и чтение оставшихся байт."""
    buffer = b""
    header_len = len(header)
    # header_len = 2
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

# --- Глобальный словарь данных клиента и Lock ---
CLIENT_DATA_LOCK = threading.Lock() # Блокировка для CLIENT_RECEIVED_DATA
CLIENT_RECEIVED_DATA = {
    'header': 0,
    'linear_speed': 0.0,
    'angular_speed': 0.0,
    'servo_positions': [0] * 12,
    'lift_height': 0,
    'reserved': [0] * 10
}
# ---------------------------------------------------

# --- Функция для выполнения действий по управлению ---
def process_control_data():
    """Читает CLIENT_RECEIVED_DATA и выполняет действия (например, отправка по Serial)."""
    # Читаем данные ПОД БЛОКИРОВКОЙ
    with CLIENT_DATA_LOCK:
        # Создаем копию данных, чтобы минимизировать время блокировки
        local_data = CLIENT_RECEIVED_DATA.copy()
        # Получаем нужные значения
        linear_speed = local_data['linear_speed']
        angular_speed = local_data['angular_speed']
        servo_positions = local_data['servo_positions']
        lift_height = local_data['lift_height']

        # Пример действия: печать
        print(f'[CONTROL LOOP] Отправили линейную скорость по Serial: {linear_speed}')
        print(f'[CONTROL LOOP] Отправили угловую скорость по Serial: {angular_speed}')
        # Пример для серв: print(f'[CONTROL LOOP] Установили позиции серв: {servo_positions[:4]}...') # Печатаем первые 4
        # Пример для подъёмника: print(f'[CONTROL LOOP] Установили высоту подъёмника: {lift_height}')

        # Здесь должна быть ваша логика отправки по Serial
        # serial_port.write(...) # <- Ваш код для Serial
        # serial_port.flush()   # <- Ваш код для Serial

        # Также можно обновлять is_safe_to_move здесь, если нужно
        # return is_currently_safe # <- Если нужно передать статус в основной цикл

# --- Поток для выполнения действий по управлению ---
def control_loop_thread():
    """Поток, который вызывает process_control_data с определённой частотой."""
    # Пример: вызывать каждые 50 мс (20 раз в секунду)
    control_interval = SEND_INTERVAL_MS / 1000.0 # Преобразуем в секунды
    while True: # Поток будет работать до завершения программы (так как daemon=True)
        start_time = time.time()
        process_control_data()
        elapsed_time = time.time() - start_time
        sleep_time = control_interval - elapsed_time
        if sleep_time > 0:
            time.sleep(sleep_time)
        # Если elapsed_time > control_interval, логика управления немного отстает,
        # но основной цикл получения данных не блокируется этим потоком.
# -------------------------------------------------------

def client_main():
    client_socket = None
    last_received_packet_time = 0
    is_connected = False
    is_safe_to_move = False
    safety_timeout = HEARTBEAT_TIMEOUT_S
    # --- Печать данных ---
    print_counter = 0
    print_interval = 20 # Печатать каждые 20 итераций (примерно раз в 20 * 50мс = 1 секунду)
    # ---------------------

    while True:
        # Проверяем соединение
        if not is_connected:
            if LOGGING_ENABLED:
                print("Клиент пытается подключиться...")
            try:
                client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client_socket.connect((SERVER_IP, SERVER_PORT))
                client_socket.settimeout(RECV_TIMEOUT_S) # Устанавливаем таймаут на recv
                if LOGGING_ENABLED:
                    print("Клиент подключен к серверу.")
                is_connected = True
                # При успешном подключении сбрасываем время получения
                last_received_packet_time = time.time()

                # --- ЗАПУСК ДЕМОНИЧЕСКОГО ПОТОКА УПРАВЛЕНИЯ ---
                # Поток запускается сразу после подключения
                control_thread = threading.Thread(target=control_loop_thread, daemon=True)
                control_thread.start()
                print("Control loop thread started.")

            except Exception as e:
                print(f"[ERROR] Не удалось подключиться: {e}. Ждём 3 секунды перед повторной попыткой...")
                time.sleep(3) # Ждем 3 секунды перед новой попыткой
                continue # Переходим к следующей итерации цикла

        if is_connected:
            # Пытаемся получить 46-байтный пакет от сервера (упакованные данные)
            # find_header будет искать b'\xDE\xAD' и возвращать 46 байт
            try:
                packet = find_header(client_socket, MESSAGE_LENGTH, PROTOCOL_START_HEADER) # Используем длину и заголовок из вставленного кода
                if packet:
                    if LOGGING_ENABLED:
                        print(f"[DEBUG] Клиент получил 46 байт (упакованные данные).")
                    # --- Распаковка данных ---
                    unpacked_data = unpack_message(packet)
                    if unpacked_data is not None:
                        # --- Обновляем глобальный словарь ПОД БЛОКИРОВКОЙ ---
                        with CLIENT_DATA_LOCK:
                            CLIENT_RECEIVED_DATA.update(unpacked_data)
                        # --- Конец блокировки ---
                        if LOGGING_ENABLED:
                            print(f"[DEBUG] Клиент распаковал данные: {CLIENT_RECEIVED_DATA}")
                    else:
                        if LOGGING_ENABLED:
                            print("[ERROR] Не удалось распаковать полученные данные.")
                        # Можно рассмотреть разрыв соединения при серии ошибок распаковки
                        # is_connected = False
                        # is_safe_to_move = False
                        # client_socket.close()
                        # client_socket = None
                        # continue
                    last_received_packet_time = time.time() # Обновляем время получения
                else:
                    # find_header вернул None -> соединение закрыто
                    print("[INFO] Соединение с сервером потеряно (сервер закрыл соединение).")
                    is_connected = False
                    is_safe_to_move = False # Сразу ставим в False при потере связи
                    client_socket.close()
                    client_socket = None
                    continue # Переходим к попытке переподключения
            except socket.timeout:
                # recv вернул timeout - это нормально, просто не получили пакет вовремя
                pass # Не обновляем last_received_packet_time
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                print(f"[ERROR] Ошибка получения от сервера: {e}")
                is_connected = False
                is_safe_to_move = False
                client_socket.close()
                client_socket = None
                continue # Переходим к попытке переподключения

            # Обновляем переменную is_safe_to_move
            current_time = time.time()
            if current_time - last_received_packet_time <= safety_timeout:
                if not is_safe_to_move:
                    print("[SAFE] Движение разрешено.")
                is_safe_to_move = True
            else:
                if is_safe_to_move:
                    print("[UNSAFE] Движение запрещено - связь потеряна.")
                is_safe_to_move = False

            # --- Печать глобального словаря ---
            print_counter += 1
            if print_counter >= print_interval:
                if LOGGING_ENABLED:
                    print("--- CLIENT_RECEIVED_DATA ---")
                    # Читаем под блокировкой для печати
                    with CLIENT_DATA_LOCK:
                        for key, value in CLIENT_RECEIVED_DATA.items():
                             print(f"  {key}: {value}")
                    print("------------------------------")
                print_counter = 0 # Сброс счетчика
            # -----------------------------------

            # Отправляем 10-байтный пакет (мусорный)
            try:
                payload_data = b'\xCC' * (PACKET_SIZE_CLIENT_TO_SERVER - len(PROTOCOL_START_HEADER)) # Произвольные данные
                packet_to_send = PROTOCOL_START_HEADER + payload_data # Используем заголовок из вставленного кода
                client_socket.sendall(packet_to_send)
                if LOGGING_ENABLED:
                    print(f"[DEBUG] Клиент отправил 10 байт.")
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                print(f"[ERROR] Ошибка отправки пакета серверу: {e}")
                is_connected = False
                is_safe_to_move = False
                client_socket.close()
                client_socket = None
                continue # Переходим к попытке переподключения

            time.sleep(SEND_INTERVAL_MS / 1000.0) # Ждем 50 мс перед следующей итерацией

if __name__ == "__main__":
    client_main()

