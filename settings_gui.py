#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import json
from pathlib import Path
from build import CONFIG_FILE, APP_NAME, APP_NAME_LONG, APP_ICON_DIR, APP_ICON, \
                      LOG_FILE, LOG_LEVEL_DEF, LOG_FORMAT, LOG_NOISY_LIBRARES, LOG_BACKUP_COUNT, LOG_FILE_MAX_BYTES

import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import copy
from typing import Any

import requests
import wmi
from PIL import Image, ImageTk 
import winreg

############################ Утилиты для работы с файлами, путями ##################################
# Корневая папка приложения (Внешняя папка, где лежит .exe или главный .py скрипт)
# Проверяем, что это ИМЕННО PyInstaller и ресурсы ИМЕННО распакованы
IS_PYINSTALLER = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

# Корневая папка приложения (Внешняя папка, где лежит .exe или главный .py)
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Папка внутренних ресурсов (Временная папка сборки)
# Если это не PyInstaller, ресурсы берем из папки с кодом
DATA_DIR = sys._MEIPASS if IS_PYINSTALLER else APP_DIR


def get_app_path(*paths):
    """Путь РЯДОМ с исполняемым файлом (для логов, конфигов)."""
    return os.path.abspath(os.path.join(APP_DIR, *paths))


def get_data_path(*paths):
    """Путь ВНУТРИ ресурсов (для иконок, картинок, дефолтных файлов)."""
    return os.path.abspath(os.path.join(DATA_DIR, *paths))

def is_onedir_build():
    """Проверяет, что приложение скомпилировано именно в режиме --onedir"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        exe_dir = os.path.dirname(sys.executable)
        # Если папка с EXE совпадает с папкой ресурсов _MEIPASS — это onedir
        return os.path.normpath(sys._MEIPASS) == os.path.normpath(exe_dir)
    return False
############################## Функции для работы с сетью ###############################################
def get_current_interface_details():
    """
    Находит самый приоритетный активный интерфейс 
    и возвращает строго связанные между собой (IP, MAC, Gateway).
    """
    c = wmi.WMI()
    interfaces = c.Win32_NetworkAdapterConfiguration(IPEnabled=True)
    
    blacklist = ('virtual', 'vpn', 'tap', 'host-only', 'vbox', 'vmware', 'hyper-v', 'pseudo')
    valid_interfaces = []

    for interface in interfaces:
        desc = interface.Description.lower() if interface.Description else ""
        
        if any(word in desc for word in blacklist):
            log.debug(f"Пропущен виртуальный интерфейс: {interface.Description}")
            continue
            
        if interface.DefaultIPGateway:
            # Получаем метрику шлюза (GatewayCostMetric может быть списком, берем первый элемент)
            try:
                metric = int(interface.GatewayCostMetric[0]) if interface.GatewayCostMetric else 9999
            except (IndexError, ValueError, TypeError):
                metric = 9999
            
            valid_interfaces.append((metric, interface))
            
    if not valid_interfaces:
        log.debug("Не найдено подходящих физических интерфейсов с активным шлюзом.")
        return None, None, None  # Ничего не найдено

    # Сортируем: на самом верху окажется адаптер с наименьшей метрикой
    valid_interfaces.sort(key=lambda x: x[0])
    best_metric, best_interface = valid_interfaces[0]
    log.debug(f"Найден активный интерфейс: {best_interface.Description}")
    # 1. Извлекаем строго IPv4 адрес победившего адаптера
    target_ip = None
    for ip in best_interface.IPAddress:
        if ':' not in ip:
            target_ip = ip
            break
            
    # 2. Извлекаем MAC-адрес именно этого адаптера
    target_mac = best_interface.MACAddress.lower() if best_interface.MACAddress else None
    
    # 3. Извлекаем шлюз именно этого адаптера
    target_gateway = best_interface.DefaultIPGateway[0] if best_interface.DefaultIPGateway else None

    return target_ip, target_mac, target_gateway
################################### Kонфигурация логирования ########################################
class LoggerAdapter_ex(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        base_extra = self.extra if self.extra is not None else {}
        passed_extra = kwargs.get("extra", {})
        kwargs["extra"] = {**base_extra, **passed_extra}
        return msg, kwargs
def setup_log():
    logfile = get_app_path(LOG_FILE)

    root_logger = logging.getLogger()

    # Устанавливаем уровень логирования
    log_level_name = LOG_LEVEL_DEF
    config_file = Path(get_app_path(CONFIG_FILE))
    if config_file.exists():
        try:
            with config_file.open('r', encoding='utf-8') as f:
                log_level_name = json.load(f).get("log_level", LOG_LEVEL_DEF)
        except (json.JSONDecodeError, KeyError, OSError):
            pass 
    if log_level_name not in logging.getLevelNamesMapping():
        log_level_name = LOG_LEVEL_DEF
    root_logger.setLevel(getattr(logging, log_level_name))

    root_logger.handlers.clear()

    rotation_handler = RotatingFileHandler(
        logfile, 
        maxBytes=LOG_FILE_MAX_BYTES, 
        backupCount=LOG_BACKUP_COUNT, 
        encoding='utf-8'
    )

    formatter = logging.Formatter(LOG_FORMAT)
    rotation_handler.setFormatter(formatter)
    root_logger.addHandler(rotation_handler)

    # Глушим шумные библиотеки

    for lib_name in LOG_NOISY_LIBRARES:
        logging.getLogger(lib_name).setLevel(logging.INFO)

    raw_logger = logging.getLogger(APP_NAME)
    logger_adapter = LoggerAdapter_ex(raw_logger, {'space': ''})
    
    return logger_adapter

log = setup_log()

################################ Основной класс приложения и функции для работы с конфигурацией ################################

DEF_GEOMETRY = "1000x600+100+100"

class SettingsGUI:
    def __init__(self, root, config_file=CONFIG_FILE):


        
        self.root = root
        self.root.title(f"{APP_NAME_LONG} - Настройки")
        self.root.resizable(True, True)
        self.load_window_settings()

        # Устанавливаем иконку приложения
        try:
            icon = Image.open(get_data_path(APP_ICON_DIR, APP_ICON))
            self.app_icon = ImageTk.PhotoImage(icon.resize((64, 64)))
            self.root.iconphoto(True, self.app_icon)
        except Exception as e:
            log.error("Load app Icon error: %s", e)

        self.is_request_fetching = False
 
        # Флаги для отслеживания несохраненных изменений
        self.has_unsaved_changes = {}     

        self.config_file = config_file
        self.switches: list[dict[str, Any]] = []      
        self.config = self.load_config()
        
        # Синхронизируем статус автозагрузки в конфиге с реальным состоянием в реестре
        if self.config["autorun"]!= is_autorun_enabled():
            self.config["autorun"]= not self.config["autorun"]
      
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def load_config(self):
        """Загружает конфигурацию из файла"""
        # Пробуем получить адрес роутера и mac и IP сетевой карты из активного сетевого интерфейса
        self.curr_dev_ip, mac, gateway = get_current_interface_details()

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:                   
                    config_json=json.load(f)
                    self.switches = copy.deepcopy(config_json.get("switches", []))
                    self.mark_changed(value=False)
                    if config_json.get("device_mac") != mac and mac is not None:
                        if  messagebox.askyesno("Внимание", "MAC-адрес в конфигурации не совпадает с текущим MAC-адресом активного сетевого интерфейса. \n"\
                                f"Хотите обновить MAC-адрес в конфигурации на текущий {mac}? \n"):
                            config_json["device_mac"] = mac
                    return config_json
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось загрузить конфигурацию: {e}")
                self.sb.set_error("Ошибка загрузки конфигурации. Проверьте файл config.json")
                return {}
        else:
            # Если конфиг не найден, создаем пример с данными из первого активного интерфейса            
            self.switches=[
                    {
                        "id": "Policy0",
                        "name": "Policy0"
                    },
                    {
                        "id": "Policy1",
                        "name": "Policy1"
                    },
                    {
                        "id": "Policy2",
                        "name": "Policy2"
                    }
                ]

            self.mark_changed()            
            return {
                "router_url": f"http://{gateway}:81" if gateway else "http://192.168.1.1:81",
                "device_mac": mac if mac else "00:11:22:33:44:55",
                "last_switch": None,
                "ip_check_interval": 60,
                "autorun": False,
                "switches": [],
            }            
    
    def load_window_settings(self):
        self.REG_PATH = rf"Software\{APP_NAME}\Instances\{get_application_hash()}"       
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REG_PATH) as key:
                maximized, _ = winreg.QueryValueEx(key, "Maximized")
                geometry_str, _ = winreg.QueryValueEx(key, "Geometry")
                self.root.geometry(geometry_str)
                if maximized == 1:
                    self.root.state("zoomed")
        except FileNotFoundError:
            # Если настроек еще нет, задаем дефолтные размеры
            self.root.geometry(DEF_GEOMETRY)
 
    def save_config(self):
        """Сохраняет конфигурацию в файл"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            self.mark_changed(value=False)
            messagebox.showinfo("Успех", "Конфигурация сохранена успешно!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить конфигурацию: {e}")
    
    def setup_ui(self):
        """Создает интерфейс"""

        # Создаем тетради (tabs)
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Вкладка 1: Основные параметры
        self.setup_general_tab(notebook)

        self.setup_switches_tab(notebook) 

        # Кнопки внизу
        button_frame = ttk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        save_icon_image = Image.open(get_data_path(APP_ICON_DIR, "icon-save.png"))
        self.save_icon = ImageTk.PhotoImage(save_icon_image.resize((16, 16)))         
        self.save_btn = ttk.Button(button_frame, text="Сохранить", image=self.save_icon, compound=tk.LEFT, command=self.on_save,                          
                                   state=tk.NORMAL if any(self.has_unsaved_changes.values()) else tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT, padx=5)

        close_icon_image = Image.open(get_data_path(APP_ICON_DIR, "icon-close.png"))
        self.close_icon = ImageTk.PhotoImage(close_icon_image.resize((16, 16)))               
        close_btn = ttk.Button(button_frame, text="Закрыть", image=self.close_icon, compound=tk.LEFT, command=self.on_closing)
        close_btn.pack(side=tk.LEFT, padx=5)

        # Статус-бар для уведомлений
        # Общий контейнер для статус-бара (заменяет старое размещение на дне окна)
        statusbar_frame = tk.Frame(self.root)
        statusbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.notification_label = tk.Label(
            statusbar_frame, 
            text="Ожидание подключения...", 
            fg="grey", 
            font=("Arial", 10),
            anchor="w"
        )
        self.notification_label.pack(side=tk.LEFT, fill=tk.X, expand=True) 
        self.right_label = tk.Label(
            statusbar_frame, 
            text=f"IP: {self.curr_dev_ip if self.curr_dev_ip else 'N/A'}", 
            fg="grey", 
            font=("Arial", 10),
            anchor="e"
        )
        self.right_label.pack(side=tk.RIGHT, padx=5)
        self.sb = SB(self.notification_label)   

#================== GENERAL TAB ===============================    
    def setup_general_tab(self, notebook):
        """Создает вкладку с основными параметрами"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="Основные параметры")

        def validate_router_url(event=None):
            """Проверяет корректность адреса и порта роутера."""
            url = self.router_url_entry.get().strip()
            if not url:
                _mark_invalid(self.router_url_entry, True)
                return False

            # Регулярное выражение для проверки IPv4 с портом
            pattern = re.compile(
                r'^(https?://)?'  # протокол
                r'((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'  # первые три октета
                r'(25[0-5]|2[0-4]\d|[01]?\d\d?)'  # последний октет
                r'(:\d{1,5})?$',  # порт
                re.IGNORECASE
            )

            if pattern.match(url):
                _mark_invalid(self.router_url_entry, False)
                self.mark_changed("router_url", widget_or_var=self.router_url_entry)
                if self.has_unsaved_changes.get("router_url"):
                    self.update_data_from_router()
                return True
            else:
                _mark_invalid(self.router_url_entry, True)
                messagebox.showerror(
                    "Ошибка ввода",
                    "Неверный формат адреса роутера.\n"
                    "Ожидается: 192.168.1.1 или 192.168.1.1:8080\n"
                    "Каждая часть IP-адреса должна быть от 0 до 255.\n"
                    "Порт, если указан, должен быть от 1 до 65535.\n"
                    f"Введено: {url}"
                )
                self.router_url_entry.focus_set()
                return False

        # Router URL
        ttk.Label(frame, text="URL Роутера:", font=("Arial", 10)).grid(row=0, column=0, sticky=tk.W, padx=10, pady=10)
        self.router_url_entry = ttk.Entry(frame, width=30)
        self.router_url_entry.insert(0, self.config.get("router_url", "http://192.168.1.1:81"))
        self.router_url_entry.grid(row=0, column=1, sticky=tk.W, padx=10, pady=10)
        self.router_url_entry.bind("<FocusOut>", validate_router_url)


        def validate_mac_address(event=None):
            """
            Проверяет и исправляет MAC-адрес к формату xx:xx:xx:xx:xx:xx
            Возвращает True, если ввод корректен (или пуст, если поле необязательно),
            иначе False и выдаёт ошибку.
            """
            mac_raw = self.device_mac_entry.get().strip()
            
            if mac_raw == "":
                _mark_invalid(self.device_mac_entry, False)
                return False  
            
            # Удаляем любые разделители (двоеточия, дефисы, точки и т.п.) и переводим в верхний регистр
            mac_clean = re.sub(r'[-:.\s]', '', mac_raw).lower()
            
            # Проверяем: ровно 12 символов и все они HEX
            if len(mac_clean) == 12 and re.match(r'^[0-9a-f]{12}$', mac_clean):
                # Форматируем с двоеточиями каждые 2 символа
                formatted_mac = ':'.join(mac_clean[i:i+2] for i in range(0, 12, 2))
                # Если текущее значение в поле не совпадает с отформатированным — заменяем
                if mac_raw != formatted_mac:
                    self.device_mac_entry.delete(0, tk.END)
                    self.device_mac_entry.insert(0, formatted_mac)
                    self.mark_changed("device_mac", widget_or_var=self.device_mac_entry)
                _mark_invalid(self.device_mac_entry, False)
                self.mark_changed("device_mac", widget_or_var=self.device_mac_entry)
                return True
            else:
                # Некорректный MAC
                _mark_invalid(self.device_mac_entry, True)
                messagebox.showerror(
                    "Ошибка ввода",
                    "MAC-адрес должен содержать 12 шестнадцатеричных цифр (0-9, a-f).\n"
                    "Поддерживаются форматы: 001122334455, 00:11:22:33:44:55, 00-11-22-33-44-55.\n"
                    "Будет автоматически приведён к виду 00:11:22:33:44:55."
                )
                self.device_mac_entry.focus_set()
                return False

        def _mark_invalid(entry_widget, is_invalid):
            """Визуальная подсветка ошибки (меняет цвет текста)."""
            if is_invalid:
                entry_widget.config(foreground='red')
            else:
                entry_widget.config(foreground='black')


        # Device MAC
        ttk.Label(frame, text="MAC адрес устройства:", font=("Arial", 10)).grid(row=1, column=0, sticky=tk.W, padx=10, pady=10)
        self.device_mac_entry = ttk.Entry(frame, width=30)
        self.device_mac_entry.insert(0, self.config.get("device_mac", "00:11:22:33:44:55"))
        self.device_mac_entry.grid(row=1, column=1, sticky=tk.W, padx=10, pady=10)
        self.device_mac_entry.bind("<FocusOut>", validate_mac_address)
        
        frame.columnconfigure(1, weight=1)

        # Check interval
        ttk.Label(frame, text="Интервал опроса API роутера (сек):", font=("Arial", 10)).grid(row=2, column=0, sticky=tk.W, padx=10, pady=10)
        self.check_interval_var = tk.IntVar(value=self.config.get("check_interval", 2))
        check_interval_spin = ttk.Spinbox(frame, width=5, from_=1, to=60, textvariable=self.check_interval_var)
        check_interval_spin.grid(row=2, column=1, sticky=tk.W, padx=10, pady=10)
        check_interval_spin.bind("<KeyRelease>", lambda e: self.mark_changed("check_interval", widget_or_var=check_interval_spin))

        # Command timeout
        ttk.Label(frame, text="Таймаут ответа роутера (сек):", font=("Arial", 10)).grid(row=3, column=0, sticky=tk.W, padx=10, pady=10)
        self.command_timeout_var = tk.IntVar(value=self.config.get("command_timeout", 5))
        command_timeout_spin = ttk.Spinbox(frame, width=5, from_=1, to=30, textvariable=self.command_timeout_var)
        command_timeout_spin.grid(row=3, column=1, sticky=tk.W, padx=10, pady=10)
        command_timeout_spin.bind("<KeyRelease>", lambda e: self.mark_changed("command_timeout", widget_or_var=command_timeout_spin))

        # Autorun checkbox
        self.autorun_var = tk.BooleanVar(value=self.config.get("autorun", False))
        autorun_check = ttk.Checkbutton(frame, text="Запускать при старте Windows", variable=self.autorun_var, command=self.toggle_autorun)
        autorun_check.grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=10, pady=10) 

        # IP Monitor
        frame_ip_mon = ttk.Labelframe(frame, text=" IP Монитор ", labelanchor="n")
        frame_ip_mon.grid(row=6, column=0, columnspan=2, sticky=tk.W, padx=10, pady=10) 
        self.autorun_ip_mon = tk.BooleanVar(value=self.config.get("autorun_ip_mon", False))
        autorun_ip_mon_check = ttk.Checkbutton(frame_ip_mon, text="Запускать при старте", variable=self.autorun_ip_mon, command=lambda: self.mark_changed("autorun_ip_mon", widget_or_var=self.autorun_ip_mon))
        autorun_ip_mon_check.pack(side= tk.LEFT, pady=5, padx=5) 
        ttk.Separator(frame_ip_mon, orient='vertical').pack(side= tk.LEFT, fill='y', padx=5)
        ttk.Label(frame_ip_mon, text="Интервал опроса (сек):", font=("Arial", 10)).pack(side= tk.LEFT, pady=5, padx=5) 
        self.ip_check_interval_var = tk.IntVar(value=self.config.get("ip_check_interval", 60))
        self.ip_check_interval_spin = ttk.Spinbox(frame_ip_mon, width=5, from_=1, to=300, textvariable=self.ip_check_interval_var)
        self.ip_check_interval_spin.pack(side= tk.LEFT, pady=5, padx=5)
        self.ip_check_interval_spin.bind("<KeyRelease>", lambda e: self.mark_changed("ip_check_interval", widget_or_var=self.ip_check_interval_spin))
        ToolTip(self.ip_check_interval_spin, 
                " Рекомендуется устанавливать интервал не меньше 30 секунд,чтобы избежать \n"\
                " возможных блокировок со стороны провайдера при частых запросах. \n"\
                " Настройка не применится автоматически если монитор уже запущен ", posY=-70, posX=-40)
                
        def change_log_level(event):
            # Получаем текстовое название уровня из combobox
            selected_level_name = logLevelComboBox.get()
            self.config["log_level"] = selected_level_name
            self.mark_changed("log_level")
            # Превращаем текст в числовой уровень (например, "DEBUG" -> 10)
            numeric_level = getattr(logging, selected_level_name)
            
            # Меняем уровень логгера
            log.setLevel(numeric_level)

        levels_mapping = logging.getLevelNamesMapping()
        if 'NOTSET' in levels_mapping:
            del levels_mapping['NOTSET']
        log_levels = sorted(levels_mapping.keys(), key=lambda name: levels_mapping[name])
        
        frame_log = ttk.LabelFrame(frame, text=" Уровень логирования ")
        frame_log.grid(row=7, column=0, columnspan=2, sticky=tk.W, padx=10, pady=10) 

        logLevelComboBox = ttk.Combobox(frame_log, values=log_levels, state="readonly")
        logLevelComboBox.pack(pady=10, padx=10, fill="x")
        current_level_name = logging.getLevelName(log.getEffectiveLevel())
        
        logLevelComboBox.set(current_level_name)
        logLevelComboBox.bind("<<ComboboxSelected>>", change_log_level)


#======================================= SWITCHES TAB ======================================    


    def setup_switches_tab(self, notebook):
        """Вторая вкладка: Dual Treeview + Редактирование имени в таблице + Перетаскивание слева"""
        # Создаем фрейм вкладки
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="Переключатели политик")

        # Сетка для всей вкладки
        frame.columnconfigure(0, weight=1)  # Левая таблица (Активные)
        frame.columnconfigure(1, weight=0)  # Кнопки (Фикс)
        frame.columnconfigure(2, weight=1)  # Правая таблица (Скрытые)
        frame.rowconfigure(0, weight=0)     # Заголовки (не растягиваются)
        frame.rowconfigure(1, weight=1)     # Строка с таблицами растягивается
        frame.rowconfigure(2, weight=0)     # 
        # Заголовки
        tk.Label(frame, text="Активные переключатели", font=("Arial", 10, "bold")).grid(row=0, column=0, pady=(10, 5), sticky="w", padx=(15, 5))
        tk.Label(frame, text="Скрытые / Отключенные", font=("Arial", 10, "bold")).grid(row=0, column=2, pady=(10, 5), sticky="w", padx=(5, 15))

        # --- ЛЕВАЯ СТОРОНА (Активные) с Treeview ---
        left_frame = ttk.Frame(frame)
        left_frame.grid(row=1, column=0, padx=(15, 5), pady=(5, 10), sticky="nsew")
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)

        # Сохраняем ссылку на left_frame для редактирования ячеек
        self.left_frame = left_frame

        # Определяем колонки для активных переключателей
        columns_active = ("id", "name", "policy_description", "interface", "interface_description", "interface_link")
        self.active_treeview = ttk.Treeview(left_frame, columns=columns_active, show="tree headings", height=12)
        
        # Настраиваем колонки
        self.active_treeview.column("#0", width=0, stretch=False)
        self.active_treeview.column("id", anchor=tk.W, width=30)
        self.active_treeview.column("name", anchor=tk.W, width=100)
        self.active_treeview.column("policy_description", anchor=tk.W, width=120)
        self.active_treeview.column("interface", anchor=tk.W, width=80)
        self.active_treeview.column("interface_description", anchor=tk.W, width=100)
        self.active_treeview.column("interface_link", anchor=tk.CENTER, width=20)
        
        # Заголовки колонок
        self.active_treeview.heading("id", text="Policy ID")
        self.active_treeview.heading("name", text="Имя переключателя")
        self.active_treeview.heading("policy_description", text="Имя политики")
        self.active_treeview.heading("interface", text="Interface ID")
        self.active_treeview.heading("interface_description", text="Имя интерфейса")
        self.active_treeview.heading("interface_link", text="Link")

        self.active_treeview.grid(row=0, column=0, sticky="nsew")        

        # Текст серый (цвет #808080 или "gray")
        self.active_treeview.tag_configure("unavailable", foreground="gray")
        # Текст жирный (семейство и размер шрифта подстроятся автоматически)
        self.active_treeview.tag_configure("new", font=("", 0, "bold"))
        
        # Скроллбар для активных
        left_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=self.active_treeview.yview)
        self.active_treeview.configure(yscrollcommand=left_scroll.set)
        left_scroll.grid(row=0, column=1, sticky="ns")
        
        # Привязываем двойной клик для редактирования имени
        self.active_treeview.bind("<Double-1>", self.on_active_cell_double_click)

        # --- ЦЕНТР (Кнопки перемещения) ---
        btn_frame = tk.Frame(frame)
        btn_frame.grid(row=1, column=1, padx=5, pady=10, sticky="ns")

        tk.Button(btn_frame, text=">", width=5, command=self.move_to_hidden).pack(pady=5)
        tk.Button(btn_frame, text="<", width=5, command=self.move_to_active).pack(pady=5)
        tk.Button(btn_frame, text=">>", width=5, command=self.move_all_to_hidden).pack(pady=5)
        tk.Button(btn_frame, text="<<", width=5, command=self.move_all_to_active).pack(pady=5)
        
        # Разделитель
        ttk.Separator(btn_frame, orient='horizontal').pack(fill='x', pady=10)
        
        # Кнопки для перемещения элемента в левой панели
        tk.Button(btn_frame, text="↑", width=5, command=self.move_active_up).pack(pady=5)
        tk.Button(btn_frame, text="↓", width=5, command=self.move_active_down).pack(pady=5)

        self.update_btn = tk.Button(btn_frame, text="⟳", width=5, command=self.update_data_from_router)
        self.update_btn.pack(side=tk.BOTTOM, pady=5)
        ToolTip(self.update_btn, "Обновить данные из роутера", posY=-40, posX=-20)

        # --- ПРАВАЯ СТОРОНА (Скрытые) с Treeview ---
        right_frame = ttk.Frame(frame)
        right_frame.grid(row=1, column=2, padx=(5, 15), pady=(5, 10), sticky="nsew")
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)

        columns_hidden = ("id", "name")
        self.hidden_treeview = ttk.Treeview(right_frame, columns=columns_hidden, show="tree headings", height=12)
        
        # Настраиваем колонки
        self.hidden_treeview.column("#0", width=0, stretch=False)
        self.hidden_treeview.column("id", anchor=tk.W, width=30)
        self.hidden_treeview.column("name", anchor=tk.W, width=150)
        
        # Заголовки колонок
        self.hidden_treeview.heading("id", text="Policy ID")
        self.hidden_treeview.heading("name", text="Имя переключателя")
        
        self.hidden_treeview.grid(row=0, column=0, sticky="nsew")

        # Текст серый (цвет #808080 или "gray")
        self.hidden_treeview.tag_configure("unavailable", foreground="gray")
        # Текст жирный (семейство и размер шрифта подстроятся автоматически)
        self.hidden_treeview.tag_configure("new", font=("", 0, "bold"))
        
        # Скроллбар для скрытых
        right_scroll = ttk.Scrollbar(right_frame, orient="vertical", command=self.hidden_treeview.yview)
        self.hidden_treeview.configure(yscrollcommand=right_scroll.set)
        right_scroll.grid(row=0, column=1, sticky="ns")

        # Напоминалка для пользователя
        self.info_label = ttk.Label(frame, text="Двойной клик в поле имени переключателя - для редактирования, двойной клик остальных полей копирует их содержимое в поле имени переключателя", font=("Arial", 8))
        self.info_label.grid(row=2, column=0, sticky=tk.W, padx=(15, 5), pady=(0, 10))

        # Переменные для DND и маппинга
        self.active_treeview_map: list[int] = []
        self.hidden_treeview_map: list[int] = []
        self.edit_entry = None
        self.edit_entry_item = None
        self.edit_entry_original_name = None
        self.update_data_from_router()  # Изначальная загрузка данных из роутера при создании вкладки
        self.refresh_switches_tab()


#====================== SWITCHES TAB FUNCTIONS ===================================

    def move_to_hidden(self):
        """Переместить выбранные элементы из Активных в Скрытые (enabled = False)"""
        selection = self.active_treeview.selection()
        if not selection:
            return

        moved_ids = []

        # 1. Циклом проходим по всем выделенным строкам
        for item_id in selection:
            row_values = self.active_treeview.item(item_id)["values"]
            if row_values:
                # Из первой колонки values (индекс 0) забираем ID свитча
                switch_id = row_values[0]
                moved_ids.append(switch_id)

                # 2. Ищем этот свитч в общей структуре и выключаем его
                for switch in self.switches:
                    if switch["id"] == switch_id:
                        switch["enabled"] = False
                        break

        # 3. Обновляем интерфейс
        self.finalize_tab_2_move()

        # 4. Восстанавливаем выделение в целевом дереве скрытых элементов
        if moved_ids:
            # Используем обычное множество строк для быстрого поиска
            targets = set(moved_ids)
            rows_to_select = []

            for row_id in self.hidden_treeview.get_children():
                hidden_values = self.hidden_treeview.item(row_id)["values"]
                if hidden_values and hidden_values[0] in targets:
                    rows_to_select.append(row_id)

            if rows_to_select:
                self.hidden_treeview.selection_set(rows_to_select)
                self.hidden_treeview.focus(rows_to_select[0]) 
                self.hidden_treeview.see(rows_to_select[0]) 

    def move_to_active(self):
        """Переместить выбранные элементы из Скрытых в Активные с сохранением позиции вставки"""
        hidden_selection = self.hidden_treeview.selection()
        if not hidden_selection:
            return

        # 1. Определяем целевой свитч в левой таблице для вставки
        target_switch = None
        active_selection = self.active_treeview.selection()
        
        if active_selection:
            active_values = self.active_treeview.item(active_selection[0])["values"]
            if active_values:
                active_id = active_values[0]
                for switch in self.switches:
                    if switch["id"] == active_id:
                        target_switch = switch
                        break

        # 2. Собираем объекты свитчей, которые нужно переместить, и включаем их
        switches_to_move = []
        moved_ids = []
        
        for item_id in hidden_selection:
            hidden_values = self.hidden_treeview.item(item_id)["values"]
            if hidden_values:
                hidden_id = hidden_values[0]
                moved_ids.append(hidden_id)
                
                for switch in self.switches:
                    if switch["id"] == hidden_id:
                        switch["enabled"] = True
                        switches_to_move.append(switch)
                        break

        # 3. Перестраиваем список self.switches
        if switches_to_move:
            # Удаляем перемещаемые элементы со своих старых мест
            for sw in switches_to_move:
                self.switches.remove(sw)
            
            # Определяем новый индекс для вставки
            if target_switch and target_switch in self.switches:
                insert_idx = self.switches.index(target_switch)
            else:
                insert_idx = len(self.switches)
            
            # Вставляем всю пачку элементов один за другим
            for sw in reversed(switches_to_move):
                self.switches.insert(insert_idx, sw)

        # 4. Обновляем интерфейс
        self.finalize_tab_2_move()

        # 5. Возвращаем множественное выделение в активном дереве
        if moved_ids:
            targets = set(moved_ids)
            rows_to_select = []

            for row_id in self.active_treeview.get_children():
                active_values = self.active_treeview.item(row_id)["values"]
                if active_values and active_values[0] in targets:
                    rows_to_select.append(row_id)

            if rows_to_select:
                self.active_treeview.selection_set(rows_to_select) 
                self.active_treeview.focus(rows_to_select[0])
                self.active_treeview.see(rows_to_select[0]) 

    def move_all_to_hidden(self):
        """Скрыть вообще все элементы"""
        for real_index in self.active_treeview_map:
            self.switches[real_index]["enabled"] = False
        self.finalize_tab_2_move()
        # Выделяем первый элемент в скрытых
        hidden_items = self.hidden_treeview.get_children()
        if hidden_items:
            self.hidden_treeview.selection_set(hidden_items[0])
            self.hidden_treeview.focus(hidden_items[0])
            self.hidden_treeview.see(hidden_items[0])

    def move_all_to_active(self):
        """Показать вообще все элементы"""
        for real_index in self.hidden_treeview_map:
            self.switches[real_index]["enabled"] = True
        self.finalize_tab_2_move()
        # Выделяем первый элемент в активных
        active_items = self.active_treeview.get_children()
        if active_items:
            self.active_treeview.selection_set(active_items[0])

    def finalize_tab_2_move(self):
        """Вспомогательный метод для фиксации изменений на вкладке 2"""
        self.mark_changed("switches")
        self.refresh_switches_tab()


    def move_active_up(self):
        """Переместить выбранный элемент в левой панели вверх"""
        selection = self.active_treeview.selection()
        if not selection:
            return
        
        item_id = selection[0]
        active_items = self.active_treeview.get_children()
        
        try:
            idx = active_items.index(item_id)
            if idx > 0:  # Если не первый элемент
                # Получаем реальные индексы в switches
                real_idx = self.active_treeview_map[idx]
                real_prev_idx = self.active_treeview_map[idx - 1]
                
                # Меняем местами в основном списке
                self.switches[real_idx], self.switches[real_prev_idx] = \
                    self.switches[real_prev_idx], self.switches[real_idx]
                
                self.mark_changed("switches")
                self._refresh_active_treeview()
                
                # Выделяем тот же элемент (который теперь выше)
                new_active_items = self.active_treeview.get_children()
                if idx - 1 < len(new_active_items):
                    self.active_treeview.selection_set(new_active_items[idx - 1])
        except (ValueError, IndexError):
            pass

    def move_active_down(self):
        """Переместить выбранный элемент в левой панели вниз"""
        selection = self.active_treeview.selection()
        if not selection:
            return
        
        item_id = selection[0]
        active_items = self.active_treeview.get_children()
        
        try:
            idx = active_items.index(item_id)
            if idx < len(active_items) - 1:  # Если не последний элемент
                # Получаем реальные индексы в switches
                real_idx = self.active_treeview_map[idx]
                real_next_idx = self.active_treeview_map[idx + 1]
                
                # Меняем местами в основном списке
                self.switches[real_idx], self.switches[real_next_idx] = \
                    self.switches[real_next_idx], self.switches[real_idx]
                
                self.mark_changed("switches")
                self._refresh_active_treeview()
                
                # Выделяем тот же элемент (который теперь ниже)
                new_active_items = self.active_treeview.get_children()
                if idx + 1 < len(new_active_items):
                    self.active_treeview.selection_set(new_active_items[idx + 1])
        except (ValueError, IndexError):
            pass

    def refresh_switches_tab(self):
        """Обновление интерфейса вкладки переключателей с учетом порядка и фильтрации"""
        # Обновляем обе части таблицы
        self._refresh_active_treeview()
        self._refresh_hidden_treeview()

    def _refresh_active_treeview(self):
        """Обновляет только активные элементы в левой таблице (быстрое обновление)"""
        # Очищаем таблицу
        for item in self.active_treeview.get_children():
            self.active_treeview.delete(item)
        
        self.active_treeview_map = []

        for real_index, switch in enumerate(self.switches):
            if switch.get("enabled", True):
                policy_data = switch.get("policy_data", {})
                policy_enabled = policy_data.get('policy_enabled', False)
                tag = ("unavailable",) if policy_data=={} else ("new",) if switch.get("new", False) else ()
                # Сокращаем interface_link до одного символа
                interface_link = policy_data.get('interface_link', '')
                if interface_link and policy_enabled:
                    interface_link = "U" if interface_link == 'up' else "D" if interface_link == 'down' else "?"
                else:
                    interface_link = '-'
                
                values = (
                    switch['id'],
                    switch.get('name', switch['id']),
                    policy_data.get('policy_description', ''),
                    policy_data.get('interface_name', '') if policy_enabled else '-',
                    policy_data.get('interface_description', '') if policy_enabled else '-',
                    interface_link
                )
                self.active_treeview.insert('', 'end', values=values, tags=tag)
                self.active_treeview_map.append(real_index)

    def _refresh_hidden_treeview(self):
        """Обновляет только скрытые элементы в правой таблице"""
        # Очищаем таблицу
        for item in self.hidden_treeview.get_children():
            self.hidden_treeview.delete(item)
        
        self.hidden_treeview_map = []

        for real_index, switch in enumerate(self.switches):
            if not switch.get("enabled", True):
                policy_data = switch.get("policy_data", {})
                tag = ("unavailable",) if policy_data=={} else ("new",) if switch.get("new", False) else ()
                hidden_values = (switch['id'], switch.get('name', 'Без имени'))
                self.hidden_treeview.insert('', 'end', values=hidden_values, tags=tag)
                self.hidden_treeview_map.append(real_index)

    def on_active_cell_double_click(self, event):
        """Редактирование ячейки 'name' при двойном клике"""
        item = self.active_treeview.identify('item', event.x, event.y)
        column = self.active_treeview.identify_column(event.x)
        
        # Редактируем только колонку 'name' (индекс 2: #0=tree, #1=id, #2=name)
        if item and column in ['#1','#2','#3','#4','#5']:
            active_items = self.active_treeview.get_children()
            try:
                idx = active_items.index(item)
                if idx < len(self.active_treeview_map):
                    real_idx = self.active_treeview_map[idx]
                    if  column == '#2':
                        self.start_edit_name(item, real_idx)
                    else:
                        self.copy_item_value_to_name(item, column.replace('#', ''), real_idx)
            except ValueError:
                pass
    
    def copy_item_value_to_name (self, treeview_item, column, real_idx):
        current_values = self.active_treeview.item(treeview_item, 'values')
        if 0 <= int(column) < len(current_values):
            new_name = current_values[int(column)-1]
            # Обновляем имя элемента
            dialog = MessageBox_Chkbox(
                self.root, "Подтверждение", f"Вы уверены, что хотите изменить имя переключателя на '{new_name}'?\n Это перезапишет текущее имя переключателя.", 
                f"""  Скопировать все элементы из колонки \n  "{self.active_treeview.heading(f'#{column}', 'text')}" в имена переключателей?"""
            )
            if dialog.result_ok:
                if dialog.checkbox_value.get():
                    column_values = [
                    self.active_treeview.item(row_id)["values"][int(column)-1] for row_id in self.active_treeview.get_children()
                    ]
                    for idx, value in enumerate(column_values):
                        if idx < len(self.active_treeview_map):
                            real_idx = self.active_treeview_map[idx]
                            self.switches[real_idx]["name"] = value
                else:
                    self.switches[real_idx]["name"] = new_name
                self.mark_changed("switches")
                self._refresh_active_treeview()
        return
    def start_edit_name(self, treeview_item, real_idx):
        """Начинает редактирование имени для элемента"""
        # Закрываем предыдущее редактирование если было
        if self.edit_entry:
            self.end_edit_name(save=True)
        
        # Получаем текущее значение
        current_values = self.active_treeview.item(treeview_item, 'values')
        current_name = current_values[1] if len(current_values) > 1 else ""
        
        # Получаем координаты ячейки в координатах Treeview
        bbox = self.active_treeview.bbox(treeview_item, '#2')
        if bbox is None:
            return
        
        x, y, width, height = bbox
        
        # Создаем Entry как дочерний элемент основного окна (self.root)
        # это обеспечит правильное отображение поверх Treeview
        self.edit_entry = tk.Entry(self.root, relief=tk.SOLID, borderwidth=1)
        self.edit_entry.insert(0, current_name)
        self.edit_entry.select_range(0, tk.END)
        
        # Вычисляем абсолютные координаты в окне
        treeview_rootx = self.active_treeview.winfo_rootx()
        treeview_rooty = self.active_treeview.winfo_rooty()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        
        entry_x = treeview_rootx + int(x) - root_x
        entry_y = treeview_rooty + int(y) - root_y
        
        # Позиционируем Entry поверх ячейки (place в координатах root)
        self.edit_entry.place(x=entry_x, y=entry_y, width=int(width), height=int(height))
        self.edit_entry.lift()
        
        # Сохраняем информацию о редактируемом элементе и оригинальное значение
        self.edit_entry_item = (treeview_item, real_idx)
        self.edit_entry_original_name = current_name
        
        # Привязываем события
        self.edit_entry.bind('<Return>', lambda e: self.end_edit_name(save=True))
        self.edit_entry.bind('<Escape>', lambda e: self.end_edit_name(save=False))
        self.edit_entry.bind('<FocusOut>', lambda e: self.end_edit_name(save=True))
        
        self.edit_entry.focus()

    def end_edit_name(self, save=True):
        """Заканчивает редактирование имени"""
        if not self.edit_entry or not self.edit_entry_item:
            return
        
        treeview_item, real_idx = self.edit_entry_item
        
        if save:
            new_name = self.edit_entry.get().strip()
            # Сравниваем с оригинальным значением - только если оно изменилось
            if new_name and new_name != self.edit_entry_original_name:
                self.switches[real_idx]["name"] = new_name
                self.mark_changed("switches")
                self._refresh_active_treeview()
        
        self.edit_entry.place_forget()
        self.edit_entry.destroy()
        self.edit_entry = None
        self.edit_entry_item = None
        self.edit_entry_original_name = None

#=================================================================================

    # Изменили порядок: теперь key идет первым, а value по умолчанию True
    def mark_changed(self, key=None, value=True, widget_or_var=None):
        """Отмечает, что конфигурация или её отдельные поля были изменены."""

        # 1. Автоматический режим для виджетов
        if key and widget_or_var is not None:
            if hasattr(widget_or_var, 'get') and 'Variable' in type(widget_or_var).__name__:
                new_value = widget_or_var.get()
            elif hasattr(widget_or_var, 'get'):
                new_value = widget_or_var.get().strip()
            else:
                return  # Неизвестный тип объекта — просто выходим

            if self.config.get(key) == new_value:
                self.has_unsaved_changes[key] = False
            else:
                self.has_unsaved_changes[key] = True

        # 2. Ручной режим (Передан ТОЛЬКО ключ: self.mark_changed("switches"))
        # Позиционный аргумент key перехватит строку, а value по умолчанию станет True
        elif key and widget_or_var is None:
            if key=="switches":
                switches=[
                            {
                                "id": item["id"], 
                                "name": item["name"],
                                "enabled": item.get("enabled", True) 
                            } for item in self.switches
                        ]


                self.has_unsaved_changes[key] = self.config.get(key, []) != switches
            else:
                self.has_unsaved_changes[key] = value
        # 3. Полный сброс всех флагов (Вызывается как self.mark_changed(value=False) после сохранения)
        elif key is None and widget_or_var is None and not value:
            self.has_unsaved_changes.clear()
            
        # 4. Режим инициализации дефолтного конфига (Вызывается вообще без параметров: self.mark_changed())
        elif key is None and widget_or_var is None and value:
            self.has_unsaved_changes["init"] = True 

        # Универсальное управление состоянием кнопки сохранения
        if hasattr(self, 'save_btn'):
            is_dirty = any(self.has_unsaved_changes.values())
            self.save_btn.config(state=tk.NORMAL if is_dirty else tk.DISABLED)



    def toggle_autorun(self):
        """Переключает автозапуск Windows и обновляет состояние конфига."""
        if not is_onedir_build():
            messagebox.showerror(
                APP_NAME_LONG, 
                "Автозагрузка поддерживается только при установке приложения в папку (режим --onedir).", 
                parent=self.root
            )
            return        
        enabled = self.autorun_var.get()
        success, error_message = autorun(enable=enabled)
        if not success:
            messagebox.showerror("Ошибка автозапуска", error_message)
            self.autorun_var.set(not enabled)
            return
        self.mark_changed("autorun", widget_or_var=self.autorun_var)
       
    def on_save(self):
        """Обработчик нажатия кнопки Сохранить"""
        # Обновляем конфиг из полей
        self.config["router_url"] = self.router_url_entry.get().strip()
        self.config["device_mac"] = self.device_mac_entry.get().strip()
        self.config["check_interval"] = self.check_interval_var.get()
        self.config["command_timeout"] = self.command_timeout_var.get()
        self.config["autorun"] = self.autorun_var.get()
        self.config["autorun_ip_mon"] = self.autorun_ip_mon.get()
        self.config["ip_check_interval"] = self.ip_check_interval_var.get()
        self.config["switches"] = [
            {
                "id": item["id"], 
                "name": item["name"],
                "enabled": item.get("enabled", True) 
            } for item in self.switches
        ]
        self.save_config()
        for item in self.switches:
            item.pop("new", None)
        self.refresh_switches_tab()
        self.mark_changed(value=False)
    
    def on_closing(self):
        """Обработчик закрытия окна"""      
        if any(self.has_unsaved_changes.values()):
            response = messagebox.askyesnocancel(
                "Несохраненные изменения",
                "У вас есть несохраненные изменения. Сохранить перед выходом?"
            )
            if response is None:  # Cancel
                return
            elif response:  # Yes
                self.on_save()

        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.REG_PATH) as key:
                is_zoomed = 1 if self.root.state() == "zoomed" else 0
                winreg.SetValueEx(key, "Maximized", 0, winreg.REG_DWORD, is_zoomed)
                if is_zoomed:
                    geom = self.root.wm_geometry()
                else:
                    geom = self.root.geometry()
                winreg.SetValueEx(key, "Geometry", 0, winreg.REG_SZ, geom)
        except Exception:
            pass
        self.root.destroy()
    def get_policy_and_interface_data(self, router_url):
        """Получает данные политик и интерфейсов с роутера и возвращает их в виде словарей"""
        url_policy=f"{router_url.strip().rstrip('/')}/rci/ip/policy"
        url_interface=f"{router_url.strip().rstrip('/')}/rci/show/interface"
        policy_json=None
        interface_json=None
        try:
            response_policy = requests.get(url_policy, timeout=5)
            
            if response_policy.status_code != 200:
                self.root.after(0, lambda rc=response_policy.status_code: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Ошибка ответа роутера Policy: HTTP {rc}")))
                policy_json=None
            else:
                policy_json = response_policy.json()

            response_interface=requests.get(url_interface, timeout=5)
            if response_interface.status_code != 200:
                self.root.after(0, lambda rc=response_interface.status_code: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Ошибка ответа роутера Interface: HTTP {rc}")))
                interface_json=None
            else:
               interface_json=response_interface.json()
        except requests.exceptions.Timeout as e:
            self.root.after(0, lambda e=e: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Таймаут подключения к роутеру: {e}")))
            return None, None
        except requests.exceptions.ConnectionError as e:
            self.root.after(0, lambda e=e: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Ошибка подключения к роутеру: {e}")))
            return None, None
        except requests.exceptions.RequestException as e:
            self.root.after(0, lambda e=e: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Ошибка получения состояния: {str(e)}")))
            return None, None
        except ValueError as e:
            self.root.after(0, lambda e=e: (self.sb.set_error("Роутер недоступен, проверьте адрес и порт"), messagebox.showerror("Ошибка", f"Ошибка парсинга JSON: {str(e)}")))
            return None, None
        
        return policy_json, interface_json


    def update_data_from_router(self):

        if self.is_request_fetching:
            return
        self.is_request_fetching = True

        router_url = self.router_url_entry.get()
        # policy_json, interface_json = self.get_policy_and_interface_data(router_url)
        def process_received_data(policy_json, interface_json):
            if policy_json is None or interface_json is None:
                return
            self.sb.set_success("Роутер доступен, данные обновлены.")
            def get_policy_data(policyID, interface):
                """Получение данных политики из роутера"""          
                policy = policy_json[policyID]
                return {
                            "policy_enabled": policy["permit"][0]["enabled"],
                            "policy_description": policy.get("description", ""),
                            "interface_name": interface.get("interface-name", ""),
                            "interface_description": interface.get("description", ""),
                            "interface_link": interface.get("link", ""),
                        }
            initial_start = False
            if self.config.get("switches",[]): #При первой инциализации в основном конфиге нет данных о переключателях
                # Для существующих политик получаем актуальные данные, если политика отсутствует в роутере данные будут {}       
                for item in self.switches:
                    policyID = item["id"]
                    if policyID in policy_json:
                        policy = policy_json[policyID]
                        interface=interface_json.get(policy["permit"][0]["interface"], {})
                        item["policy_data"] = get_policy_data(policyID, interface)
                    else:
                        # Отсутствующие политики возврвщают пустой словарь и помечатся тегом недоступно
                        item["policy_data"] = {} 
            else:
                initial_start = True
                self.switches = [] # При первом запуске если получены данные из роутера обнуляем switches
                                   # Если данные из роутера не получены, то в switches останется список инициализации из load_config (ветвь с дефолтными данными)
            # Для отсутствующих политик создаем записи имя подставляем из policy description (имени заданном в роутере)
            existing_policies = [item["id"] for item in self.switches]
            absent_policies  = list(set(policy_json.keys())-set(existing_policies))
            for policyID in absent_policies:
                policy = policy_json[policyID]
                interface = interface_json.get(policy["permit"][0]["interface"], {})
                new_item = {
                    "id": policyID,
                    "name": policy.get("description", policyID),
                    "enabled": initial_start, # Если это первый запуск, то новые политики по умолчанию включены, иначе выключены, так как пользователь их не добавлял
                    "new": True, # Помечаем как новый, чтобы выделить жирным
                    "policy_data": get_policy_data(policyID, interface)
                }
                self.switches.append(new_item)

            if initial_start:
                # При первом запуске сортируем по id
                self.switches.sort(key=lambda x: int(x["id"].replace("Policy", "")))
            # Проверка есть ли в активных переключателях недоступные политики и спрашиваем пользователя, перенести их в панель скрытых
            for item in self.switches:
                if item.get("enabled", True) and not item.get("policy_data", {}):
                    responce = messagebox.askyesno(
                        "Внимание! Недоступная политика",
                        f"Переключатель {item['id']} ({item['name']}) недоступен в роутере \n Вероятно он был удален в его настройках \n\n Перенести его в панель скрытых?"
                    )
                    if responce:
                        item["enabled"]=False
                        self.mark_changed("switches")
            self.refresh_switches_tab()
            return
        def process_send_request():
            try:
                policy_json, interface_json = self.get_policy_and_interface_data(router_url)
                self.root.after(0, lambda: process_received_data(policy_json, interface_json))
            finally:
                self.is_request_fetching = False
        threading.Thread(target=process_send_request, daemon=True).start()



def autorun (enable=True):
    """Добавляет или удаляет программу из автозагрузки Windows"""
    try:       
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_ALL_ACCESS) as reg_key:
            if enable:
                winreg.SetValueEx(reg_key, APP_NAME, 0, winreg.REG_SZ, sys.executable)
                return True, ""
            else:
                try:
                    winreg.DeleteValue(reg_key, APP_NAME)
                    return True, ""
                except FileNotFoundError:
                    return True, ""
    except Exception as e:
        error_message = f"Не удалось {'добавить' if enable else 'удалить'} из автозагрузки: {e}"
        return False, error_message
def is_autorun_enabled():
    """Проверяет, добавлена ли программа в автозагрузку Windows (только наличие ключа)"""
    try:
        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as key:
            try:
                # Пытаемся прочитать значение – если ошибки нет, ключ существует
                winreg.QueryValueEx(key, APP_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception as e:
        log.debug("Ошибка", f"Не удалось проверить автозагрузку: {e}")
        return False
def get_application_hash():
    """Возвращает безопасную Base64-строку пути запуска программы."""
    import base64
    if getattr(sys, "frozen", False):
        current_path = os.path.abspath(sys.executable)
    else:
        current_path = os.path.abspath(sys.argv[0])
    normalized_path = current_path.lower()
    path_bytes = normalized_path.encode("utf-8")
    b64_bytes = base64.urlsafe_b64encode(path_bytes)
    return b64_bytes.decode("utf-8").rstrip("=")

class SB:
    """Класс-обертка над tk.Label для удобного управления статус-баром."""
    def __init__(self, label_widget: tk.Label):
        # Сохраняем ссылку на существующий виджет tk.Label внутри класса
        self.label = label_widget

        # Запоминаем оригинальные (дефолтные) настройки, которые были при создании
        self.original_config = {
            "text": self.label.cget("text"),
            "fg": self.label.cget("fg"),
            "bg": self.label.cget("bg"),
            "font": self.label.cget("font"),
        }

    def set_success(self, text="Сохранено успешно"):
        """Переключает статус-бар в режим успеха (зеленый цвет)."""
        self.label.config(
            text=f"✔ {text}",
            fg="#2E7D32",  # Приятный темно-зеленый
            font=("Arial", 10, "bold"),
        )

    def set_error(self, text="Ошибка при выполнении операции"):
        """Переключает статус-бар в режим ошибки (красный цвет)."""
        self.label.config(
            text=f"⚠ {text}",
            fg="#D32F2F",  # Заметный красный
            font=("Arial", 10, "bold"),
        )

    def set_loading(self, text="Пожалуйста, подождите..."):
        """Переключает статус-бар в режим ожидания/загрузки (серый цвет)."""
        self.label.config(
            text=f"⏳ {text}",
            fg="#757575",  # Серый цвет
            font=("Arial", 10, "italic"),
        )

    def set_custom(self, text, fg=None, bg=None, font=None):
        """Метод для передачи любых кастомных параметров на лету."""
        config = {"text": text}
        if fg:
            config["fg"] = fg
        if bg:
            config["bg"] = bg
        if font:
            config["font"] = font
        self.label.config(**config)

    def reset(self):
        """Мгновенно сбрасывает статус-бар к первоначальному виду."""
        self.label.config(**self.original_config)
class ToolTip:
    def __init__(self, widget, text, posX=20, posY=-40):
        self.posX = posX
        self.posY = posY
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hide_tip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.show_tip) # Задержка 500 мс перед показом

    def unschedule(self):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def show_tip(self):
        if self.tip_window or not self.text:
            return
        # Позиционируем подсказку рядом с курсором
        x, y = self.widget.winfo_rootx() + self.posX, self.widget.winfo_rooty() + self.posY
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(self.tip_window, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1)
        label.pack()

    def hide_tip(self):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None

class MessageBox_Chkbox(tk.Toplevel):

    def __init__(self, parent, title, message, checkbox_text):
        super().__init__(parent)

        # Размеры окна сообщения
        WIDTH = 350
        HEIGHT = 180

        x = parent.winfo_x() + (parent.winfo_width() // 2) - (WIDTH // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (HEIGHT // 2)

        # Применяем всё ОДИН раз в самом начале конструктора
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.resizable(False, False)
        self.title(title)

        # Делаем окно модальным (блокирует родительское окно)
        self.transient(parent)
        self.grab_set()

        # Результаты работы окна
        self.result_ok = False
        self.checkbox_value = tk.BooleanVar(value=False)

        # Контейнер для отступов
        frame = ttk.Frame(self, padding=15)
        frame.pack(fill="both", expand=True)

        # Текст сообщения
        lbl_msg = ttk.Label(frame, text=message, wraplength=300, justify="left")
        lbl_msg.pack(anchor="w", pady=(0, 15))

        # Чекбокс
        cb = ttk.Checkbutton(
            frame, text=checkbox_text, variable=self.checkbox_value
        )
        cb.pack(anchor="w", pady=(0, 15))

        # Блок кнопок
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(anchor="e")

        btn_ok = ttk.Button(btn_frame, text="OK", command=self.on_ok)
        btn_ok.pack(side="left", padx=(0, 5))

        btn_cancel = ttk.Button(
            btn_frame, text="Cancel", command=self.on_cancel
        )
        btn_cancel.pack(side="left")

        # Ждем закрытия окна
        self.wait_window(self)

    def on_ok(self):
        self.result_ok = True
        self.destroy()

    def on_cancel(self):
        self.result_ok = False
        self.destroy()

def main():
    root = tk.Tk()
    gui = SettingsGUI(root)
    root.mainloop()

if __name__ == "__main__":
    print("!!! ВНИМАНИЕ: settings_gui.py запущен как основной скрипт !!!")
    main()
