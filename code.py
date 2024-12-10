import adafruit_datetime
import adafruit_lis3dh
import adafruit_requests
import board
from adafruit_matrixportal.matrixportal import MatrixPortal
import adafruit_imageload
import displayio
from external_rtc import ExternalRTC
import os
import wifi
import socketpool
import ssl
import time
import supervisor

supervisor.runtime.autoreload = False

# noinspection PyBroadException
try:
    from typing import Optional
except:
    pass

class API:
    def __init__(self):
        self.requests: Optional[adafruit_requests.Session] = None
        self.init_requests()

    @staticmethod
    def to_datetime(as_str: str) -> adafruit_datetime.datetime:
        # workaround for https://github.com/adafruit/Adafruit_CircuitPython_datetime/issues/22
        if as_str[-1] == "Z":
            print(f"Warning: applying workaround for unsupported datetime format {as_str}")
            as_str = as_str[:-1] + "-00:00"
        return adafruit_datetime.datetime.fromisoformat(as_str)

    def init_requests(self) -> None:
        ssid = os.getenv("CIRCUITPY_WIFI_SSID")
        password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
        timeout = os.getenv("CIRCUITPY_WIFI_TIMEOUT")
        timeout = int(timeout) if timeout else 10

        print(f"Connecting to {ssid}...")
        wifi.radio.connect(ssid = ssid, password = password, timeout = timeout)
        pool = socketpool.SocketPool(wifi.radio)
        # noinspection PyTypeChecker
        requests = adafruit_requests.Session(pool, ssl.create_default_context())
        print("Connected!")

        self.requests = requests

    def get(self, url: str):
        api_key = os.getenv("BABYBUDDY_API_KEY")
        full_url = os.getenv("BABYBUDDY_API_URL") + url
        print(f"GET {full_url}")
        response = self.requests.get(
            url = full_url,
            headers = {
                "Authorization": f"Token {api_key}"
            }
        )

        return response.json()

    def get_last_sleep(self) -> Optional[adafruit_datetime.datetime]:
        sleeps = self.get("sleep/?limit=1")
        if len(sleeps["results"]) == 0:
            return None

        return API.to_datetime(sleeps["results"][0]["end"])

    def get_last_feeding(self) -> tuple[Optional[adafruit_datetime.datetime], Optional[str]]:
        feedings = self.get("feedings/?limit=1")
        if len(feedings["results"]) == 0:
            return None, None

        last_feeding = feedings["results"][0]

        method = None
        if last_feeding["method"] == "bottle":
            method = "B"
        elif last_feeding["method"] == "right breast":
            method = "R"
        elif last_feeding["method"] == "left breast":
            method = "L"
        elif last_feeding["method"] == "both breasts":
            method = "RL"

        return API.to_datetime(last_feeding["start"]), method

    def get_last_changes(self) -> tuple[Optional[adafruit_datetime.datetime], Optional[adafruit_datetime]]:
        last_peed = None
        last_pooped = None

        changes = self.get("changes/?limit=20")
        for change in changes["results"]:
            if last_peed is None and change["wet"]:
                last_peed = API.to_datetime(change["time"])
            if last_pooped is None and change["solid"]:
                last_pooped = API.to_datetime(change["time"])

            if last_peed is not None and last_pooped is not None:
                break

        return last_peed, last_pooped

    def get_current_timer(self) -> tuple[Optional[adafruit_datetime.datetime], int]:
        timers = self.get("timers/?limit=1")

        if len(timers["results"]) == 0:
            return None, UI.NO_TIMER

        timer = timers["results"][0]

        timer_started = API.to_datetime(timer["start"])
        timer_type = UI.NO_TIMER

        if timer["name"] is not None:
            name = timer["name"].lower()
            if "feeding" in name:
                timer_type = UI.FEEDING_TIMER
            elif "sleep" in name:
                timer_type = UI.SLEEP_TIMER

        if timer_type == UI.NO_TIMER:
            return None, UI.NO_TIMER

        return timer_started, timer_type

class UI:
    def __init__(self, matrixportal: MatrixPortal, rtc: ExternalRTC):
        self.active_sleep_icon = None
        self.active_feeding_icon = None
        self.inactive_feeding_icon = None
        self.inactive_sleep_icon = None
        self.matrixportal = matrixportal
        self.rtc = rtc
        self.icon_tile_grids = {}
        self.init_components()

        self.update_label(UI.FEEDING, "", 0x440000)
        self.update_label(UI.SLEEP, "", 0x440000)
        self.update_label(UI.LAST_PEED, "", 0x004455)
        self.update_label(UI.LAST_POOPED, "", 0x554400)

    def update(self,
        last_feeding: Optional[adafruit_datetime.datetime],
        last_feeding_method: Optional[str],
        last_sleep: Optional[adafruit_datetime.datetime],
        last_peed: Optional[adafruit_datetime.datetime],
        last_pooped: Optional[adafruit_datetime.datetime],
        current_timer_started: Optional[adafruit_datetime.datetime],
        current_timer_type: Optional[int]
    ):
        is_feeding_timer_running = current_timer_started is not None and current_timer_type == UI.FEEDING_TIMER
        is_sleep_timer_running = current_timer_started is not None and current_timer_type == UI.SLEEP_TIMER

        self.active_feeding_icon.hidden = not is_feeding_timer_running
        self.inactive_feeding_icon.hidden = is_feeding_timer_running
        self.active_sleep_icon.hidden = not is_sleep_timer_running
        self.inactive_sleep_icon.hidden = is_sleep_timer_running

        if is_feeding_timer_running:
            last_feeding_str = self.delta_to_str(current_timer_started)
        else:
            last_feeding_str = self.delta_to_str(last_feeding)
            if last_feeding_method is not None:
                last_feeding_str = " " + last_feeding_method

        self.update_label(UI.FEEDING, last_feeding_str, 0x444444 if is_feeding_timer_running else 0x440000)

        self.update_label(
            UI.SLEEP,
            self.delta_to_str(current_timer_started if is_sleep_timer_running else last_sleep),
            0x444444 if is_sleep_timer_running else 0x440000
        )

        self.update_label(UI.LAST_PEED, self.delta_to_str(last_peed, spaces = False))
        self.update_label(UI.LAST_POOPED, self.delta_to_str(last_pooped, spaces = False))

    def init_components(self) -> None:
        self.init_labels()
        self.init_icons()

    def init_labels(self) -> None:
        self.matrixportal.add_text(
            text_font = "/assets/big.bdf",
            text_position = (self.matrixportal.display.width - 1, -1),
            text_anchor_point = (1, 0)
        )

        self.matrixportal.add_text(
            text_font = "/assets/big.bdf",
            text_position = (self.matrixportal.display.width - 1, 12),
            text_anchor_point = (1, 0)
        )

        self.matrixportal.add_text(
            text_font = "/assets/small.bdf",
            text_position = (0, self.matrixportal.display.height - 2),
            text_anchor_point = (0, 1)
        )

        self.matrixportal.add_text(
            text_font = "/assets/small.bdf",
            text_position = (self.matrixportal.display.width + 1, self.matrixportal.display.height - 2),
            text_anchor_point = (1, 1)
        )

    def init_icons(self) -> None:
        self.active_feeding_icon = self.init_icon(path = "/assets/feeding-active.bmp", y = 2)
        self.inactive_feeding_icon = self.init_icon(path = "/assets/feeding-inactive.bmp", y = 2)
        self.active_sleep_icon = self.init_icon(path = "/assets/sleep-active.bmp", y = 15)
        self.inactive_sleep_icon = self.init_icon(path = "/assets/sleep-inactive.bmp", y = 15)

    def init_icon(self, path: str, y: int) -> displayio.TileGrid:
        bitmap, palette = adafruit_imageload.load(path)
        tile_grid = displayio.TileGrid(bitmap, pixel_shader = palette)
        tile_grid.x = 2
        tile_grid.y = y
        tile_grid.hidden = True
        self.matrixportal.display.root_group.append(tile_grid)
        return tile_grid

    def update_label(self, label_index: int, text: str, color: Optional[int] = None) -> None:
        self.matrixportal.set_text(text, label_index)
        if color is not None:
            self.matrixportal.set_text_color(color, label_index)

    def delta_to_str(self, datetime: adafruit_datetime.datetime, show_zero_hours: bool = False, spaces: bool = True) -> str:
        if datetime is None:
            return "-"

        now = self.rtc.now()
        delta = now - datetime

        # noinspection PyUnresolvedReferences
        delta_seconds = delta.seconds + (delta.days * 60 * 60 * 24)

        if delta_seconds < 60:
            return ("0h 0m" if spaces else "0h0m") if show_zero_hours else "0m"

        if delta_seconds < 60 * 60:
            label = f"{delta_seconds // 60}m"
            if show_zero_hours:
                label = ("0h " if spaces else "0h") + label

            return label

        hours = delta_seconds // 60 // 60
        minutes = delta_seconds // 60 % 60
        return f"{hours}h {minutes}m" if spaces else f"{hours}h{minutes}m"


api = API()

rtc = ExternalRTC(board.I2C())
rtc.sync(api.requests)

UI.FEEDING = 0
UI.SLEEP = 1
UI.LAST_PEED = 2
UI.LAST_POOPED = 3

UI.NO_TIMER = 0
UI.FEEDING_TIMER = 1
UI.SLEEP_TIMER = 2

matrixportal = MatrixPortal(color_order = "RBG")
matrixportal.display.rotation = 180
ui = UI(matrixportal, rtc)

last_rtc_update = rtc.now()

accelerometer = adafruit_lis3dh.LIS3DH_I2C(i2c =board.I2C(), address = 0x19)

while True:
    try:
        # noinspection PyUnresolvedReferences
        if (rtc.now() - last_rtc_update).seconds > (12 * 60 * 60):
            print("RTC sync")
            rtc.sync(api.requests)
            last_rtc_update = rtc.now()

        last_feeding, method = api.get_last_feeding()
        last_peed, last_pooped = api.get_last_changes()
        timer_started, timer_type = api.get_current_timer()
        last_sleep = api.get_last_sleep()

        matrixportal.display.auto_refresh = False
        ui.update(
            last_feeding = last_feeding,
            last_feeding_method = method,
            last_sleep = last_sleep,
            last_peed = last_peed,
            last_pooped = last_pooped,
            current_timer_started = timer_started,
            current_timer_type = timer_type
        )
        matrixportal.display.auto_refresh = True
    except Exception as e:
        import traceback
        traceback.print_exception(e)
    finally:
        now = time.monotonic()
        end = now + 20
        while now < end:
            x, y, z = accelerometer.acceleration
            now = time.monotonic()

            if y > 8 and matrixportal.display.rotation != 0: # right-side up
                matrixportal.display.rotation = 0
            elif y < -8 and matrixportal.display.rotation != 180: # upside down
                matrixportal.display.rotation = 180

        time.sleep(20)
