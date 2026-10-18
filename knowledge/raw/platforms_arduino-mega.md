<!-- source: https://www.cirkitdesigner.com/docs/platforms/arduino-mega -->

- [/docs/](/docs/)
- [/docs/platforms/](/docs/platforms/) Supported MCUs
- Arduino Mega 2560
On this page
# Arduino Mega 2560 Simulation
The Arduino Mega 2560 is powered by the ATmega2560 chip, which has 256KB of Flash program memory, 8KB of SRAM, and 4KB of EEPROM. The board features 54 digital pins, 16 analog input pins, and 4 serial ports. It runs at 16 MHz.
## Getting Started[#getting-started](#getting-started) ​
Place an Arduino Mega 2560 on the canvas and open the code editor by clicking on the Code tab. The Mega is code-compatible with the Uno — most Arduino sketches work without modification.
void setup() {
pinMode(LED_BUILTIN, OUTPUT);
}
void loop() {
digitalWrite(LED_BUILTIN, HIGH);
delay(500);
digitalWrite(LED_BUILTIN, LOW);
delay(500);
}
## Pin Layout[#pin-layout](#pin-layout) ​
Pins 0–53 are digital GPIO pins. Pins A0–A15 double as analog input pins in addition to being digital GPIO pins.
Digital pins 2–13, 44, 45, and 46 have hardware PWM support (15 PWM channels total).
### Power Pins[#power-pins](#power-pins) ​
The simulator exposes the following power pins:
| | Pin | Voltage
| 5V | 5V
| 3.3V | 3.3V
| GND | 0V
| Vin | 9V
| IOREF | 5V
### Special Pin Functions[#special-pin-functions](#special-pin-functions) ​
| | Pin | Function | Signal
| 0 | Serial (USART0) | RX
| 1 | Serial (USART0) | TX
| 2 | External interrupt | INT4
| 3 | External interrupt | INT5
| 14 | Serial3 | TX
| 15 | Serial3 | RX
| 16 | Serial2 | TX
| 17 | Serial2 | RX
| 18 | Serial1 | TX
| 19 | Serial1 | RX
| 20 | I2C | SDA (Data)
| 21 | I2C | SCL (Clock)
| 50 | SPI | MISO
| 51 | SPI | MOSI
| 52 | SPI | SCK (Clock)
| 53 | SPI | SS (Chip select)
### On-Board LED[#on-board-led](#on-board-led) ​
The built-in LED on digital pin 13 is simulated. Use LED_BUILTIN to control it:
pinMode(LED_BUILTIN, OUTPUT);
digitalWrite(LED_BUILTIN, HIGH);
## Simulation Features[#simulation-features](#simulation-features) ​
| | Feature | Status | Notes
| Processor (ATmega2560) | ✔️ |
| GPIO | ✔️ | Including external and pin change interrupts
| 8-bit Timers | ✔️ | Timer0, Timer2
| 16-bit Timers | ✔️ | Timer1, Timer3, Timer4, Timer5. Input capture not supported
| PWM | ✔️ | Pins 2–13, 44, 45, 46 — used by analogWrite(), Servo, tone
| ADC | ✔️ | analogRead() on pins A0–A15 (16 channels)
| USART | ✔️ | 4 serial ports — Serial, Serial1, Serial2, Serial3
| SPI | 🟡 | Master mode only
| I2C | 🟡 | Master mode only
| EEPROM | ✔️ |
| Watchdog Timer | ✔️ |
| Analog Comparator | ❌ | Not yet supported
Legend:
- ✔️ Supported
- 🟡 Partial support
- ❌ Not yet supported
## Simulation Speed[#simulation-speed](#simulation-speed) ​
The Arduino Mega 2560 runs at a 16 MHz clock frequency, matching real hardware.
## Practical Limitations[#practical-limitations](#practical-limitations) ​
- SPI and I2C are master mode only — slave mode is not supported
- No analog comparator — if your project uses the analog comparator, it cannot be simulated at this time
- Timing may differ slightly from real hardware — most Arduino code is unaffected
