import json
import os
import shutil
import sys
import time
import threading
import io
import requests
import ctypes
from PIL import Image
import pystray
from settings_gui import log, get_app_path, get_data_path
from build import IP_TRAY_ENDPOINTS

def load_config(endpoints_file=IP_TRAY_ENDPOINTS):
    """Загружает конфигурацию из JSON файла. 
    Если файла нет рядом с EXE, копирует его из ресурсов, иначе возвращает дефолт.
    """
    # 1. Сразу формируем два четких абсолютных пути
    external_path = get_app_path(endpoints_file) # Путь РЯДОМ с exe
    internal_path = get_data_path(endpoints_file) # Путь ВНУТРИ ресурсов exe

    # 2. Первая попытка: файл уже существует на диске рядом с EXE
    if os.path.exists(external_path):
        try:
            with open(external_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Ошибка чтения {external_path}: {e}")
            # Возвращаем дефолт, если файл поврежден, чтобы не ломать запуск
            return {"endpoints": [], "flag_providers": []}
            
    # 3. Вторая попытка: внешнего файла нет, но он есть внутри ресурсов
    if os.path.exists(internal_path):
        try:
            shutil.copy(internal_path, external_path)
            log.info(f"Файл {endpoints_file} успешно извлечен из ресурсов в {external_path}")
            
            with open(external_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Не удалось извлечь файл из ресурсов: {e}")

    # 4. Резервная конфигурация по умолчанию (fallback), если файла нет нигде
    return {
        "endpoints": [],
        "flag_providers": []
    }

CONFIG = load_config()


# Потокобезопасное хранилище данных приложения
state = {
    "current_ip": None,
    "country_code": "US",
    "country": None,
    "region": None,
    "ip_info_text": "Инициализация мониторинга...",
    "icon_image": Image.new("RGBA", (24, 24), (128, 128, 128, 255)), # Серый квадрат
    "current_flags_provider": None,
    "current_service_index": 0,
    "ip_check_interval": 60,
}

def fetch_ip_info():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }   
    endpoints = CONFIG.get("endpoints", [])
    if not endpoints:
        log.error("Список endpoints пуст в конфигурации.")
        return None

    total_services = len(endpoints)
    # Начинаем опрос с того индекса, который был успешным в прошлый раз
    start_index = state["current_service_index"]

    for i in range(total_services):
        # Рассчитываем текущий индекс со смещением «по кругу»
        check_index = (start_index + i) % total_services
        service = endpoints[check_index]
        
        try:
            log.debug(f"Отправка запроса url: {service["url"]}")
            response = requests.get(service["url"], headers=headers, timeout=6)
            if response.status_code == 200:
                log.debug(f"Ответ получен от {service["url"]} успешно")
                data = response.json()

                if data and data.get(service["code_key"]):
                    # Запоминаем этот индекс как успешный для следующей итерации через минуту
                    state["current_service_index"] = check_index
                    fmt_text = service["fmt_string"].format(**{k: data.get(k, "") for k in data})
                    return {
                        "ip": data.get(service["ip_key"]),
                        "code": data.get(service["code_key"]),
                        "country": data.get(service["country_key"]),
                        "region": data.get(service["region_key"]),
                        "text": fmt_text
                    }
            else:
                log.warning(f"{service["url"]} Ошибка сети: HTTP {response.status_code}")
            
        except requests.exceptions.Timeout as e:
            log.warning(f"{service["url"]} Таймаут подключения: {e}")                   
            continue
        except requests.exceptions.ConnectionError as e:
            log.warning(f"{service["url"]} Ошибка подключения: {e}")
            continue
        except requests.exceptions.RequestException as e:
            log.warning(f"{service["url"]} Ошибка запроса: {str(e)}")
            continue
        except ValueError as e:
            log.warning(f"{service["url"]} Ошибка парсинга JSON: {str(e)}")
            continue           
    return None


# --- 2. ПУЛ СЕРВИСОВ ДЛЯ СКАЧИВАНИЯ ФЛАГОВ (FALLBACK) ---
def fetch_flag_image(country_code):
    code_upper = country_code.upper()
    code_lower = country_code.lower()
    flag_providers = CONFIG.get("flag_providers", [])

    for provider  in flag_providers:
        try:
            url = provider["url_template"].format(code_upper=code_upper, code_lower=code_lower)
            log.debug(f"Попытка загрузить флаг из {provider['name']} по url: {url}")
            response = requests.get(url, timeout=4)
            if response.status_code == 200:
                img = Image.open(io.BytesIO(response.content))
                state["current_flags_provider"] = provider["name"]
                return img.resize((24, 24), Image.Resampling.LANCZOS)
        except Exception:
            continue
    state["current_flags_provider"] = None
    # Заглушка (синий квадрат), если пропали оба CDN-сервиса с флагами
    return Image.new("RGBA", (24, 24), (0, 0, 255, 255))

# --- 3. НАВЕДЕНИЕ МЫШИ И ОБРАБОТКА КЛИКА ---
def show_native_win_box(icon, item):
    """Вызывает полностью изолированное нативное окно Windows через ctypes"""
    # Запуск окна в отдельном системном потоке, чтобы полностью исключить фризы трея
    threading.Thread(
        target=lambda: ctypes.windll.user32.MessageBoxW(
            0, 
            state["ip_info_text"], 
            "Информация о текущем IP", 
            0x40 | 0x0  # 0x40 - иконка "Информация" (i), 0x0 - только кнопка ОК
        ),
        daemon=True
    ).start()

# --- 4. ЦИКЛ ДЛЯ ФОНОВОГО МОНИТОРИНГА ---
def monitor_loop(icon):
    while True:
        info = fetch_ip_info()
        
        if info:
            ip_changed = info["ip"] != state["current_ip"]
            flag_missing = state.get("current_flags_provider") is None
            
            if ip_changed or flag_missing:
                state["current_ip"] = info["ip"]
                state["country_code"] = info["code"]
                state["country"] = info["country"]
                state["region"] = info["region"]
                           
                state["icon_image"] = fetch_flag_image(info["code"])
                
                provider = state.get('current_flags_provider') or 'отсутствует'
                state["ip_info_text"] = f"{info['text']}\nФлаг провайдер: {provider}\nИнтервал опроса: {state['ip_check_interval']} сек."
    
                icon.icon = state["icon_image"]
                icon.title = (
                    f"Текущий IP: {state.get('current_ip', 'unknown')}\n"
                    f"Страна: {state.get('country', 'unknown')}\n"
                    f"Регион: {state.get('region', 'unknown')}\n"
                    "Нажмите для полной информации"
                )
            
        else: 
            icon.title = "Ошибка: Нет связи с IP-сервисами"
            state["ip_info_text"] = "Не удалось получить информацию о IP. Проверьте подключение к интернету."
            state["icon_image"] = Image.new("RGBA", (24, 24), (255, 0, 0, 255))  # Красный квадрат
            icon.icon = state["icon_image"]
            state["current_ip"] = ""  # Сброс текущего IP при ошибке получения данных
            state["current_flags_provider"] = None

        time.sleep(state["ip_check_interval"])


def on_exit(icon, item):
    icon.stop()

# --- 5. ЗАПУСК ПРИЛОЖЕНИЯ ---
def main(ip_check_interval = 60):
    state["ip_check_interval"] = ip_check_interval
    icon = pystray.Icon(
        "ip_monitor", 
        state["icon_image"], 
        title="Запуск мониторинга IP...",
        menu=pystray.Menu(
            # default=True вешает вызов нативного окна на двойной клик по иконке флага
            pystray.MenuItem("Показать полную информацию", show_native_win_box, default=True),
            pystray.MenuItem("Выход", on_exit)
        )
    )
    
    monitor_thread = threading.Thread(target=monitor_loop, args=(icon,), daemon=True)
    monitor_thread.start()
    icon.run()

if __name__ == "__main__":
    main(10)
