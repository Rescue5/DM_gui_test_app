import serial
import serial.tools.list_ports
import time
import threading
import queue
from tkinter import messagebox
import csv
import tkinter as tk
from tkinter import ttk
from ttkthemes import ThemedTk
from tkinter import scrolledtext
from PIL import Image, ImageTk
import os
import datetime  # Для временных меток

# Флаги, очереди и блокировки
stop_event = threading.Event()
command_queue = queue.Queue()
lock = threading.Lock()  # Для синхронизации доступа к сериалу
test_running = threading.Event()

# Переменные для отображения текущих значений
# current_moment_var = None
# current_thrust_var = None
# current_rpm_var = None


count = 0
previous_rpm = []  # Для хранения значений RPM предыдущей скорости
current_rpm = []   # Для хранения значений RPM текущей скорости
current_speed = None  # Текущая скорость для сбора данных
previous_speed = None  # Предыдущая скорость для анализа
stand_name = None     # Название стенда


# Добавляем в раздел глобальных переменных
previous_avg_rpm = None  # Среднее RPM предыдущей скорости
current_avg_rpm = None  # Среднее RPM текущей скорости
rpm_count = 0  # Счетчик RPM для текущей скорости

# Добавляем переменные для прогресс-бара
test_target_speed = None  # Целевая скорость для текущего теста
progress_complete = False  # Флаг завершения прогресса

# Настройки
BAUD_RATE = 115200

ser = None  # Глобальная переменная для хранения объекта Serial
process_commands_thread = None  # Поток обработки команд
read_serial_thread = None       # Поток чтения данных

# Переменные для логирования
log_file = None
csv_file = None
log_file_lock = threading.Lock()  # Для синхронизации доступа к лог-файлам


def parse_and_save_to_csv(data):
    """Парсинг строки и запись в CSV + анализ оборотов."""
    global current_rpm, previous_rpm, current_speed, previous_speed
    global current_avg_rpm, previous_avg_rpm, rpm_count
    global test_target_speed, progress_complete
    # global current_moment_var, current_thrust_var, current_rpm_var

    if data.startswith("Speed set to:"):
        parts = data.split(":")
        try:
            speed = int(parts[1].strip())
            # Обновляем прогресс-бар
            update_progress_bar(speed)
        except (IndexError, ValueError) as e:
            log_to_console(f"Ошибка парсинга скорости: {data} | Ошибка: {e}")
            return

    elif data.startswith("Скорость:"):
        parts = data.split(":")
        try:
            speed = int(parts[1].strip())
            moment = None
            thrust = None
            rpm = None

            if stand_name == "пропеллер":
                if len(parts) >= 8:
                    moment = float(parts[3].strip())
                    thrust = float(parts[5].strip())
                    rpm = int(parts[7].strip())
                else:
                    log_to_console(
                        "Недостаточно данных для пропеллера: " + data)
                    return
            elif stand_name == "момент":
                if len(parts) >= 8:
                    moment = float(parts[3].strip())
                    thrust = float(parts[5].strip())
                    rpm = int(parts[7].strip())
                else:
                    log_to_console("Недостаточно данных для момента: " + data)
                    return
            else:
                log_to_console("Неизвестный тип стенда.")
                return

            # Обновляем отображаемые значения
            # if moment is not None:
            #     current_moment_var = tk.StringVar(value="Момент: --")
            # if thrust is not None:
            #     current_thrust_var = tk.StringVar(value="Тяга: --")
            # if rpm is not None:
            #     current_rpm_var = tk.StringVar(value="RPM: --")

            # Проверяем, существует ли файл CSV
            write_headers = False
            if test_running.is_set() and csv_file:
                if not os.path.exists(csv_file):
                    write_headers = True
                else:
                    # Если файл существует, проверяем его размер
                    if os.path.getsize(csv_file) == 0:
                        write_headers = True

                with log_file_lock:
                    with open(csv_file, 'a', newline='') as csvfile:
                        csv_writer = csv.writer(csvfile, delimiter=';')
                        if write_headers:
                            if stand_name == "пропеллер":
                                csv_writer.writerow(
                                    ["Speed", "Moment", "Thrust", "RPM"])
                            elif stand_name == "момент":
                                csv_writer.writerow(
                                    ["Speed", "Moment", "Thrust", "RPM"])
                        # Записываем данные
                        if stand_name == "пропеллер":
                            csv_writer.writerow([speed, moment, thrust, rpm])
                        elif stand_name == "момент":
                            csv_writer.writerow([speed, moment, thrust, rpm])

            # Обработка RPM для анализа
            if current_speed != speed:
                # Если переключение на новую скорость
                if current_avg_rpm is not None and previous_avg_rpm is not None:
                    # Сравниваем средние значения RPM двух предыдущих скоростей
                    analyze_rpm()

                # Обновляем скорости
                previous_speed = current_speed
                current_speed = speed

                # Сбрасываем сбор RPM для новой скорости
                current_rpm = []
                rpm_count = 0
                previous_avg_rpm = current_avg_rpm
                current_avg_rpm = None

            if current_avg_rpm is None:
                # Собираем только первые 5 RPM для текущей скорости
                if rpm_count < 5:
                    current_rpm.append(rpm)
                    rpm_count += 1
                    # log_to_console(f"Собрано RPM {rpm_count}/5 для скорости {speed}: {rpm}")
                    if rpm_count == 5:
                        current_avg_rpm = sum(current_rpm) / len(current_rpm)
                        log_to_console(
                            f"Среднее RPM для скорости {speed}: {current_avg_rpm:.2f}")
                        if previous_avg_rpm is not None:
                            analyze_rpm()
            # Если уже собрано 5 RPM, дальнейшие значения игнорируются для анализа
            # Но все еще записываются в CSV и лог

        except (IndexError, ValueError) as e:
            log_to_console(f"Ошибка парсинга данных: {data} | Ошибка: {e}")


def analyze_rpm():
    """Анализирует средние RPM для текущей и предыдущей скорости."""
    global previous_avg_rpm, current_avg_rpm, test_running, previous_speed, current_speed

    if previous_avg_rpm is None or current_avg_rpm is None:
        return  # Недостаточно данных для анализа

    # log_to_console(f"Сравнение RPM между скоростью {previous_speed} ({previous_avg_rpm:.2f}) "
    #               f"и скоростью {current_speed} ({current_avg_rpm:.2f})")

    if current_avg_rpm < previous_avg_rpm:
        log_to_console(
            "Среднее RPM на текущей скорости меньше, чем на предыдущей. Остановка теста.")
        command_queue.put("STOP")
        return

    # Вычисляем процент изменения между RPM
    rpm_change_percent = (
        (current_avg_rpm - previous_avg_rpm) / previous_avg_rpm) * 100

    # log_to_console(f"Изменение RPM: {rpm_change_percent:.2f}%")

    # Если изменение менее 4%, останавливаем тест
    if abs(rpm_change_percent) < 4:
        log_to_console("Слишком малый рост оборотов. Остановка теста.")
        command_queue.put("STOP")


def log_to_console(message):
    """Вывод сообщения в консольное окно и в stdout для отладки с временной меткой."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console_output.config(state=tk.NORMAL)
    console_output.insert(tk.END, f"[{timestamp}] {message}\n")
    console_output.yview(tk.END)
    console_output.config(state=tk.DISABLED)
    # Также выводим в стандартный вывод для отладки
    print(f"[{timestamp}] {message}")


def read_serial():
    """Постоянно читает данные из COM-порта и выводит их в консоль. Также логирует данные, если тест запущен."""
    global log_file, csv_file, stand_name

    while not stop_event.is_set():
        if ser is not None and ser.is_open:
            try:
                if ser.in_waiting > 0:
                    with lock:
                        try:
                            data = ser.readline().decode('utf-8').strip()
                        except UnicodeDecodeError:
                            log_to_console("Не удалось декодировать данные.")
                            continue

                    if data:
                        log_to_console(data)  # Выводим данные в консоль

                        # Записываем в лог-файл, если тест запущен
                        if test_running.is_set() and log_file and csv_file:
                            with log_file_lock:
                                try:
                                    with open(log_file, 'a') as lf:
                                        lf.write(data + '\n')
                                except Exception as e:
                                    log_to_console(
                                        f"Ошибка записи в лог-файл: {e}")

                        # Анализируем RPM, если начинается строка с "Скорость:"
                        if data.startswith("Скорость:") or data.startswith("Speed set to:"):
                            parse_and_save_to_csv(data)

                        # Проверяем, завершен ли тест
                        if "Motor stopped" in data or "Test complete" in data:
                            log_to_console(
                                "Тест завершен или двигатель остановлен.")
                            previous_rpm.clear()
                            current_rpm.clear()
                            current_speed = None
                            previous_speed = None
                            test_running.clear()  # Останавливаем только тест, программа продолжает работать
                            reset_progress_bar()

                        # Проверяем название стенда
                        if data.startswith("Наименование стенда:"):
                            try:
                                stand_name = data.split(":")[1].strip().lower()
                                log_to_console(
                                    f"Название стенда: {stand_name}")
                                if stand_name in ["пропеллер", "момент"]:
                                    instruction_label.config(text=f"Стенд: {stand_name.capitalize()}. Теперь можно "
                                                             f"запускать тест.")
                                    start_button.config(state=tk.NORMAL)
                                else:
                                    log_to_console("Неизвестный тип стенда.")
                            except IndexError:
                                log_to_console(
                                    "Не удалось извлечь название стенда.")
            except serial.SerialException as e:
                log_to_console(f"Ошибка чтения из COM-порта: {e}")
                time.sleep(1)
        else:
            time.sleep(1)  # Ждем подключения


def send_command(command):
    """Отправка команды в Serial."""
    try:
        with lock:
            ser.write((command + '\n').encode('utf-8'))
            ser.flush()
        log_to_console(f"Отправлена команда: {command}")
    except Exception as e:
        log_to_console(f"Ошибка при отправке команды: {e}")


def process_commands():
    """Функция для обработки команд от пользователя."""
    while not stop_event.is_set():
        try:
            command = command_queue.get(timeout=1)
            # Отладочное сообщение
            log_to_console(f"Получена команда из очереди: {command}")
            if command == "STOP":
                send_command(command)
                time.sleep(10)
                test_running.clear()  # Останавливаем только тест, программа продолжает работать
                log_to_console("Тест остановлен.")
                reset_progress_bar()
            elif command.startswith("START"):
                test_running.set()  # Устанавливаем флаг, что тест запущен
                send_command(command)
                log_to_console("Тест запущен.")
            elif command == "INFO":
                send_command(command)
                log_to_console("Команда INFO отправлена.")
            elif command.startswith("PULSE_THRESHOLD_") or command.startswith("MOMENT_TENZ_") or command.startswith(
                    "THRUST_TENZ_"):
                send_command(command)
                log_to_console(f"Команда {command} отправлена.")
        except queue.Empty:
            continue


def connect_to_arduino():
    """Подключение к Arduino."""
    global ser, process_commands_thread, read_serial_thread, log_file, csv_file

    if ser is None or not ser.is_open:
        com_port = com_port_combobox.get()
        try:
            ser = serial.Serial(com_port, BAUD_RATE, timeout=1)
            time.sleep(2)  # Ждем инициализации порта
            log_to_console("Подключение к Arduino успешно.")

            # Запускаем поток чтения данных, если он еще не запущен
            if read_serial_thread is None or not read_serial_thread.is_alive():
                read_serial_thread = threading.Thread(
                    target=read_serial, daemon=True)
                read_serial_thread.start()
                log_to_console("Поток чтения данных запущен.")

            # Запускаем поток обработки команд, если он еще не запущен
            if process_commands_thread is None or not process_commands_thread.is_alive():
                process_commands_thread = threading.Thread(
                    target=process_commands, daemon=True)
                process_commands_thread.start()
                log_to_console("Поток обработки команд запущен.")

            # Инструкция пользователю
            instruction_label.config(
                text="Подключено. Нажмите 'Информация о стенде' для настройки.")
            # Отключаем кнопку до получения информации
            start_button.config(state=tk.DISABLED)
        except serial.SerialException as e:
            log_to_console(f"Ошибка при открытии COM-порта: {e}")
            return


def start_test():
    """Запуск теста."""
    global ser, log_file, csv_file, stand_name, previous_rpm, current_rpm, current_speed, previous_speed, \
        previous_avg_rpm, current_avg_rpm, rpm_count, test_target_speed, progress_complete

    previous_rpm = []  # Для хранения значений RPM предыдущей скорости
    current_rpm = []  # Для хранения значений RPM текущей скорости
    current_speed = None  # Текущая скорость для сбора данных
    previous_speed = None  # Предыдущая скорость для анализа

    # Добавляем в раздел глобальных переменных
    previous_avg_rpm = None  # Среднее RPM предыдущей скорости
    current_avg_rpm = None  # Среднее RPM текущей скорости
    rpm_count = 0  # Счетчик RPM для текущей скорости

    # Получаем названия двигателя и пропеллера
    engine_name = engine_name_entry.get()
    propeller_name = propeller_name_entry.get()

    # Получаем значение процентов с ползунка
    percent = speed_percent_slider.get()

    if not engine_name or not propeller_name:
        log_to_console("Введите названия двигателя и пропеллера.")
        return

    # Составляем имена файлов для логов и CSV
    log_file = f"{engine_name}_{propeller_name}_log.txt"
    csv_file = f"{engine_name}_{propeller_name}_data.csv"

    # Проверяем, существуют ли уже файлы с таким именем
    if os.path.exists(log_file) or os.path.exists(csv_file):
        # Показываем предупреждение о перезаписи
        answer = messagebox.askyesno(
            "Файлы существуют",
            "Файл логов или CSV с таким именем уже существует.\nВы хотите перезаписать их?"
        )
        if not answer:
            log_to_console("Тест не запущен. Файлы уже существуют.")
            return
        else:
            try:
                if os.path.exists(log_file):
                    os.remove(log_file)
                    log_to_console(
                        f"Существующий лог-файл '{log_file}' удален.")
                if os.path.exists(csv_file):
                    os.remove(csv_file)
                    log_to_console(
                        f"Существующий CSV-файл '{csv_file}' удален.")
            except Exception as e:
                log_to_console(f"Ошибка при удалении существующих файлов: {e}")
                return

    # Убедимся, что COM-порт подключен
    if ser is None or not ser.is_open:
        log_to_console(
            "COM-порт не открыт. Подключитесь к Arduino перед запуском теста.")
        return

    # Очистка предыдущих данных RPM при запуске нового теста
    test_running.clear()       # Останавливаем предыдущий тест, если он был
    previous_rpm.clear()      # Очистка предыдущего RPM
    current_rpm.clear()       # Очистка текущего RPM
    previous_speed = None     # Очистка предыдущей скорости
    current_speed = None      # Очистка текущей скорости
    log_to_console("Предыдущие данные RPM и скорости очищены.")
    time.sleep(1)              # Даем время, чтобы старые данные остановились

    # Устанавливаем целевую скорость
    test_target_speed = 1000 + (percent * 10)  # Например, 40% -> 1400
    log_to_console(f"Целевая скорость установлена на {test_target_speed} RPM.")

    # Сброс прогресс-бара и флага завершения
    reset_progress_bar()

    test_running.set()         # Устанавливаем флаг теста

    # Составляем команду START_xx
    command = f"START_{percent}"
    command_queue.put(command)
    log_to_console("Запуск теста...")


def stop_test():
    """Остановка теста."""
    if ser is not None and ser.is_open:
        command_queue.put("STOP")
        log_to_console("Остановка теста...")
        reset_progress_bar()
    else:
        log_to_console("COM-порт не открыт.")


def start_freeze():
    """Отправка команды START_FREEZE."""
    if ser is not None and ser.is_open:
        command_queue.put("START_FREEZE")
        log_to_console("Отправлена команда START_FREEZE")
    else:
        log_to_console("COM-порт не открыт.")


def stop_freeze():
    """Отправка команды STOP_FREEZE."""
    if ser is not None and ser.is_open:
        command_queue.put("STOP_FREEZE")
        log_to_console("Отправлена команда STOP_FREEZE")
    else:
        log_to_console("COM-порт не открыт.")


def emergency_stop(event):
    """Экстренная остановка по нажатию клавиши."""
    log_to_console("Экстренная остановка: нажата клавиша 'Esc'.")
    stop_test()


def close_application():
    """Закрытие приложения."""
    log_to_console("Закрытие приложения...")
    stop_event.set()
    if ser is not None and ser.is_open:
        ser.close()
        log_to_console("COM-порт закрыт.")
    root.destroy()


def update_progress_bar(speed):
    """Вычисляет и обновляет прогресс-бар на основе текущей скорости."""
    global test_target_speed, progress_complete

    if not test_running.is_set() or test_target_speed is None or progress_complete:
        return  # Тест не запущен или прогресс уже завершен

    # Определяем минимальную скорость (1000 RPM)
    min_speed = 1000

    # Вычисляем прогресс
    progress = ((speed - min_speed) / (test_target_speed - min_speed)) * 100

    # Ограничиваем прогресс до 100%
    if progress >= 100:
        progress = 100
        progress_complete = True

    # Обновляем прогресс-бар в главном потоке
    root.after(0, lambda: progress_var.set(progress))
    root.after(0, lambda: progress_label.config(
        text=f"Прогресс: {int(progress)}%"))

    # log_to_console(f"Текущая скорость: {speed} RPM. Прогресс: {int(progress)}%")

    if progress_complete:
        log_to_console("Прогресс достиг 100%.")
        # Дополнительные действия при достижении 100%, если необходимо


def reset_progress_bar():
    """Сбрасывает прогресс-бар до нуля."""
    global progress_complete
    progress_complete = False
    root.after(0, lambda: progress_var.set(0))
    root.after(0, lambda: progress_label.config(text="Прогресс: 0%"))

# Функции для отправки настроек


def send_pulse_threshold():
    """Отправка команды для настройки PULSE_THRESHOLD."""
    value = pulse_threshold_entry.get().strip()
    if value.isdigit() and int(value) > 0:
        command = f"PULSE_THRESHOLD_{value}"
        command_queue.put(command)
    else:
        messagebox.showerror(
            "Ошибка ввода", "Введите корректное положительное целое число для PULSE_THRESHOLD.")


def send_moment_tenz():
    """Отправка команды для настройки MOMENT_TENZ."""
    try:
        value = float(moment_tenz_entry.get().strip())
        if value > 0:
            command = f"MOMENT_TENZ_{value}"
            command_queue.put(command)
        else:
            raise ValueError
    except ValueError:
        messagebox.showerror(
            "Ошибка ввода", "Введите корректное положительное число для MOMENT_TENZ.")


def send_thrust_tenz():
    """Отправка команды для настройки THRUST_TENZ."""
    try:
        value = float(thrust_tenz_entry.get().strip())
        if value > 0:
            command = f"THRUST_TENZ_{value}"
            command_queue.put(command)
        else:
            raise ValueError
    except ValueError:
        messagebox.showerror(
            "Ошибка ввода", "Введите корректное положительное число для THRUST_TENZ.")


# Основное окно
root = ThemedTk()
root.get_themes()  # Получаем доступные темы
root.set_theme("arc")  # Устанавливаем желаемую тему

root.title("Тестирование")

# Создаем основную рамку для размещения элементов
main_frame = tk.Frame(root, padx=10, pady=10)
main_frame.pack(expand=True, fill=tk.BOTH)

# Поля для ввода названия двигателя и пропеллера
input_frame = tk.Frame(main_frame)
input_frame.grid(row=0, column=0, columnspan=2, pady=(0, 10), sticky='ew')

engine_name_label = tk.Label(input_frame, text="Название двигателя:")
engine_name_label.grid(row=0, column=0, padx=10, pady=5, sticky='w')
engine_name_entry = tk.Entry(input_frame)
engine_name_entry.grid(row=0, column=1, padx=10, pady=5, sticky='ew')

propeller_name_label = tk.Label(input_frame, text="Название пропеллера:")
propeller_name_label.grid(row=1, column=0, padx=10, pady=5, sticky='w')
propeller_name_entry = tk.Entry(input_frame)
propeller_name_entry.grid(row=1, column=1, padx=10, pady=5, sticky='ew')

# Загрузка и настройка изображения
try:
    image = Image.open("dron_motors.png")  # Загрузите изображение
    # Уменьшите размер до 250x100 пикселей
    image = image.resize((250, 100), Image.Resampling.LANCZOS)
    logo_photo = ImageTk.PhotoImage(image)
    # Добавление лого компании справа от полей ввода
    logo_label = tk.Label(main_frame, image=logo_photo)
    logo_label.grid(row=0, column=2, rowspan=2, padx=10, pady=5, sticky='n')
except FileNotFoundError:
    log_to_console("Изображение 'dron_motors.png' не найдено.")

input_frame.columnconfigure(1, weight=1)  # Позволяет полям ввода растягиваться

# Добавление ползунка для выбора процентов скорости
speed_percent_label = tk.Label(main_frame, text="Процент разгона:")
speed_percent_label.grid(row=1, column=0, padx=10, pady=5, sticky='w')

# Ползунок для выбора значений от 10 до 100 с шагом 10
speed_percent_slider = tk.Scale(main_frame, from_=10, to=100,
                                orient=tk.HORIZONTAL, length=300, resolution=10, tickinterval=10)
speed_percent_slider.grid(row=1, column=1, padx=10, pady=5, sticky='ew')

# Добавление прогресс-бара и метки
progress_frame = tk.Frame(main_frame)
progress_frame.grid(row=2, column=0, columnspan=2, pady=(10, 10), sticky='ew')

progress_label = tk.Label(progress_frame, text="Прогресс: 0%")
progress_label.pack(anchor='w', padx=10)

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(
    progress_frame, variable=progress_var, maximum=100)
progress_bar.pack(fill='x', padx=10, pady=5)

# Добавляем новый фрейм для отображения момента, тяги и RPM (Новый код)
# info_frame = tk.LabelFrame(main_frame, text="Текущие значения")
# info_frame.grid(row=2, column=2, padx=0, pady=0, sticky='n')
#
# moment_label = tk.Label(info_frame, textvariable=current_moment_var)
# moment_label.pack(anchor='w', padx=10, pady=5)
#
# thrust_label = tk.Label(info_frame, textvariable=current_thrust_var)
# thrust_label.pack(anchor='w', padx=10, pady=5)
#
# rpm_label = tk.Label(info_frame, textvariable=current_rpm_var)
# rpm_label.pack(anchor='w', padx=10, pady=5)


# Настройки COM-портов
com_frame = tk.Frame(main_frame)
com_frame.grid(row=3, column=0, pady=10, sticky='w')

com_port_label = tk.Label(com_frame, text="Выберите COM-порт:")
com_port_label.grid(row=0, column=0, padx=10, pady=5, sticky='w')

com_ports = [port.device for port in serial.tools.list_ports.comports()]
com_port_combobox = ttk.Combobox(com_frame, values=com_ports)
com_port_combobox.set("Выберите COM-порт")
com_port_combobox.grid(row=0, column=1, padx=10, pady=5, sticky='ew')

com_frame.columnconfigure(1, weight=1)  # Позволяет комбобоксу растягиваться

# Новый фрейм для кнопок "Подключение к стенду" и "Информация о стенде"
connect_info_frame = tk.Frame(main_frame)
# Размещаем справа от com_frame
connect_info_frame.grid(row=3, column=1, padx=10, pady=10, sticky='e')

# Кнопка "Подключение к стенду"
connect_button = tk.Button(
    connect_info_frame, text="Подключение к стенду", command=connect_to_arduino)
connect_button.pack(side=tk.LEFT, padx=5, pady=5)

# Кнопка "Информация о стенде"
info_button = tk.Button(connect_info_frame, text="Информация о стенде",
                        command=lambda: command_queue.put("INFO"))
info_button.pack(side=tk.LEFT, padx=5, pady=5)

# Новый фрейм для настроек (PULSE_THRESHOLD, MOMENT_TENZ, THRUST_TENZ)
settings_frame = tk.LabelFrame(main_frame, text="Настройки")
settings_frame.grid(row=4, column=0, columnspan=3,
                    pady=10, sticky='ew', padx=10)

# PULSE_THRESHOLD
pulse_threshold_label = tk.Label(
    settings_frame, text="Колличество пульсов\nна 10 оборотов\n(70 по умлочанию)")
pulse_threshold_label.grid(row=0, column=0, padx=10, pady=5, sticky='e')
pulse_threshold_entry = tk.Entry(settings_frame)
pulse_threshold_entry.grid(row=0, column=1, padx=10, pady=5, sticky='ew')
pulse_threshold_button = tk.Button(
    settings_frame, text="Отправить", command=send_pulse_threshold)
pulse_threshold_button.grid(row=0, column=2, padx=10, pady=5)

# MOMENT_TENZ
moment_tenz_label = tk.Label(
    settings_frame, text="Коэффициент момента\n(1 по умолчанию)")
moment_tenz_label.grid(row=1, column=0, padx=10, pady=5, sticky='e')
moment_tenz_entry = tk.Entry(settings_frame)
moment_tenz_entry.grid(row=1, column=1, padx=10, pady=5, sticky='ew')
moment_tenz_button = tk.Button(
    settings_frame, text="Отправить", command=send_moment_tenz)
moment_tenz_button.grid(row=1, column=2, padx=10, pady=5)

# THRUST_TENZ
thrust_tenz_label = tk.Label(
    settings_frame, text="Коэффициент тяги\n(1 по умолчанию)")
thrust_tenz_label.grid(row=2, column=0, padx=10, pady=5, sticky='e')
thrust_tenz_entry = tk.Entry(settings_frame)
thrust_tenz_entry.grid(row=2, column=1, padx=10, pady=5, sticky='ew')
thrust_tenz_button = tk.Button(
    settings_frame, text="Отправить", command=send_thrust_tenz)
thrust_tenz_button.grid(row=2, column=2, padx=10, pady=5)

# Позволяет полю ввода растягиваться
settings_frame.columnconfigure(1, weight=1)

# Остальные кнопки управления остаются в button_frame
button_frame = tk.Frame(main_frame)
button_frame.grid(row=5, column=0, columnspan=3,
                  pady=(10, 0), sticky='ew', padx=10)

start_button = tk.Button(button_frame, text="Запустить тест",
                         command=start_test, state=tk.DISABLED)
start_button.grid(row=0, column=0, padx=10, pady=5)

stop_button = tk.Button(
    button_frame, text="Остановить тест", command=stop_test)
stop_button.grid(row=0, column=1, padx=10, pady=5)

start_freeze_button = tk.Button(
    button_frame, text="Начать охлаждение", command=start_freeze)
start_freeze_button.grid(row=1, column=0, padx=10, pady=5)

stop_freeze_button = tk.Button(
    button_frame, text="Остановить охлаждение", command=stop_test)
stop_freeze_button.grid(row=1, column=1, padx=10, pady=5)

# Инструкция пользователю
instruction_label = tk.Label(
    button_frame, text="Подключитесь к стенду и нажмите 'Информация о стенде', перед запуском теста.")
instruction_label.grid(row=2, column=0, columnspan=3,
                       padx=10, pady=5, sticky='w')

# Консольное окно
console_output = scrolledtext.ScrolledText(
    main_frame, wrap=tk.WORD, height=15, width=60, state=tk.DISABLED)
console_output.grid(row=6, column=0, columnspan=3,
                    padx=10, pady=10, sticky='nsew')

main_frame.columnconfigure(2, weight=1)  # Позволяет кнопкам растягиваться
# Позволяет консольному окну растягиваться
main_frame.rowconfigure(6, weight=1)

# Закрытие приложения
root.protocol("WM_DELETE_WINDOW", close_application)

# Привязка клавиши 'Esc' к экстренной остановке
root.bind('<Escape>', emergency_stop)

root.mainloop()
