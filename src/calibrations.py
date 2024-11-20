import os
import ulab
import json
import constants
from collections import OrderedDict
from json_settings_file import JsonSettingsFile
import math  # Verwende math für isnan() und isinf()
from message_screen import MessageScreen

class CalibrationsError(Exception):
    pass

class Calibrations(JsonSettingsFile):

    FILE_TYPE = 'calibrations'
    FILE_NAME = constants.CALIBRATIONS_FILE
    LOAD_ERROR_EXCEPTION = CalibrationsError
    ALLOWED_FIT_TYPES = ['linear', 'polynomial']

    def __init__(self):
        self.menu_screen = None
        self.message_screen = MessageScreen()  # Stellt sicher, dass message_screen initialisiert ist
        self.measure_screen = None
        self.mode = Mode.MEASURE
        board.DISPLAY.brightness = 1.0

    def check(self):
        for name, calibration in self.data.items():
            error_list = []
            error_list.extend(self.check_fit(name, calibration))
            
            # Holen Sie `fit_type` aus `calibration` und übergeben es an `check_range`
            fit_type = calibration.get('fit_type')
            error_list.extend(self.check_range(name, calibration, fit_type))
            
            error_list.extend(self.check_channel(name, calibration))
            if error_list:
                self.error_dict[name] = error_list

        for name in self.error_dict:
            del self.data[name]

    def check_fit(self, name, calibration): 
        error_list = []
        try:
            fit_type = calibration['fit_type']
        except KeyError:
            fit_type = None
            error_msg = f'{name} missing fit_type'
            error_list.append(error_msg)
        else:
            if fit_type not in self.ALLOWED_FIT_TYPES:
                error_msg = f'{name} unknown fit_type {fit_type}'
                error_list.append(error_msg)
        try:
            fit_coef = calibration['fit_coef']
        except KeyError:
            fit_coef = None
            error_msg = f'{name} missing fit_coef' 
            error_list.append(error_msg)
        else:
            try:
                fit_coef = ulab.numpy.array(fit_coef)
            except (ValueError, TypeError):
                error_msg = f'{name} fit coeff format incorrect'
                error_list.append(error_msg)
        if fit_type == 'linear' and fit_coef.size > 2:
            error_msg = f'{name} too many fit_coef for linear fit'
            error_list.append(error_msg)
        return error_list

    def check_range(self, name, calibration, fit_type=None):
        min_value = None
        max_value = None
        error_list = []

        try:
            range_data = calibration['range']
        except KeyError:
            # Verwenden Sie `fit_type`, das beim Aufruf an die Funktion übergeben wurde
            if fit_type != 'linear':
                error_msg = f'{name} range data missing'
                error_list.append(error_msg)
        else:
            if not isinstance(range_data, dict):
                error_msg = f'range_data must be dict'
                error_list.append(error_msg)
                return error_list

            try:
                min_value = float(range_data['min'])
            except KeyError:
                error_msg = f'{name} range min missing'
                error_list.append(error_msg)
            except (ValueError, TypeError): 
                error_msg = f'{name} range min not float' 
                error_list.append(error_msg)

            try:
                max_value = float(range_data['max'])
            except KeyError:
                error_msg = f'{name} range max missing'
                error_list.append(error_msg)
            except (ValueError, TypeError): 
                error_msg = f'{name} range max not float' 
                error_list.append(error_msg)

            if min_value is not None and max_value is not None:
                if min_value >= max_value:
                    error_msg = f'{name} range min > max'
                    error_list.append(error_msg)

        return error_list

    def check_channel(self, name, calibration):
        error_list = []
        try:
            channel = calibration['channel']
        except KeyError:
            pass
        else:
            if channel not in range(0, constants.NUM_CHANNEL):
                error_msg = f'channel {channel} not allowed'
                error_list.append(error_msg)
        return error_list

    def led(self, name):
        return self.data[name].get('led')

    def units(self, name):
        return self.data[name].get('units')

    def channel(self, name): 
        return self.data[name].get('channel')

    def apply(self, name, absorbance_dict):
        # absorbance_dict ist ein Dictionary mit Kanalnamen als Schlüssel und Absorptionswerten als Werten
        concentrations = {}
        calibration_data = self.data[name]
        channels = calibration_data.get('channels', {})
        
        for channel_name, channel_data in channels.items():
            fit_type = channel_data.get('fit_type', 'linear')
            fit_coef = ulab.numpy.array(channel_data.get('fit_coef', [1, 0]))
            absorbance = absorbance_dict.get(channel_name, None)
            if absorbance is not None:
                if fit_type == 'linear':
                    # Konzentration = (Absorbance - intercept) / slope
                    slope = fit_coef[0]
                    intercept = fit_coef[1]
                    if slope != 0:
                        concentration = (absorbance - intercept) / slope
                    else:
                        concentration = None
                else:
                    concentration = None
                # Überprüfen, ob die Konzentration im gültigen Bereich liegt
                range_min = channel_data.get('range', {}).get('min', None)
                range_max = channel_data.get('range', {}).get('max', None)
                if concentration is not None:
                    if (range_min is not None and concentration < range_min) or \
                    (range_max is not None and concentration > range_max):
                        concentration = None
                concentrations[channel_name] = concentration
        return concentrations

    def get_expected_ratios(self, name):
        """Retrieve the expected channel absorbance ratios for a given substance."""
        return self.data[name].get('expected_ratios', {})

    def calculate_deviations(self, name, absorbances):
        """Calculate percentage deviation for each channel from expected ratios in ascending order."""
        deviations = {}
        expected_ratios = self.get_expected_ratios(name)

        # Map each absorbance to its channel name in ascending order, including 910nm and Clear
        channel_names = ["415nm", "445nm", "480nm", "515nm", "555nm", "590nm", "630nm", "680nm", "910nm", "Clear"]
        absorbance_dict = {ch: ab for ch, ab in zip(channel_names, absorbances)}

        # Use 590nm as the baseline for deviation calculations
        baseline_absorbance = absorbance_dict.get("590nm", None)

        # Only proceed if the baseline is valid
        if baseline_absorbance is None or baseline_absorbance == 0 or math.isnan(baseline_absorbance) or math.isinf(baseline_absorbance):
            print("Warning: Baseline (590nm) absorbance missing, zero, or infinite. Deviations cannot be calculated accurately.")
            return {"error": "Baseline missing, zero, or infinite"}

        # Calculate deviations based on the baseline and expected ratios
        for channel in channel_names:
            if channel in expected_ratios:
                expected_ratio = expected_ratios[channel]
                if channel in absorbance_dict:
                    measured_ratio = absorbance_dict[channel] / baseline_absorbance
                    # Check for inf or NaN values
                    if math.isnan(measured_ratio) or math.isinf(measured_ratio):
                        deviations[channel] = "N/A"  # Set "N/A" if the value is invalid
                    else:
                        deviation = ((measured_ratio - expected_ratio) / expected_ratio) * 100
                        # Round to 1 decimal if under 10%, else no decimals
                        deviations[channel] = round(deviation, 1) if abs(deviation) < 10 else round(deviation)

        return deviations
