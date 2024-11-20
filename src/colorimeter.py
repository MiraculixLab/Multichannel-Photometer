import gc
import time
import ulab
import board
import analogio
import digitalio
import gamepadshift
import constants
import adafruit_itertools

from light_sensor import LightSensor
from light_sensor import LightSensorOverflow
from light_sensor import LightSensorIOError

from battery_monitor import BatteryMonitor

from configuration import Configuration
from configuration import ConfigurationError

from calibrations import Calibrations
from calibrations import CalibrationsError

from menu_screen import MenuScreen
from message_screen import MessageScreen
from multi_measure_screen import MultiMeasureScreen

class Mode:
    MEASURE = 0
    MENU = 1
    MESSAGE = 2
    ABORT = 3

class Colorimeter:
    ABOUT_STR = 'About'
    RAW_SENSOR_STR = 'Raw Sensor'
    ABSORBANCE_STR = 'Absorbance'
    TRANSMITTANCE_STR = 'Transmittance'

    DEFAULT_MEASUREMENTS = [ABSORBANCE_STR, TRANSMITTANCE_STR, RAW_SENSOR_STR]

    def __init__(self):
        self.menu_screen = None
        self.message_screen = MessageScreen()
        self.measure_screen = None
        self._mode = Mode.MEASURE  # Korrekte Initialisierung des internen Attributs
        board.DISPLAY.brightness = 1.0

        # Initialize menu items using the class attribute DEFAULT_MEASUREMENTS
        self.menu_items = list(self.DEFAULT_MEASUREMENTS)
        self.menu_view_pos = 0
        self.menu_item_pos = 0
        self.is_blanked = False
        self.blank_values = ulab.numpy.ones((constants.NUM_CHANNEL,))

        # Setup gamepad inputs
        self.last_button_press = time.monotonic()
        self.pad = gamepadshift.GamePadShift(
            digitalio.DigitalInOut(board.BUTTON_CLOCK),
            digitalio.DigitalInOut(board.BUTTON_OUT),
            digitalio.DigitalInOut(board.BUTTON_LATCH),
        )

        # Load Configuration
        self.configuration = Configuration()
        try:
            self.configuration.load()
        except ConfigurationError as error:
            self.message_screen.set_message(error)
            self.message_screen.set_to_error()
            self.mode = Mode.MESSAGE  # Verwende self.mode statt mode

        # Load calibrations and populate menu items
        self.calibrations = Calibrations()
        try:
            self.calibrations.load()
        except CalibrationsError as error:
            self.message_screen.set_message(error)
            self.message_screen.set_to_error()
            self.mode = Mode.MESSAGE  # Verwende self.mode
        else:
            if self.calibrations.has_errors:
                error_msg = 'errors found in calibrations file'
                self.message_screen.set_message(error_msg)
                self.message_screen.set_to_error()
                self.mode = Mode.MESSAGE  # Verwende self.mode

        # Extend menu with calibration data
        self.menu_items.extend([k for k in self.calibrations.data])
        self.menu_items.append(self.ABOUT_STR)

        # Set default/startup measurement
        if self.configuration.startup in self.menu_items:
            self.measurement_name = self.configuration.startup
        else:
            if self.configuration.startup is not None:
                error_msg = f'startup measurement {self.configuration.startup} not found'
                self.message_screen.set_message(error_msg)
                self.message_screen.set_to_error()
                self.mode = Mode.MESSAGE  # Verwende self.mode
            self.measurement_name = self.menu_items[0]

        # Setup light sensor and preliminary blanking
        try:
            self.light_sensor = LightSensor()
        except LightSensorIOError as error:
            error_msg = f'missing sensor? {error}'
            self.message_screen.set_message(error_msg, ok_to_continue=False)
            self.message_screen.set_to_abort()
            self.mode = Mode.ABORT  # Verwende self.mode
        else:
            if self.configuration.gain is not None:
                self.light_sensor.gain = self.configuration.gain
            self.blank_sensor(set_blanked=False)

        # Setup battery monitoring settings cycles
        self.battery_monitor = BatteryMonitor()
        self.setup_menu_cycles()

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, new_mode):
        self.delete_screens()
        if new_mode == Mode.MEASURE:
            self.measure_screen = MultiMeasureScreen()
        elif new_mode in (Mode.MESSAGE, Mode.ABORT):
            self.message_screen = MessageScreen()
        elif new_mode == Mode.MENU:
            self.menu_screen = MenuScreen()
            self.menu_view_pos = 0
            self.menu_item_pos = 0
            self.update_menu_screen()
        gc.collect()
        self._mode = new_mode


    def setup_menu_cycles(self):
        self.gain_cycle = adafruit_itertools.cycle(constants.GAIN_TO_STR)
        if self.configuration.gain is not None:
            while next(self.gain_cycle) != self.configuration.gain:
                continue

    def delete_screens(self):
        self.message_screen = None
        self.measure_screen = None
        self.menu_screen = None
        gc.collect()

    def update_menu_screen(self):
        if self.menu_screen is None:
            return

        n0 = self.menu_view_pos
        n1 = n0 + self.menu_screen.items_per_screen
        view_items = []

        for i, item in enumerate(self.menu_items[n0:n1]):
            if item in self.DEFAULT_MEASUREMENTS or item == self.ABOUT_STR:
                item_text = f'{n0 + i} {item}'
            else:
                try:
                    led = self.calibrations.led(item)
                    chan = self.calibrations.channel(item)

                    if led is None and chan is None:
                        item_text = f'{n0 + i} {item}'
                    elif chan is None:
                        item_text = f'{n0 + i} {item} ({led})'
                    elif led is None:
                        chan_str = constants.CHANNEL_TO_STR[chan]
                        item_text = f'{n0 + i} {item} ({chan_str})'
                    else:
                        chan_str = constants.CHANNEL_TO_STR[chan]
                        item = item[:8]
                        item_text = f'{n0 + i} {item} ({led},{chan_str})'
                except KeyError:
                    item_text = f'{n0 + i} {item}'

            view_items.append(item_text)

        self.menu_screen.set_menu_items(view_items)
        pos = self.menu_item_pos - self.menu_view_pos
        self.menu_screen.set_curr_item(pos)
    # Remaining methods unchanged...

    @property
    def is_absorbance(self):
        return self.measurement_name == self.ABSORBANCE_STR

    @property
    def is_transmittance(self):
        return self.measurement_name == self.TRANSMITTANCE_STR

    @property
    def is_raw_sensor(self):
        return self.measurement_name == self.RAW_SENSOR_STR

    @property
    def is_calibrated_measurement(self):
        return self.measurement_name not in self.DEFAULT_MEASUREMENTS

    @property
    def measurement_units(self):
        if self.measurement_name in self.DEFAULT_MEASUREMENTS:
            units = None
        else:
            units = self.calibrations.units(self.measurement_name)
        return units

    @property
    def raw_sensor_values(self):
        return self.light_sensor.raw_values

    @property
    def transmittances(self):
        transmittances = self.raw_sensor_values / self.blank_values
        mask = transmittances > 1.0
        transmittances[mask] = 1.0
        return transmittances

    @property
    def absorbances(self):
        transmittances = self.transmittances
        if transmittances is None:
            return None
        absorbances = -ulab.numpy.log10(transmittances)
        mask = absorbances < 0.0
        absorbances[mask] = 0.0
        return absorbances

    @property
    def measurement_values(self):
        if self.is_absorbance:
            values = self.absorbances
        elif self.is_transmittance:
            values = self.transmittances
        elif self.is_raw_sensor:
            values = self.raw_sensor_values
        elif self.is_calibrated_measurement:
            if self.measurement_name == "PSILOCYBIN":
                deviations = self.calibrations.calculate_deviations(
                    self.measurement_name, 
                    self.absorbances
                )
                return deviations
            else:
                values = [self.calibrations.apply(self.measurement_name, a) for a in self.absorbances if a is not None]
        else:
            values = None
        return values

    def blank_sensor(self, set_blanked=True):
        num_samples = constants.NUM_BLANK_SAMPLES
        blank_samples = ulab.numpy.zeros((num_samples, constants.NUM_CHANNEL))
        for i in range(num_samples):
            try:
                blank_samples[i, :] = self.light_sensor.raw_values
            except Exception as e:
                print(f"Error during blanking: {e}")
                blank_samples[i, :] = ulab.numpy.ones(constants.NUM_CHANNEL)
            time.sleep(constants.BLANK_DT)

        self.blank_values = ulab.numpy.median(blank_samples, axis=0)
        self.blank_values = ulab.numpy.where(self.blank_values > 0, self.blank_values, 1.0)
        if set_blanked:
            self.is_blanked = True

    def blank_button_pressed(self, buttons):
        return buttons & constants.BUTTON['blank'] and not self.is_raw_sensor

    def menu_button_pressed(self, buttons):
        return buttons & constants.BUTTON['menu']

    def up_button_pressed(self, buttons):
        return buttons & constants.BUTTON['up']

    def down_button_pressed(self, buttons):
        return


    def right_button_pressed(self, buttons):
        return buttons & constants.BUTTON['right']

    def channel_button_pressed(self, buttons):
        return buttons & constants.BUTTON['left']

    def gain_button_pressed(self, buttons):
        return buttons & constants.BUTTON['gain'] if self.is_raw_sensor else False

    def itime_button_pressed(self, buttons):
        return buttons & constants.BUTTON['itime'] if self.is_raw_sensor else False

    def handle_button_press(self):
        buttons = self.pad.get_pressed()
        if not buttons:
            return 
        if not self.check_debounce():
            return  

        self.last_button_press = time.monotonic()

        if self.mode == Mode.MEASURE:
            if self.blank_button_pressed(buttons):
                self.measure_screen.set_blanking()
                self.blank_sensor()
            elif self.menu_button_pressed(buttons):
                self.mode = Mode.MENU
            elif self.gain_button_pressed(buttons):
                self.light_sensor.gain = next(self.gain_cycle)
                self.is_blanked = False
            elif self.itime_button_pressed(buttons):
                pass

        elif self.mode == Mode.MENU:
            if self.menu_button_pressed(buttons):
                self.mode = Mode.MEASURE
            elif self.up_button_pressed(buttons): 
                self.decr_menu_item_pos()
            elif self.down_button_pressed(buttons): 
                self.incr_menu_item_pos()
            elif self.right_button_pressed(buttons): 
                selected_item = self.menu_items[self.menu_item_pos]
                if selected_item == self.ABOUT_STR:
                    about_msg = f'firmware version {constants.__version__}'
                    self.mode = Mode.MESSAGE
                    self.message_screen.set_message(about_msg) 
                    self.message_screen.set_to_about()
                else:
                    self.measurement_name = self.menu_items[self.menu_item_pos]
                    self.mode = Mode.MEASURE
            self.update_menu_screen()

        elif self.mode == Mode.MESSAGE:
            if self.calibrations.has_errors:
                error_msg = self.calibrations.pop_error()
                self.message_screen.set_message(error_msg)
                self.message_screen.set_to_error()
                self.mode = Mode.MESSAGE
            else:
                if self.menu_button_pressed(buttons):
                    self.mode = Mode.MENU
                else:
                    self.mode = Mode.MEASURE

    def check_debounce(self):
        button_dt = time.monotonic() - self.last_button_press
        return button_dt >= constants.DEBOUNCE_DT

    def run(self):
        while True:
            self.handle_button_press()

            if self.mode == Mode.MEASURE:
                try:
                    self.measure_screen.set_measurement(
                        self.measurement_name, 
                        self.measurement_units, 
                        self.measurement_values,
                        self.light_sensor.CHANNEL_NAMES,
                        self.configuration.precision,
                    )
                except LightSensorOverflow:
                    self.measure_screen.set_overflow(self.measurement_name)

                self.battery_monitor.update()
                battery_voltage = self.battery_monitor.voltage_lowpass
                self.measure_screen.set_battery(battery_voltage)

                if self.is_blanked:
                    self.measure_screen.set_blanked()
                else:
                    self.measure_screen.set_not_blanked()

                self.measure_screen.set_gain(self.light_sensor.gain)
                self.measure_screen.show()

            elif self.mode == Mode.MENU:
                self.menu_screen.show()

            elif self.mode in (Mode.MESSAGE, Mode.ABORT):
                self.message_screen.show()

            gc.collect()
            time.sleep(constants.LOOP_DT)
