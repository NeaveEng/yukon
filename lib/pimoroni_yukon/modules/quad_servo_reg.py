# SPDX-FileCopyrightText: 2023 Christopher Parrott for Pimoroni Ltd
#
# SPDX-License-Identifier: MIT

from .common import YukonModule, ADC_HIGH, IO_LOW, IO_HIGH
from machine import Pin
from servo import Servo
from ucollections import OrderedDict
from pimoroni_yukon.errors import FaultError, OverTemperatureError
import pimoroni_yukon.logging as logging


class QuadServoRegModule(YukonModule):
    NAME = "Quad Servo Regulated"
    SERVO_1 = 0
    SERVO_2 = 1
    SERVO_3 = 2
    SERVO_4 = 3
    NUM_SERVOS = 4
    TEMPERATURE_THRESHOLD = 80.0

    # | ADC1  | ADC2  | SLOW1 | SLOW2 | SLOW3 | Module               | Condition (if any)          |
    # |-------|-------|-------|-------|-------|----------------------|-----------------------------|
    # | HIGH  | ALL   | 0     | 1     | 0     | Quad Servo Regulated | Power Not Good              |
    # | HIGH  | ALL   | 0     | 1     | 1     | Quad Servo Regulated | Power Good                  |
    @staticmethod
    def is_module(adc1_level, adc2_level, slow1, slow2, slow3):
        return adc1_level == ADC_HIGH and slow1 is IO_LOW and slow2 is IO_HIGH

    def __init__(self, init_servos=True, halt_on_not_pgood=False):
        super().__init__()
        self.__init_servos = init_servos
        self.halt_on_not_pgood = halt_on_not_pgood

        self.__last_pgood = False

    def initialise(self, slot, adc1_func, adc2_func):
        # Store the pwm pins
        pins = (slot.FAST1, slot.FAST2, slot.FAST3, slot.FAST4)

        if self.__init_servos:
            # Create servo objects
            self.servos = [Servo(pins[i], freq=50) for i in range(len(pins))]
        else:
            self.servo_pins = pins

        # Create the power control pin objects
        self.__power_en = slot.SLOW1
        self.__power_good = slot.SLOW3

        # Pass the slot and adc functions up to the parent now that module specific initialisation has finished
        super().initialise(slot, adc1_func, adc2_func)

    def reset(self):
        if self.__init_servos:
            for servo in self.servos:
                servo.disable()

        self.__power_en.init(Pin.OUT, value=False)
        self.__power_good.init(Pin.IN)

    def enable(self):
        self.__power_en.value(True)

    def disable(self):
        self.__power_en.value(False)

    def is_enabled(self):
        return self.__power_en.value() == 1

    @property
    def servo1(self):
        if self.__init_servos:
            return self.servos[0]
        raise RuntimeError("servo1 is only accessible if init_servos was True during initialisation")

    @property
    def servo2(self):
        if self.__init_servos:
            return self.servos[1]
        raise RuntimeError("servo2 is only accessible if init_servos was True during initialisation")

    @property
    def servo3(self):
        if self.__init_servos:
            return self.servos[2]
        raise RuntimeError("servo3 is only accessible if init_servos was True during initialisation")

    @property
    def servo4(self):
        if self.__init_servos:
            return self.servos[3]
        raise RuntimeError("servo4 is only accessible if init_servos was True during initialisation")

    def read_power_good(self):
        return self.__power_good.value() == 1

    def read_temperature(self):
        return self.__read_adc2_as_temp()

    def monitor(self):
        pgood = self.read_power_good()
        if pgood is not True:
            if self.halt_on_not_pgood:
                raise FaultError(self.__message_header() + "Power is not good! Turning off output")

        temperature = self.read_temperature()
        if temperature > self.TEMPERATURE_THRESHOLD:
            raise OverTemperatureError(self.__message_header() + f"Temperature of {temperature}°C exceeded the limit of {self.TEMPERATURE_THRESHOLD}°C! Turning off output")

        if self.__last_pgood is True and pgood is not True:
            logging.warn(self.__message_header() + "Power is not good")
        elif self.__last_pgood is not True and pgood is True:
            logging.warn(self.__message_header() + "Power is good")

        # Run some user action based on the latest readings
        if self.__monitor_action_callback is not None:
            self.__monitor_action_callback(pgood, temperature)

        self.__last_pgood = pgood
        self.__power_good_throughout = self.__power_good_throughout and pgood

        self.__max_temperature = max(temperature, self.__max_temperature)
        self.__min_temperature = min(temperature, self.__min_temperature)
        self.__avg_temperature += temperature
        self.__count_avg += 1

    def get_readings(self):
        return OrderedDict({
            "PGood": self.__power_good_throughout,
            "T_max": self.__max_temperature,
            "T_min": self.__min_temperature,
            "T_avg": self.__avg_temperature
        })

    def process_readings(self):
        if self.__count_avg > 0:
            self.__avg_temperature /= self.__count_avg
            self.__count_avg = 0    # Clear the count to prevent process readings acting more than once

    def clear_readings(self):
        self.__power_good_throughout = True
        self.__max_temperature = float('-inf')
        self.__min_temperature = float('inf')
        self.__avg_temperature = 0
        self.__count_avg = 0
