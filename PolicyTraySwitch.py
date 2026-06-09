#!/usr/bin/env python
# -*- coding: utf-8 -*-

from build import CONFIG_FILE, APP_NAME, APP_NAME_LONG, APP_ICON_DIR, APP_ICON_TRAY, APP_ICON, APP_ICON_TRAY_LINK_DOWN

from settings_gui import log, get_app_path, get_data_path, is_onedir_build
import sys
import os

import multiprocessing
import queue

import pystray
from PIL import Image, ImageDraw
import requests

import threading
import time
import json
from pathlib import Path

from typing import Literal

class VPNTrayApp:
     
    def __init__(self, config_file):
        from settings_gui import is_autorun_enabled   
        self.main_thread_action_queue = queue.Queue()
        self.periodic_check_thread_stop_event = threading.Event()

        self.config_file = config_file
        self.icon = None 
        self.config = self.load_config(config_file)
       
        
        # Используем последний переключатель, или первый при первом запуске
        self.current_switch = self.config["last_switch"]  # Текущий выбранный переключатель
        self.current_interface_name = None
        self.current_interface_link = None
        self.current_policy = None
        # Текущее состояние переключателя устанавливается через сеттер
        self._current_state = "unknown"  
    
        self.ip_monitor_process = None
        self.is_my_ip_tray_running = False
        self.start_ip_monitor_time = None
        if self.config.get("autorun_ip_mon", False):
            self.on_toggle_ip_monitor()
            
        self.is_switching = False

        # Синхронизируем статус автозагрузки в конфиге с реальным состоянием в системе
        if self.config["autorun"]!= is_autorun_enabled():
            self.config["autorun"]= not self.config["autorun"]
            self.save_config()
       
        # Создаем иконки для разных состояний
        self.icons = {
            "on": self.create_icon_img((0, 255, 0)),      # зеленый - включен
            "on_link_down": self.create_icon_img((0, 255, 0),icon_file=APP_ICON_TRAY_LINK_DOWN, icon_color=(255, 0, 0)), # зеленый с красным крестом - включен, но линк down
            "off": self.create_icon_img((255, 0, 0)),     # красный - выключен
            "off_link_down": self.create_icon_img((255, 0, 0),icon_file=APP_ICON_TRAY_LINK_DOWN),
            "turning_on": self.create_icon_img((255, 255, 0)),  # желтый - включается
            "turning_off": self.create_icon_img((255, 255, 0)), # желтый - выключается
            "unknown": self.create_icon_img((128, 128, 128)),    # серый - неизвестно
            "unavailable": self.create_icon_img((128, 128, 128)),  # серый - роутер недоступен
            "base_icon": self.create_icon_img(color=None, icon_file=APP_ICON)  # Иконка без цветного фона, только с изображением (или серый квадрат, если изображения нет)
        }
        
    def load_config(self, config_file):
        """Загружает конфигурацию из файла"""

        config = None
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)                   
        except Exception as e:
            MB.error(APP_NAME_LONG, f"Ошибка чтения конфигурации: {e} \n Если это первый запуск приложения, убедитесь что вы не забыли сохранить конфигурацию на предыдущем шаге, попробуйте снова", 
                     queue_obj=self.main_thread_action_queue)           
            self.main_thread_action_queue.put(("EXIT", 1))
            return {}  

        # Проверка обязательных полей для Keenetic
        if not config.get("router_url"):
            MB.error(APP_NAME_LONG, "router_url не указан в config.json!\n\nУкажите URL роутера типа http://192.168.1.1:81", 
                     queue_obj=self.main_thread_action_queue)           
            self.main_thread_action_queue.put(("EXIT", 1))
            return {}

        if not config.get("device_mac"):
            MB.error(APP_NAME_LONG, "device_mac не указан в config.json!\n\nУкажите MAC-адрес устройства, например 00:13:33:af:ee:c7", 
                     queue_obj=self.main_thread_action_queue)           
            self.main_thread_action_queue.put(("EXIT", 1))
            return {}

        # Проверяем, что last_switch есть в списке switches (если установлен)
        switches_ids = [s["id"] for s in config["switches"]]
        if config.get("last_switch") not in switches_ids:           
            config["last_switch"] = switches_ids[0]

        return config

    def save_config(self):
        """Сохраняет текущую конфигурацию в файл"""
        config_file = get_app_path(CONFIG_FILE)
        try:           
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
                log.debug(f"✅ Конфигурация сохранена в: {config_file}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения конфигурации: {e}")

    def create_icon_img(self, color, icon_path=APP_ICON_DIR, icon_file=APP_ICON_TRAY, icon_color=None, link_down=False):
        """Создает иконку: цветной квадрат + APP_ICON_DIR/APP_ICON_TRAY (при наличии)"""
        size = 64
        icon_path = Path(get_data_path(icon_path, icon_file))
        # Если цвет не задан, загружаем иконку заданную в APP_ICON_DIR/APP_ICON_TRAY без цветного фона. Если иконки нет, возвращаем серый квадрат.
        if color is None:
            if icon_path.exists():
                try:
                    img = Image.open(icon_path).convert('RGBA')
                    # Масштабируем до стандартного размера трея (64x64)
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
                    return img
                except Exception as e:
                    log.error(f"Ошибка загрузки {APP_ICON_TRAY}: {e}. Используется серый квадратный фон.")
            
            # Если файла нет, возвращаем серую пустышку, чтобы код не упал при старте
            return Image.new('RGBA', (size, size), (128, 128, 128, 255))



        # Фоновая заливка состоянием
        image = Image.new('RGBA', (size, size), color + (255,))
        draw = ImageDraw.Draw(image)

        # Рамка для видимости границ
        draw.rectangle([0, 0, size - 1, size - 1], outline=(255, 255, 255, 255), width=2)

        
        if icon_path.exists():
            try:
                overlay = Image.open(icon_path).convert('RGBA')
                # уменьшаем до квадратной иконки, чтобы было отступы
                overlay_size = 54
                overlay = overlay.resize((overlay_size, overlay_size), Image.Resampling.LANCZOS)
                # Если задан цвет icon_color, применяем его к иконке, сохраняя прозрачность
                if icon_color is not None:
                    from PIL import ImageOps
                    alpha = overlay.split()[3]
                    overlay = ImageOps.colorize(overlay.convert('L'), black=icon_color, white=icon_color).convert('RGBA')
                    overlay.putalpha(alpha)
                pos = ((size - overlay_size) // 2, (size - overlay_size) // 2)
                image.alpha_composite(overlay, dest=pos)
            except Exception as e:
                log.error(f"Ошибка загрузки {APP_ICON_TRAY}: {e}. Используется цветной фон.")
        else:
            # fallback на белый круг, если icon_tray.png нет
            circle_margin = 8
            draw.ellipse(
                [circle_margin, circle_margin, size - circle_margin, size - circle_margin],
                fill=color + (255,),
                outline=(255, 255, 255, 255),
                width=2
            )

        # Если link_down=True, рисуем белый крест поверх иконки или круга
        if link_down:
            cross_margin = 14 
            cross_color = (255, 255, 255, 255) if color == (255, 0, 0) else (255, 0, 0, 255) # Белый или черный цвет
            cross_width = 8  # Толщина линий для хорошей видимости

            # Линия 1: Из верхнего левого в нижний правый угол
            draw.line(
                [cross_margin, cross_margin, size - cross_margin, size - cross_margin],
                fill=cross_color,
                width=cross_width
            )
            # Линия 2: Из нижнего левого в верхний правый угол
            draw.line(
                [cross_margin, size - cross_margin, size - cross_margin, cross_margin],
                fill=cross_color,
                width=cross_width
            )

        return image
    
    def get_switch_name(self, switch_id):
        """Получает человекочитаемое имя переключателя"""
        for switch in self.config["switches"]:
            if switch["id"] == switch_id:
                return switch.get("name", switch_id)
        return switch_id

################################ Функции управления автозагрузкой приложения при старте Windows ###      


    def enable_autorun(self):
        """Включает автозагрузку приложения"""
        from settings_gui import autorun
        
        # 1. Проверяем строгий режим сборки
        if not is_onedir_build():
            MB.error(
                APP_NAME_LONG, 
                "Автозагрузка поддерживается только при установке приложения в папку (режим --onedir).", 
                queue_obj=self.main_thread_action_queue
            )
            return

        # 2. Основная логика включения
        success, error_msg = autorun(enable=True)
        if success:
            self.config["autorun"] = True
            self.save_config()
            MB.show(APP_NAME_LONG, f"Автозагрузка включена: {sys.executable}", queue_obj=self.main_thread_action_queue)
        else:
            MB.error(APP_NAME_LONG, error_msg)
         
    def disable_autorun(self):
        """Отключает автозагрузку приложения"""
        from settings_gui import autorun
        
        # 1. Проверяем строгий режим сборки
        if not is_onedir_build():
            MB.error(
                APP_NAME_LONG, 
                "Автозагрузка поддерживается только при установке приложения в папку (режим --onedir).", 
                queue_obj=self.main_thread_action_queue
            )
            return

        # 2. Основная логика отключения
        success, error_msg = autorun(enable=False)
        if success:
            self.config["autorun"] = False
            self.save_config()
            MB.show(APP_NAME_LONG, f"Автозагрузка отключена: {sys.executable}", queue_obj=self.main_thread_action_queue)
        else:
            MB.error(APP_NAME_LONG, error_msg)
    def on_toggle_autorun(self, icon=None, item=None):
        """Переключает статус автозагрузки"""
        if self.config.get("autorun", False):
            self.disable_autorun()
        else:
            self.enable_autorun()
##################################### ФУНКЦИИ УПРАВЛЕНИЯ РОУТЕРОМ ############################################

    def request_to_router(self, url, retry_count=3):
        for attempt in range(1, retry_count + 1):
            try:
                response = requests.get(url, timeout=5)

                if response.status_code != 200:
                    log.error(f"Ошибка получения статуса запроса {url}: HTTP {response.status_code}")
                    return None, response.status_code
                   
                return response.json(), response.status_code
                
            except requests.exceptions.Timeout as e:
                log.error(f"Таймаут подключения к роутеру запрос - {url} (попытка {attempt}/{retry_count}): {e}")
                if attempt < retry_count:
                    time.sleep(0.2)
                    continue
                return None, e
            except requests.exceptions.ConnectionError as e:
                log.error(f"Ошибка подключения к роутеру запрос - {url} (попытка {attempt}/{retry_count}): {e}")
                if attempt < retry_count:
                    time.sleep(0.2)
                    continue
                return None, e
            except requests.exceptions.RequestException as e:
                log.error(f"Ошибка получения состояния запроса {url}: {str(e)}")
                return None, e
            except ValueError as e:
                log.error(f"Ошибка парсинга JSON запроса {url}: {str(e)}")
                return None, e
                
        log.error(f"Запрос к {url}: Макс. количество попыток исчерпано")
        return None, "Макс. количество попыток исчерпано"

    def get_switch_state_from_router(self, switch_id=None):
        """Получает текущее состояние переключателя из роутера Keenetic"""
        if switch_id is None:
            switch_id = self.current_switch
        
        # 1. Проверка состояния политики текущего переключателя
        url = f"{self.config['router_url'].rstrip('/')}/rci/ip/policy/{switch_id}"
        policy_json, err_msg = self.request_to_router(url)
        if policy_json is None: 
            
            if err_msg == 404:
                log.error(f"Запрос политики {switch_id} вернул 404, возможно она была удалена в настройках роутера")
                self.current_state = "policy_unavailable"
                self.on_lost_policy(switch_id)
            else:
                log.error(f"Запрос политики {switch_id} вернул ошибку {str(err_msg)}")
                self.current_state = "unavailable"
            return None, err_msg
        # При успешном получении политики текущего переклюяателя сбрасываем флаг недоступности политики
        self.is_lost_policy_notified = None

        # Обновляем данные интерфейса, связанного с политикой (для отображения в подсказке)
        interfaceID = policy_json['permit'][0]['interface']
        url = f"{self.config['router_url'].rstrip('/')}/rci/show/interface/{interfaceID}"
        interface_json, err_msg = self.request_to_router(url)

        # Получаем статус подключения политики к хосту
        url = f"{self.config['router_url'].rstrip('/')}/rci/ip/hotspot/host"
        hosts, err_msg = self.request_to_router(url)

        if interface_json:
            old_interface_name = self.current_interface_name
            old_current_interface_link = self.current_interface_link
            self.current_interface_name = interface_json['description']
            self.current_interface_link = interface_json['link']
            if old_interface_name != self.current_interface_name or old_current_interface_link != self.current_interface_link:
                if old_interface_name != self.current_interface_name:              
                    log.debug(f"🔄 Имя текущего интерфейса политики {switch_id} обновлено на {self.current_interface_name}")
                if old_current_interface_link != self.current_interface_link:
                    log.debug(f"📶 Link текущего интерфейса политики {switch_id} {self.current_interface_name} изменился на {self.current_interface_link}")

        if hosts is None:
            log.error(f"Запрос списка хостов вернул ошибку {str(err_msg)}")
            self.current_state = "unavailable"
            return None, err_msg

        if not isinstance(hosts, list):
            err_msg = "Неожиданный формат ответа от роутера (ожидался list)"
            log.error(f"Ошибка: {err_msg}")
            self.current_state = "unavailable"
            return None, err_msg

        target_mac = self.config['device_mac'].lower()
        old_current_policy = self.current_policy
        for obj in hosts:
            if str(obj.get('mac', '')).lower() == target_mac:
                self.current_policy = obj.get('policy')
                # Если политика хоста сменилась, обновляем меню (для обновления свойства default)
                if old_current_policy != self.current_policy:
                    log.debug(f"🔄 Текущая политика для устройства {target_mac} обновлена на {self.current_policy}")
                    if self.icon:
                        self.icon.menu = self.create_menu()
                if self.current_policy == switch_id:
                    return 'on', None
                else:
                    return 'off', None
        
        # Если устройство не найдено в списке хостов
        err_msg = f"Устройство с MAC {target_mac} не найдено в роутере среди активных хостов"
        log.error(err_msg)
        self.current_state = "host_unavailable"
        return None, err_msg

    def on_lost_policy (self, switch_id):  
        if self.icon and self.icon.HAS_NOTIFICATION and not getattr(self, 'is_lost_policy_notified', False):
            self.is_lost_policy_notified = True
            self.icon.notify(title=f"{APP_NAME_LONG}", 
                         message=f"⚠️ Внимание! Политика {switch_id} ({self.get_switch_name(switch_id)}) не доступна! Возможно он была удалена из роутера. Выберите другую политику в меню или добавьте эту политику обратно в настройки роутера.")

    def switch_policy_state(self, switch_id, desired_state: Literal["on", "off"]):
        """Устанавливает политику в состояние on/off через роутер Keenetic"""

        def send_router_command(payload):
            url = f"{self.config['router_url'].rstrip('/')}/rci/"
            headers = {
                'Content-Type': 'application/json'
            }
            log.debug(f"🛠️ Отправка POST {url}")
            log.debug(f"📤 Payload: {json.dumps(payload, ensure_ascii=False)}")

            response = requests.post(url, headers=headers, json=payload, timeout=5)

            log.debug(f"📥 Ответ: HTTP {response.status_code}")
            log.debug(f"📄 Тело ответа: {response.text}")

            if response.status_code != 200:
                raise requests.exceptions.RequestException(f"HTTP {response.status_code}: {response.text}")
            return response.json()


        mac = self.config['device_mac']
        if desired_state == 'on':
            payload = [
                {"parse": f"ip hotspot host {mac} policy {switch_id}"},
                {"parse": "system configuration save"}
            ]
        else:
            payload = [
                {"parse": f"no ip hotspot host {mac} policy"},
                {"parse": "system configuration save"}
            ]

        try:
            response_json = send_router_command(payload)
            status_list = response_json[0].get("parse", {}).get("status", []) if response_json else []
            request_status = status_list[0].get("status", "") if status_list else ""

            if request_status == "error":
                err_msg = f"Ошибка отправки команды для {switch_id} : \n Сервер вернул 'error' статус\n Проверьте что выбранная политика существует\n в настройках роутера"
                MB.error(APP_NAME_LONG, err_msg, queue_obj=self.main_thread_action_queue)
                return False, err_msg
            
            time.sleep(0.5)
            return True, f'Команды отправлена для {switch_id} успешно'
        except requests.exceptions.RequestException as e:
            return False, f"Ошибка отправки команды для {switch_id} : {str(e)}"

    def on_toggle_action(self, icon=None, item=None):
        """Основное действие переключения (вызывается при клике)"""
        
        # 1. Проверяем, не идет ли уже процесс переключения
        if self.is_switching:
            log.debug("⏳ Повторный клик заблокирован, уже выполняется переключение...")
            return
        self.is_switching = True
        try:
            # 2. Определяем текущее состояние
            switch_id = self.current_switch
            current_state, err_msg = self.get_switch_state_from_router(switch_id)
            
            if current_state is None:
                log.error(f"❌ Не удалось определить состояние роутера: {str(err_msg)}")
                self.current_state = "unknown"
                return
            if self._current_state != current_state:
                log.warning(f"⚠️ Текущее состояние в приложении ({self._current_state}) не совпадает с состоянием на роутере ({current_state}). Обновляем состояние приложения.")
                self.current_state = current_state

            old_state = self._current_state
            desired_state = 'off' if current_state == 'on' else 'on'

            # 3. Отправляем команду на роутер
            success, err_msg = self.switch_policy_state(switch_id, desired_state)
            log.debug(f"{err_msg}")

            if not success:
                self.current_state = old_state
                log.error(f"❌ Ошибка отправки команды: {str(err_msg)}")
                return

            # 4. Команда ушла успешно — ставим промежуточный статус анимации
            self.current_state = "turning_off" if old_state == "on" else "turning_on"

            # 5. Линейный мониторинг изменений
            start_time = time.time()
            timeout = self.config["command_timeout"]
            check_interval = 0.5

            while time.time() - start_time < timeout:
                new_state, _ = self.get_switch_state_from_router(switch_id)
                
                if new_state is not None and new_state != old_state:
                    self.current_state = new_state
                    return  

                time.sleep(check_interval)

            # 6. Обработка таймаута (если из цикла не вышли по return)
            log.error("❌ Истек таймаут ожидания смены состояния на роутере")
            final_state, final_err = self.get_switch_state_from_router(switch_id)
            
            if final_state is not None:
                self.current_state = final_state
            else:
                self.current_state = "unknown"
                log.error(f"⚠️ Не удалось получить финальное состояние: {str(final_err)}")
        finally:
            self.is_switching = False 

    def periodic_state_check(self):
        """Периодическая проверка состояния"""
       
        while self.icon and self.icon._running and not self.periodic_check_thread_stop_event.is_set():

            if self.is_switching:
                log.debug("🔄 Пропуск периодической проверки: роутер сейчас занят переключением")
                self.periodic_check_thread_stop_event.wait(self.config["check_interval"])
                continue
                     
            if self._current_state not in ["turning_on", "turning_off"]:
                if self.current_state == "initializing":
                    start_time = time.time()
                new_state, msg = self.get_switch_state_from_router()
                if self.current_state == "initializing" and time.time() - start_time < 5:
                    time.sleep(5 - (time.time() - start_time))               
                if new_state is not None:
                    # Роутер доступен, обновляем состояние
                    if new_state != self._current_state:
                        self.current_state = new_state
                        switch_name = self.get_switch_name(self.current_switch)
                        if new_state == "on":
                            log.debug(f"Состояние обновлено: {switch_name} ВКЛЮЧЕН")
                        elif new_state == "off":
                            log.debug(f"Состояние обновлено: {switch_name} ВЫКЛЮЧЕН")
                else:
                    # Роутер или политика недоступна
                    log.error(f"Ошибка: {str(msg)}")
                    if self._current_state not in  ["unavailable", "policy_unavailable"]:
                        self.current_state = "policy_unavailable" if msg == 404 else "unavailable"
                        log.debug("Роутер или политика стала недоступна")

            #Проверяем IP монитор
            if self.start_ip_monitor_time and time.time() - self.start_ip_monitor_time < 5:
                # Если мы только что запустили IP монитор, даем ему 5 секунд на инициализацию, прежде чем проверять статус процесса
                self.periodic_check_thread_stop_event.wait(self.config["check_interval"])
                continue
            else:
                self.start_ip_monitor_time = None  
            if self.is_my_ip_tray_running and (self.ip_monitor_process is None or not self.ip_monitor_process.is_alive()):
                self.is_my_ip_tray_running = False
                self.icon.update_menu()
                log.debug("IP монитор был запущен, но процесс не найден. Обновляем статус.")
            self.periodic_check_thread_stop_event.wait(self.config["check_interval"])
            

#####################################################################################################

    def update_icon_state(self, state=None):
        """Обновляет иконку в зависимости от состояния"""      
        if state is None:
            state = self._current_state

        # 1. Маппинг статусов на реальные ключи иконок
        # Здесь мы можем привязать сколько угодно статусов к одной и той же серой иконке
        state_map = {
            "on": "on" if self.current_interface_link in (None, "up") else "on_link_down",
            "off": "off" if self.current_interface_link in (None, "up") else "off_link_down",
            "turning_on": "turning_on",
            "turning_off": "turning_off",
            "unavailable": "unavailable",
            "policy_unavailable": "unavailable",  # Используем ту же серую иконку
            "host_unavailable": "unavailable",  # Если нужно, можно добавить отдельный статус для недоступности хоста
            "initializing": "base_icon"          # Иконка для инициализации
        }
        status_suff = {"on": "ON", 
                     "off": "OFF", 
                     "turning_on": "Включается...", 
                     "turning_off": "Выключается..."}
        
        icon_key = state_map.get(state, "unknown")
        if not self.icon:
            return 

        if icon_key in self.icons:
            self.icon.icon = self.icons[icon_key]

        # 2. Формируем текст подсказки строго на основе входящего статуса (state)
        if state == "unavailable":
            self.icon.title = "⚠️ Роутер недоступен"
        elif state == "initializing":
            self.icon.title = "🔄 Инициализация..."           
        elif state == "policy_unavailable":
            switch_name = self.get_switch_name(self.current_switch)
            self.icon.title = f"⚠️ Ошибка: Политика {switch_name} не доступна!"
        elif state == "host_unavailable":
            self.icon.title = f"⚠️ Ошибка: Устройство с MAC {self.config['device_mac']} не найдено в роутере!"    
        else:
            # Стандартные рабочие статусы            
            status = status_suff.get(state, "Неизвестно")
            switch_name = self.get_switch_name(self.current_switch)
            
            if self.current_interface_name:
                self.icon.title = f"{switch_name} ({self.current_interface_name}): {status}\nLink = {self.current_interface_link}"
            else:
                self.icon.title = f"{switch_name}: {status}"
        self.icon.update_menu()         

    @property
    def current_state(self):
        return self._current_state

    @current_state.setter
    def current_state(self, state):
           self._current_state = state
           self.update_icon_state(state)

    def on_open_settings_gui(self, icon=None, item=None):
        """Открывает окно настроек в отдельном потоке"""
        from settings_gui import SettingsGUI  # Импортируем здесь, чтобы не было проблем при отсутствии GUI
        import tkinter as tk
        # 1. Проверяем, существует ли уже окно настроек
        if hasattr(self, 'settings_root') and self.settings_root is not None:
            try:
                self.settings_root.deiconify()
                self.settings_root.lift()
                self.settings_root.focus_force()
                return  # Окно уже открыто, выходим
            except tk.TclError:
                self.settings_root = None

        def open_gui_thread():
            # Создаем главное окно           
            self.settings_root = tk.Tk()
            
            def on_widget_destroy(event):
                if event.widget == self.settings_root:
                    self.settings_root = None

            self.settings_root.bind("<Destroy>", on_widget_destroy)
            SettingsGUI(self.settings_root, self.config_file)
            self.settings_root.after(100, self.check_queue, self.settings_root)
            self.settings_root.mainloop()

            # После закрытия GUI перезагружаем конфиг
            self.config = self.load_config(self.config_file)

            # Проверяем, что текущий переключатель еще существует
            switches_ids = [s["id"] for s in self.config.get("switches", [])]
            if self.current_switch not in switches_ids:
                self.current_switch = switches_ids[0] if switches_ids else None
                if self.current_switch:
                    self.config["last_switch"] = self.current_switch
            
            # Обновляем меню в иконке
            if self.icon:
                self.icon.menu = self.create_menu()
            self.update_icon_state()
        self.main_thread_action_queue.put((open_gui_thread, ()))
    def on_change_switch(self, switch_id):
        """Изменяет текущий переключатель"""
        if switch_id == self.current_switch:
            return
        
        self.current_switch = switch_id
        self.current_state = "unknown"
        
        # Сохраняем последний использованный переключатель
        self.config["last_switch"] = switch_id
        self.save_config()
        
        # Получаем состояние нового переключателя
        new_state, err_msg = self.get_switch_state_from_router()
        if new_state is not None:
            self.current_state = new_state

        # self.update_current_interface_name()
        self.update_icon_state()

    def create_menu(self):
        """Создает меню иконки в панели задач"""

        # Создаем подменю для выбора политик с чекбоксами
        switch_items = []
        
        for switch in self.config["switches"]:
            switch_id = switch["id"]
            switch_name = switch.get("name", "") or switch_id
            switch_visible = switch.get("enabled", True)    
            switch_items.append(
                pystray.MenuItem(
                    f"{switch_name}",
                    # make_change_handler(switch_id),
                    action=lambda *args, s_id=switch_id: self.on_change_switch(s_id),
                    checked=lambda *args, s_id=switch_id: s_id == self.current_switch,
                    default=switch_id == self.current_policy,
                    visible=switch_visible
                )
            )
       
        switch_menu = pystray.Menu(*switch_items)
        
        # Создаем основные пункты меню
        toggle_item = pystray.MenuItem("🔄 Подкл/откл к политике", self.on_toggle_action, default=True, visible=False)
        switches_item = pystray.MenuItem("📋 Выбрать политику", switch_menu)        
        autorun_item = pystray.MenuItem(
            "🚀 Запускать при старте Windows",
            self.on_toggle_autorun,
            checked=lambda *args: self.config.get("autorun", False)
        )
        show_my_ip_item = pystray.MenuItem(
            "ɪᴘ Включить IP монитор",
            self.on_toggle_ip_monitor,
             checked=lambda *args: self.is_my_ip_tray_running
        )       
        settings_item = pystray.MenuItem("⚙️ Настройки", self.on_open_settings_gui)       
        quit_item = pystray.MenuItem("❌ Выход", self.on_quit)

        # Главное меню
        menu_items = [  toggle_item, 
                        switches_item, 
                        autorun_item, 
                        pystray.Menu.SEPARATOR, 
                        show_my_ip_item, 
                        settings_item,
                        quit_item
                        ]        
        return pystray.Menu(*menu_items)
   
    def on_quit(self, icon, item):
        """Выход из приложения"""
        if self.icon:
            self.icon.visible = False        
        self.main_thread_action_queue.put(("EXIT", 0))  # Отправляем сигнал на выход с кодом 0
    def on_toggle_ip_monitor(self, icon=None, item=None):
        import my_ip_tray
        if not self.is_my_ip_tray_running:
            self.is_my_ip_tray_running = True
            self.start_ip_monitor_time = time.time()
            self.ip_monitor_process = multiprocessing.Process(
                target=my_ip_tray.main,
                args=(self.config.get("ip_check_interval", 60),), 
                daemon=True
            )
            self.ip_monitor_process.start()
        else:
            # МГНОВЕННАЯ И НАДЕЖНАЯ ОСТАНОВКА ИЗ ГЛАВНОГО МЕНЮ
            self.is_my_ip_tray_running = False
            if self.ip_monitor_process and self.ip_monitor_process.is_alive():
                # Убиваем процесс на уровне ОС. 
                self.ip_monitor_process.terminate()
                self.ip_monitor_process.join(timeout=1)

    
    def run(self):
        """Запускает приложение"""
        
        # Создаем иконку
        self.icon = pystray.Icon(name=APP_NAME)
        # Создаем меню
        self.icon.menu = self.create_menu()
        self.current_state = "initializing"

        # Уведомление при запуске
        def notify_start(icon):
            icon.visible = True
           
            # Запускаем периодическую проверку в отдельном потоке
            check_thread = threading.Thread(target=self.periodic_state_check, 
                                        daemon=True)
            check_thread.start()
            log.info("-" * 50, extra={'space': '\n'})
            log.info("Приложение запущено!")
            log.info("-" * 50)
            if icon.HAS_NOTIFICATION:
                icon.notify(title=f"{APP_NAME_LONG}", 
                            message="------------------------\nПриложение запущено!\n\nИконка появилась в системном трее")              

        # Запускаем иконку (из основного потока)
        self.icon.run_detached(setup=notify_start)

        # Цикл основного потока
        exit_code = None       
        try:
            while True:
                try:
                    # Ждем задачу 0.1 сек, чтобы не грузить процессор
                    task = self.main_thread_action_queue.get(timeout=0.1)

                    if not isinstance(task, tuple) or len(task) < 1:
                        log.error(f"⚠️ Ошибка: в очередь главного потока попала неправильная задача: {task}")
                        continue

                    # 1. Проверяем, если пришел кортеж выхода: ("EXIT", код)
                    if task[0] == "EXIT":
                        exit_code = task[1]
                        break
                                                
                    # 2. Безопасная распаковка обычной задачи
                    if len(task) == 2 and callable(task[0]):
                        func, args = task
                        func(*args)
                    else:
                        log.error(f"⚠️ Ошибка: в очередь главного потока попала неправильная задача: {task}")   
                    
                except queue.Empty:
                    # Очередь пуста, уходим на следующую итерацию while True
                    continue
        finally:
            log.info("Выход из приложения")
            self.periodic_check_thread_stop_event.set()
            if self.ip_monitor_process and self.ip_monitor_process.is_alive():
                    self.ip_monitor_process.terminate()
                    self.ip_monitor_process.join(timeout=1.0) # Ждем секунду, чтобы процесс успел закрыть файлы                       
            if self.icon:
                try:
                    self.icon.stop()
                except Exception:
                    pass # Если она уже была остановлена, игнорируем ошибку        
            if exit_code is not None:
                sys.exit(exit_code)

    def check_queue(self, root):
        """Регулярно проверяет очередь внутри цикла Tkinter"""
        # Если окно уже начали закрывать или оно уничтожено, сразу выходим
        if not root.winfo_exists():
            return

        try:
            # Опустошаем ОЧЕРЕДЬ ПОЛНОСТЬЮ за один проход таймера (чинит задержки GUI)
            while True:
                try:
                    task = self.main_thread_action_queue.get_nowait()
                except queue.Empty:
                    break # Очередь пуста, выходим из цикла обработки

                # Гарантируем, что task — это непустой кортеж (защита от краша при проверке индексов)
                if not isinstance(task, tuple) or len(task) < 1:
                    log.error(f"⚠️ Ошибка: в очередь Tkinter попала сломанная задача: {task}")
                    continue

                # 1. Проверяем сигнал выхода
                if task[0] == "EXIT":
                    # Извлекаем код возврата, если он есть
                    exit_code = task[1] if len(task) > 1 else 0
                    root.destroy()
                    # Возвращаем сигнал в очередь для основного цикла run()
                    self.main_thread_action_queue.put(("EXIT", exit_code))
                    return

                # 2. Безопасная распаковка обычной задачи
                if len(task) == 2 and callable(task[0]):
                    func, args = task
                    func(*args)
                else:
                    log.error(f"⚠️ Ошибка: в очередь Tkinter попала неправильная задача: {task}")

        except Exception as e:
            log.error(f"🔥 Критическая ошибка при обработке очереди Tkinter: {e}")

        # Если окно живо, планируем следующую проверку через 100 мс
        if root.winfo_exists():
            root.after(100, self.check_queue, root)

def main():
    # Проверяем наличие конфигурации
    config_file = get_app_path(CONFIG_FILE)

    if not os.path.exists(config_file):
        from settings_gui import SettingsGUI
        import tkinter as tk

        root = tk.Tk()  
        SettingsGUI(root, config_file)  
        root.mainloop()
        # Если пользователь закрыл окно первого запуска, не настроив программу,
        # и файл не появился — прерываем выполнение
        if not os.path.exists(config_file):
            MB.error(APP_NAME_LONG, "Файл конфигурации не создан!\nВозможно вы его не сохранили\nЗапустите программу снова, настройте и сохраните параметры!")
            sys.exit(0)

    # ОСНОВНОЙ РАБОЧИЙ ЦИКЛ ПРИЛОЖЕНИЯ        
    try:
        app = VPNTrayApp(config_file)
        app.run()
    except KeyboardInterrupt:
        log.debug("\n\n👋 Приложение остановлено пользователем")
    except Exception as e:
        log.error(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        log.error("\nПриложение завершается из-за ошибки.")
        sys.exit(1)


from enum import IntEnum
class MB(IntEnum):
    """
    Универсальный класс-контейнер для констант Win32 MessageBoxW
    и кросс-поточного вызова нативных окон Windows.
    """
    
    # Кнопки
    OK               = 0x00000000
    OKCANCEL         = 0x00000001
    YESNO            = 0x00000004
    
    # Иконки
    ICONERROR        = 0x00000010  # Красный крестик (X)
    ICONQUESTION     = 0x00000020  # Синий вопрос (?)
    ICONWARNING      = 0x00000030  # Желтый восклицательный знак (!)
    ICONINFORMATION  = 0x00000040  # Синяя буква (i)
    
    # Модальность и поведение
    TOPMOST          = 0x00040000  # Поверх всех окон на экране

    # Системные коды ответов Windows (для проверки нажатых кнопок)
    IDOK             = 1
    IDCANCEL         = 2
    IDYES            = 6
    IDNO             = 7

    @staticmethod
    def show(title: str, text: str, style: int = 0, queue_obj: queue.Queue | None = None) -> int | None:
        """
        Универсальный метод вызова окна. Автоматически распознает контекст потока.
        
        :param title: Заголовок окна.
        :param text: Текст сообщения.
        :param style: Стили кнопок, иконок и поведения (через битовое ИЛИ '|').
        :param queue_obj: Очередь задач (self.action_queue). Если передана — вызов идет через очередь.
        :return: Код нажатой кнопки (int) при прямом вызове, либо None при вызове через очередь.
        """
        import ctypes
        # Если тип иконки вообще не задан в маске флагов (0x000000F0), 
        # по умолчанию подставляем синюю информационную иконку (i)
        if not (style & 0x000000F0):
            style |= MB.ICONINFORMATION

        # СЦЕНАРИЙ 1: Очередь передана (Вызов из фонового потока)
        if queue_obj is not None and threading.current_thread() != threading.main_thread():
            # Создаем внутреннюю функцию-колбэк для главного потока
            def trigger_box():
                ctypes.windll.user32.MessageBoxW(0, text, title, style)
            
            # Ставим показ окна в очередь главного потока и выходим
            queue_obj.put((trigger_box, ()))
            return None

        # СЦЕНАРИЙ 2: Очередь не передана (Вызов из Главного потока или функции main)
        return ctypes.windll.user32.MessageBoxW(0, text, title, style)

    @staticmethod
    def error(title: str, text: str, style: int = 0, queue_obj: queue.Queue | None = None) -> int | None:
        """
        Специализированный метод для показа критических ошибок.
        Принудительно вшивает красный крестик (ICONERROR) и вывод поверх всех окон (TOPMOST).
        """
        full_style = MB.OK | MB.ICONERROR | MB.TOPMOST | style
        return MB.show(title, text, full_style, queue_obj)

    @staticmethod
    def ask_yes_no(title: str, text: str, style: int = 0) -> bool:
        """
        Удобный метод для вопросов Да/Нет (вызывается строго в Главном потоке, 
        так как требует мгновенного синхронного ответа от пользователя).
        """
        import ctypes
        full_style = MB.YESNO | MB.ICONQUESTION | style
        result = ctypes.windll.user32.MessageBoxW(0, text, title, full_style)
        return result == MB.IDYES

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
