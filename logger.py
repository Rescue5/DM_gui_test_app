import serial
import time
import threading
import queue
import csv
import sys
import select
import os

# Настройки COM-порта (замените на ваш порт)
SERIAL_PORT = '/dev/cu.usbserial-A5069RR4'  # Замените на правильный COM-порт
BAUD_RATE = 115200
LOG_FILE = 'test_log.txt'
CSV_FILE = 'test_data.csv'

# Открываем COM-порт
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
except serial.SerialException as e:
    print(f"Ошибка при открытии COM-порта: {e}")
    exit(1)

# Флаги, очереди и блокировки
stop_event = threading.Event()
command_queue = queue.Queue()
lock = threading.Lock()  # Для синхронизации доступа к флагу


def parse_and_save_to_csv(data):
    """Парсинг строки и запись в CSV."""
    if data.startswith("Скорость:"):
        parts = data.split(":")
        try:
            speed = parts[1]
            moment = parts[3]
            thrust = parts[5]
            rpm = parts[7]

            with open(CSV_FILE, 'a', newline='') as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow([speed, moment, thrust, rpm])
        except IndexError:
            print("Ошибка парсинга данных:", data)


def log_data():
    """Функция для логирования данных из COM-порта в файл."""
    global stop_event
    with open(LOG_FILE, 'w') as file:
        with open(CSV_FILE, 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(["Скорость", "Момент", "Тяга", "Об/мин"])

        while not stop_event.is_set():
            if ser.in_waiting > 0:
                with lock:
                    data = ser.readline().decode('utf-8').strip()
                print(data)
                file.write(data + '\n')
                file.flush()

                if "Motor stopped" in data or "Test complete" in data:
                    print("Тест завершен или двигатель остановлен.")
                    stop_event.set()
                    command_queue.put("STOP")
                    break

                if data.startswith("Скорость:"):
                    parse_and_save_to_csv(data)
            time.sleep(0.1)


def send_command(command):
    """Отправка команды в Serial."""
    try:
        with lock:
            ser.write((command + '\n').encode('utf-8'))
            ser.flush()
        print(f"Отправлена команда: {command}")
    except Exception as e:
        print(f"Ошибка при отправке команды: {e}")


def process_commands():
    """Функция для обработки команд от пользователя."""
    while not stop_event.is_set():
        try:
            command = command_queue.get(timeout=1)
            if command == "STOP":
                send_command(command)
                time.sleep(10)
                stop_event.set()
                break
            elif command == "START":
                send_command(command)
        except queue.Empty:
            continue


def user_input_thread():
    """Функция для ввода команд в отдельном потоке."""
    global stop_event
    while not stop_event.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0]:
            user_input = input().strip()
            if user_input == "START":
                command_queue.put("START")
            else:
                print(
                    f"Неизвестная команда '{user_input}'. Отправляется команда STOP.")
                command_queue.put("STOP")
                stop_event.set()  # Завершаем программу при неизвестной команде


def main():
    log_thread = threading.Thread(target=log_data)
    command_thread = threading.Thread(target=process_commands)
    # Поток для пользовательского ввода
    input_thread = threading.Thread(target=user_input_thread)

    log_thread.start()
    command_thread.start()
    input_thread.start()

    log_thread.join()
    command_thread.join()

    # Если основной цикл завершен, завершаем ввод и закрываем программу
    stop_event.set()
    input_thread.join()

    if ser.is_open:
        ser.close()
    print("COM-порт закрыт.")
    sys.exit(0)  # Принудительно завершаем программу


if __name__ == "__main__":
    main()
