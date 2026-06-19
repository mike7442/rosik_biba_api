# server_with_full_gui_no_imports.py
import socket
import threading
import time
import struct # Для упаковки/распаковки байтов, если нужно будет
import tkinter as tk # Импортируем tkinter для UI
from tkinter import Scale, Label, Frame, BOTH, LEFT, RIGHT, TOP, BOTTOM, Entry # Добавим Entry для reserved, если понадобится # Импортируем нужные виджеты

# --- ВСТАВКА КОДА ИЗ demo_protocol_utils.py ---
ORDER = '>'  # Порядок байтов (Big-Endian)
PROTOCOL_START_HEADER = b'\xDE\xAD'  # Используем имя, подходящее для клиента/сервера
HEADER_VALUE = struct.unpack('>H', PROTOCOL_START_HEADER)[0]
MESSAGE_FORMAT = f'{ORDER}HffHHHHHHHHHHHHHBBBBBBBBBB' # Формат сообщения (46 байт)
MESSAGE_LENGTH = struct.calcsize(MESSAGE_FORMAT) # Вычисляем длину: 46 байт

# --- Конфигурация ---
SERVER_IP = '192.168.0.221'  # Принимать подключения на любом интерфейсе
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

# --- Функции для UI ---
# Удаляем дубликаты функций
def update_linear_speed(value):
    with SERVER_DATA_LOCK: # Блокируем доступ к SERVER_DATA
        SERVER_DATA['linear_speed'] = float(value)
        print(f"[UI Update] Linear Speed set to: {SERVER_DATA['linear_speed']}")

def update_angular_speed(value):
    with SERVER_DATA_LOCK: # Блокируем доступ к SERVER_DATA
        SERVER_DATA['angular_speed'] = float(value)
        print(f"[UI Update] Angular Speed set to: {SERVER_DATA['angular_speed']}")

def update_lift_height(value):
    with SERVER_DATA_LOCK:
        SERVER_DATA['lift_height'] = int(value)
        print(f"[UI Update] Lift Height set to: {SERVER_DATA['lift_height']}")

def update_servo_position(servo_index, value):
    with SERVER_DATA_LOCK:
        SERVER_DATA['servo_positions'][servo_index] = int(value)
        print(f"[UI Update] Servo {servo_index} position set to: {SERVER_DATA['servo_positions'][servo_index]}")

def create_gui():
    root = tk.Tk()
    root.title("Server Control Panel")

    # Используем Canvas и Scrollbar для прокрутки, если элементов много
    canvas = tk.Canvas(root)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    # --- Слайдеры для linear_speed и angular_speed (как раньше) ---
    linear_label = Label(scrollable_frame, text="Linear Speed")
    linear_label.grid(row=0, column=0, sticky='w', padx=(5, 5), pady=(5, 0))

    linear_slider = Scale(scrollable_frame, from_=-2.0, to=2.0, resolution=0.01,
                          orient='horizontal',
                          command=update_linear_speed)
    linear_slider.set(SERVER_DATA['linear_speed'])
    linear_slider.grid(row=1, column=0, columnspan=2, sticky='ew', padx=(5, 5), pady=(0, 10))
    # Настройка веса столбца для растягивания слайдера
    scrollable_frame.columnconfigure(0, weight=1)
    scrollable_frame.columnconfigure(1, weight=1)

    angular_label = Label(scrollable_frame, text="Angular Speed")
    angular_label.grid(row=2, column=0, sticky='w', padx=(5, 5), pady=(5, 0))

    angular_slider = Scale(scrollable_frame, from_=-1.0, to=1.0, resolution=0.01,
                           orient='horizontal',
                           command=update_angular_speed)
    angular_slider.set(SERVER_DATA['angular_speed'])
    angular_slider.grid(row=3, column=0, columnspan=2, sticky='ew', padx=(5, 5), pady=(0, 10))


    # --- Слайдер для lift_height ---
    lift_label = Label(scrollable_frame, text="Lift Height")
    lift_label.grid(row=4, column=0, sticky='w', padx=(5, 5), pady=(10, 0))

    lift_slider = Scale(scrollable_frame, from_=0, to=65535, resolution=1,
                        orient='horizontal',
                        command=update_lift_height)
    lift_slider.set(SERVER_DATA['lift_height'])
    lift_slider.grid(row=5, column=0, columnspan=2, sticky='ew', padx=(5, 5), pady=(0, 10))


    # --- Слайдеры для servo_positions ---
    servo_frame_start_row = 6
    servo_labels = []
    servo_sliders = []

    for i in range(len(SERVER_DATA['servo_positions'])):
        label = Label(scrollable_frame, text=f"Servo {i}")
        label.grid(row=servo_frame_start_row + i*2, column=0, sticky='w', padx=(5, 5), pady=(5, 0))
        servo_labels.append(label)

        slider = Scale(scrollable_frame, from_=500, to=2500, resolution=1,
                       orient='horizontal',
                       command=lambda val, idx=i: update_servo_position(idx, val)) # Захватываем индекс 'i'
        slider.set(SERVER_DATA['servo_positions'][i])
        slider.grid(row=servo_frame_start_row + i*2 + 1, column=0, columnspan=2, sticky='ew', padx=(5, 5), pady=(0, 5))
        servo_sliders.append(slider)

    # --- reserved пока без слайдеров, можно добавить Entry, если нужно ---
    reserved_label = Label(scrollable_frame, text="Reserved Bytes (Edit in Code)")
    reserved_label.grid(row=servo_frame_start_row + len(SERVER_DATA['servo_positions'])*2, column=0, sticky='w', padx=(5, 5), pady=(10, 0))

    # Pack canvas и scrollbar
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    root.mainloop()

# --- Точка входа ---
if __name__ == "__main__":
    # Запускаем сервер в фоновом потоке
    server_thread = threading.Thread(target=server_main, daemon=True)
    server_thread.start()
    print("Server started in background thread.")
    # Запускаем UI в основном потоке
    print("Starting UI...")
    create_gui()
    print("UI closed.")
