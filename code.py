import adafruit_datetime
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
        response = self.requests.get(
			url = os.getenv("BABYBUDDY_API_URL") + url,
			headers = {
				"Authorization": f"Token {api_key}"
			}
		)

        return response.json()

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

        return adafruit_datetime.datetime.fromisoformat(last_feeding["start"]), method

    def get_last_changes(self) -> tuple[Optional[adafruit_datetime.datetime], Optional[adafruit_datetime]]:
        last_peed = None
        last_pooped = None

        changes = self.get("changes/?limit=20")
        for change in changes["results"]:
            if last_peed is None and change["wet"]:
                last_peed = adafruit_datetime.datetime.fromisoformat(change["time"])
            if last_pooped is None and change["solid"]:
                last_pooped = adafruit_datetime.datetime.fromisoformat(change["time"])

            if last_peed is not None and last_pooped is not None:
                break

        return last_peed, last_pooped

    def get_current_timer(self) -> tuple[Optional[adafruit_datetime.datetime], int]:
        timers = self.get("timers/?limit=1")

        if len(timers["results"]) == 0:
            return None, UI.NO_TIMER

        timer = timers["results"][0]

        timer_started = adafruit_datetime.datetime.fromisoformat(timer["start"])
        timer_type = UI.GENERIC_TIMER

        if timer["name"] is not None:
            name = timer["name"].lower()
            if "feeding" in name:
                timer_type = UI.FEEDING_TIMER
            elif "sleep" in name:
                timer_type = UI.SLEEP_TIMER
            else:
                timer_type = UI.GENERIC_TIMER

        return timer_started, timer_type

class UI:
    def __init__(self, matrixportal: MatrixPortal, rtc: ExternalRTC):
        self.matrixportal = matrixportal
        self.rtc = rtc
        self.icon_tile_grids = {}
        self.init_components()

        self.update_label(UI.LAST_FEEDING, "", 0xFF0000)
        self.update_label(UI.TIMER, "", 0xFFFFFF)
        self.update_label(UI.LAST_PEED, "", 0x0099FF)
        self.update_label(UI.LAST_POOPED, "", 0xFFAA66)

    def init_components(self) -> None:
        self.init_labels()
        self.init_icons()

    def init_labels(self) -> None:
        self.matrixportal.add_text(
            text_font = "/big.bdf",
            text_position = (self.matrixportal.display.width // 2, 0),
            text_anchor_point = (0.5, 0)
        )

        self.matrixportal.add_text(
            text_font = "/big.bdf",
            text_position = (self.matrixportal.display.width - 7, 12),
            text_anchor_point = (1, 0)
        )

        self.matrixportal.add_text(
            text_font = "/small.bdf",
            text_position = (0, self.matrixportal.display.height - 1),
            text_anchor_point = (0, 1)
        )

        self.matrixportal.add_text(
            text_font = "/small.bdf",
            text_position = (self.matrixportal.display.width + 1, self.matrixportal.display.height - 1),
            text_anchor_point = (1, 1)
        )

    def init_icons(self) -> None:
        icons = {
            UI.GENERIC_TIMER: "/timer.bmp",
            UI.FEEDING_TIMER: "/feeding.bmp",
            UI.SLEEP_TIMER: "/sleep.bmp"
        }

        for index, bmp_path in icons.items():
            bitmap, palette = adafruit_imageload.load(bmp_path)
            tile_grid = displayio.TileGrid(bitmap, pixel_shader = palette)
            tile_grid.x = 7
            tile_grid.y = 15
            tile_grid.hidden = True
            self.matrixportal.display.root_group.append(tile_grid)
            self.icon_tile_grids[index] = tile_grid

    def update_label(self, label_index: int, text: str, color: Optional[int] = None) -> None:
        self.matrixportal.set_text(text, label_index)
        if color is not None:
            self.matrixportal.set_text_color(color, label_index)

    def delta_to_str(self, datetime: adafruit_datetime.datetime, show_zero_hours: bool = False, spaces: bool = True) -> str:
        now = self.rtc.now()
        delta = now - datetime

        # noinspection PyUnresolvedReferences
        delta_seconds = delta.seconds

        if delta_seconds < 60:
            return ("0h 0m" if spaces else "0h0m") if show_zero_hours else "0m"
        elif delta_seconds < 60 * 60:
            label = f"{delta_seconds // 60}m"
            if show_zero_hours:
                label = ("0h " if spaces else "0h") + label

            return label
        else:
            hours = delta_seconds // 60 // 60
            minutes = delta_seconds // 60 % 60
            return f"{hours}h {minutes}m" if spaces else f"{hours}h{minutes}m"

    def update_timer(self, timer_started: Optional[adafruit_datetime.datetime], timer_type: int = 0) -> None:
        if timer_started is None:
            timer_type = UI.NO_TIMER

        for index, tile_grid in self.icon_tile_grids.items():
            hidden = (index != timer_type) or timer_type == UI.NO_TIMER
            tile_grid.hidden = hidden

        if timer_started is None:
            label = ""
        else:
            label = self.delta_to_str(timer_started)

        self.update_label(UI.TIMER, label)

    def update_last_feeding(self, last_feeding: Optional[adafruit_datetime.datetime], method: Optional[str]) -> None:
        label = "No feedings"
        if last_feeding is not None:
            label = self.delta_to_str(last_feeding, show_zero_hours = True)
            if method is not None:
                label += " " + method

        self.update_label(UI.LAST_FEEDING,label)

    def update_last_peed(self, last_peed: Optional[adafruit_datetime.datetime]) -> None:
        self.update_label(
            UI.LAST_PEED,
            "No data" if last_peed is None else self.delta_to_str(last_peed, spaces = False)
        )

    def update_last_pooped(self, last_pooped: Optional[adafruit_datetime.datetime]) -> None:
        self.update_label(
            UI.LAST_POOPED,
            "No data" if last_pooped is None else self.delta_to_str(last_pooped, spaces = False)
        )

api = API()

rtc = ExternalRTC(board.I2C())
rtc.sync(api.requests)

UI.LAST_FEEDING = 0
UI.TIMER = 1
UI.LAST_PEED = 2
UI.LAST_POOPED = 3

UI.NO_TIMER = 0
UI.FEEDING_TIMER = 1
UI.SLEEP_TIMER = 2,
UI.GENERIC_TIMER = 3

matrixportal = MatrixPortal(color_order = "RBG")
ui = UI(matrixportal, rtc)

last_rtc_update = rtc.now()

while True:
    try:
        if (rtc.now() - last_rtc_update).seconds > (24 * 60 * 60):
            print("RTC sync")
            rtc.sync(api.requests)
            last_rtc_update = rtc.now()

        last_feeding, method = api.get_last_feeding()
        last_peed, last_pooped = api.get_last_changes()
        timer_started, timer_type = api.get_current_timer()

        matrixportal.display.auto_refresh = False
        ui.update_last_feeding(last_feeding, method)
        ui.update_timer(timer_started, timer_type)
        ui.update_last_peed(last_peed)
        ui.update_last_pooped(last_pooped)
        matrixportal.display.auto_refresh = True
    except Exception as e:
        print(e)
    finally:
        time.sleep(20)