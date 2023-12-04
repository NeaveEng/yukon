# SPDX-FileCopyrightText: 2023 Christopher Parrott for Pimoroni Ltd
#
# SPDX-License-Identifier: MIT

from .common import YukonModule, ADC_HIGH, IO_LOW, IO_HIGH
from machine import Pin
from ucollections import OrderedDict
from pimoroni_yukon.errors import FaultError, OverTemperatureError
import pimoroni_yukon.logging as logging


class DualMotorModule(YukonModule):
    NAME = "Dual Motor"
    NUM_MOTORS = 2
    MOTOR_1 = 0
    MOTOR_2 = 1
    FAULT_THRESHOLD = 0.1
    DEFAULT_FREQUENCY = 25000
    TEMPERATURE_THRESHOLD = 70.0

    # The current (in amps) associated with each limit (Do Not Modify!)
    CURRENT_LIMIT_1 = 0.161
    CURRENT_LIMIT_2 = 0.251
    CURRENT_LIMIT_3 = 0.444
    CURRENT_LIMIT_4 = 0.786
    CURRENT_LIMIT_5 = 1.143
    CURRENT_LIMIT_6 = 1.611
    CURRENT_LIMIT_7 = 1.890
    CURRENT_LIMIT_8 = 2.153
    CURRENT_LIMIT_9 = 2.236
    MIN_CURRENT_LIMIT = CURRENT_LIMIT_1
    DEFAULT_CURRENT_LIMIT = CURRENT_LIMIT_3
    MAX_CURRENT_LIMIT = CURRENT_LIMIT_9

    # | ADC1  | ADC2  | SLOW1 | SLOW2 | SLOW3 | Module               | Condition (if any)          |
    # |-------|-------|-------|-------|-------|----------------------|-----------------------------|
    # | HIGH  | ALL   | 0     | 0     | 1     | Dual Motor           |                             |
    @staticmethod
    def is_module(adc1_level, adc2_level, slow1, slow2, slow3):
        return adc1_level == ADC_HIGH and slow1 is IO_LOW and slow2 is IO_LOW and slow3 is IO_HIGH

    def __init__(self, frequency=DEFAULT_FREQUENCY, current_limit=DEFAULT_CURRENT_LIMIT, init_motors=True):
        super().__init__()
        self.__frequency = frequency
        self.__current_limit = current_limit
        self.__init_motors = init_motors

        # An ascending order list of current limits with the pin states to achieve them
        self.__current_limit_states = OrderedDict({
            self.CURRENT_LIMIT_1: (0, 0),
            self.CURRENT_LIMIT_2: (-1, 0),
            self.CURRENT_LIMIT_3: (0, -1),
            self.CURRENT_LIMIT_4: (1, 0),
            self.CURRENT_LIMIT_5: (-1, -1),
            self.CURRENT_LIMIT_6: (0, 1),
            self.CURRENT_LIMIT_7: (1, -1),
            self.CURRENT_LIMIT_8: (-1, 1),
            self.CURRENT_LIMIT_9: (1, 1),
        })

    def initialise(self, slot, adc1_func, adc2_func):
        # Store the pwm pins
        pins_p = (slot.FAST2, slot.FAST4)
        pins_n = (slot.FAST1, slot.FAST3)

        if self.__init_motors:
            from motor import Motor

            # Create motor objects
            self.motors = [Motor((pins_p[i], pins_n[i]), freq=self.__frequency) for i in range(len(pins_p))]

        if not self.__init_motors:
            self.motor_pins = [(pins_p[i], pins_n[i]) for i in range(len(pins_p))]

        # Create motor control pin objects
        self.__motors_en = slot.SLOW3
        self.__motors_vref1 = slot.SLOW1
        self.__motors_vref2 = slot.SLOW2

        # Pass the slot and adc functions up to the parent now that module specific initialisation has finished
        super().initialise(slot, adc1_func, adc2_func)

    def reset(self):
        if self.__init_motors:
            for motor in self.motors:
                motor.disable()

        self.__motors_en.init(Pin.OUT, value=False)
        self.set_current_limit(self.__current_limit)

    def enable(self):
        self.__motors_en.value(True)

    def disable(self):
        self.__motors_en.value(False)

    def is_enabled(self):
        return self.__motors_en.value() == 1

    def current_limit(self):
        return self.__current_limit

    def set_current_limit(self, amps):
        if self.is_enabled():
            raise RuntimeError("Cannot change current limit whilst motor driver is active")

        # Start with the lowest limit
        chosen_limit = self.MIN_CURRENT_LIMIT
        chosen_state = self.__current_limit_states[chosen_limit]

        # Find the closest current limit below the given amps value
        for limit, state in self.__current_limit_states.items():
            if limit > amps:
                break
            chosen_limit = limit
            chosen_state = state

        if chosen_state[0] == -1:
            self.__motors_vref1.init(Pin.IN)
        elif chosen_state[0] == 0:
            self.__motors_vref1.init(Pin.OUT, value=False)
        else:
            self.__motors_vref1.init(Pin.OUT, value=True)

        if chosen_state[1] == -1:
            self.__motors_vref2.init(Pin.IN)
        elif chosen_state[1] == 0:
            self.__motors_vref2.init(Pin.OUT, value=False)
        else:
            self.__motors_vref2.init(Pin.OUT, value=True)

        self.__current_limit = chosen_limit

        logging.info(self.__message_header() + f"Current limit set to {self.__current_limit}A")

    @property
    def motor1(self):
        if self.__init_motors:
            return self.motors[0]
        raise RuntimeError("motor1 is only accessible if init_motors was True during initialisation")

    @property
    def motor2(self):
        if self.__init_motors:
            return self.motors[1]
        raise RuntimeError("motor2 is only accessible if init_motors was True during initialisation")

    def read_fault(self):
        return self.__read_adc1() <= self.FAULT_THRESHOLD

    def read_temperature(self, samples=1):
        return self.__read_adc2_as_temp(samples)

    def monitor(self):
        fault = self.read_fault()
        if fault is True:
            raise FaultError(self.__message_header() + "Fault detected on motor driver! Turning off output")

        temperature = self.read_temperature()
        if temperature > self.TEMPERATURE_THRESHOLD:
            raise OverTemperatureError(self.__message_header() + f"Temperature of {temperature}°C exceeded the limit of {self.TEMPERATURE_THRESHOLD}°C! Turning off output")

        # Run some user action based on the latest readings
        if self.__monitor_action_callback is not None:
            self.__monitor_action_callback(fault, temperature)

        self.__fault_triggered = self.__fault_triggered or fault
        self.__max_temperature = max(temperature, self.__max_temperature)
        self.__min_temperature = min(temperature, self.__min_temperature)
        self.__avg_temperature += temperature
        self.__count_avg += 1

    def get_readings(self):
        return OrderedDict({
            "Fault": self.__fault_triggered,
            "T_max": self.__max_temperature,
            "T_min": self.__min_temperature,
            "T_avg": self.__avg_temperature,
        })

    def process_readings(self):
        if self.__count_avg > 0:
            self.__avg_temperature /= self.__count_avg
            self.__count_avg = 0    # Clear the count to prevent process readings acting more than once

    def clear_readings(self):
        self.__fault_triggered = False
        self.__max_temperature = float('-inf')
        self.__min_temperature = float('inf')
        self.__avg_temperature = 0
        self.__count_avg = 0
