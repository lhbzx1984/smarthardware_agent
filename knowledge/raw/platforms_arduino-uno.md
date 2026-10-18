<!-- source: https://www.cirkitdesigner.com/docs/platforms/arduino-uno -->

- [/docs/](/docs/)
- [/docs/platforms/](/docs/platforms/) Supported MCUs
- Arduino Uno
On this page
# Arduino Uno Simulation
The Arduino Uno is the most popular board in the Arduino family. It is powered by the ATmega328p chip, which has 32KB of Flash program memory, 2KB of SRAM, and 1KB of EEPROM.
## Getting Started[#getting-started](#getting-started) ​
Place an Arduino Uno on the canvas and open the code editor by clicking on the Code tab. Write your Arduino sketch using the standard Arduino API.
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
Pins 0–13 are digital GPIO pins. Pins A0–A5 double as analog input pins in addition to being digital GPIO pins.
Digital pins 3, 5, 6, 9, 10, and 11 have hardware PWM support.
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
| 0 | Serial (USART) | RX
| 1 | Serial (USART) | TX
| 2 | External interrupt | INT0
| 3 | External interrupt | INT1
| 10 | SPI | SS (Chip select)
| 11 | SPI | MOSI
| 12 | SPI | MISO
| 13 | SPI | SCLK (Clock)
| A4 | I2C | SDA (Data)
| A5 | I2C | SCL (Clock)
### On-Board LED[#on-board-led](#on-board-led) ​
The built-in LED on digital pin 13 is simulated. Use LED_BUILTIN to control it:
pinMode(LED_BUILTIN, OUTPUT);
digitalWrite(LED_BUILTIN, HIGH);
## Simulation Features[#simulation-features](#simulation-features) ​
| | Feature | Status | Notes
| Processor (ATmega328p) | ✔️ |
| GPIO | ✔️ | Including external and pin change interrupts
| 8-bit Timers | ✔️ | Timer0, Timer2
| 16-bit Timer | ✔️ | Timer1
| PWM | ✔️ | Pins 3, 5, 6, 9, 10, 11 — used by analogWrite(), Servo, tone
| ADC | ✔️ | analogRead() on pins A0–A5
| USART | ✔️ | Used by Serial
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
The Arduino Uno runs at a 16 MHz clock frequency, matching real hardware.
## Practical Limitations[#practical-limitations](#practical-limitations) ​
- SPI and I2C are master mode only — slave mode is not supported
- No analog comparator — if your project uses the analog comparator, it cannot be simulated at this time
- Timing may differ slightly from real hardware — most Arduino code is unaffected
