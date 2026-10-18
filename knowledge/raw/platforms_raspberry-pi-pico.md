<!-- source: https://www.cirkitdesigner.com/docs/platforms/raspberry-pi-pico -->

- [/docs/](/docs/)
- [/docs/platforms/](/docs/platforms/) Supported MCUs
- Raspberry Pi Pico
On this page
# Raspberry Pi Pico Simulation
The Raspberry Pi Pico is an RP2040 microcontroller board with a dual-core ARM Cortex-M0+ processor, 264KB of internal RAM, and flexible Programmable I/O (PIO). It runs at 125 MHz.
## Getting Started[#getting-started](#getting-started) ​
Place a Raspberry Pi Pico on the canvas and open the code editor by clicking on the Code tab. Write your Arduino sketch using the standard Arduino API.
void setup() {
Serial.begin(115200);
while (!Serial) {
delay(10); // wait for USB serial connection
}
Serial.println("Hello, Pico!");
pinMode(LED_BUILTIN, OUTPUT);
}
void loop() {
digitalWrite(LED_BUILTIN, HIGH);
delay(500);
digitalWrite(LED_BUILTIN, LOW);
delay(500);
}
note
USB Serial setup takes a moment. Use while (!Serial) in setup() to wait for the connection before printing, or messages may be lost.
## Pin Layout[#pin-layout](#pin-layout) ​
Pins GP0–GP28 are digital GPIO pins. GP26, GP27, and GP28 also support analog input.
### Analog Input Pins[#analog-input-pins](#analog-input-pins) ​
| | Pin | Analog Channel
| GP26 | 0
| GP27 | 1
| GP28 | 2
### Power Pins[#power-pins](#power-pins) ​
The simulator exposes the following power pins:
| | Pin | Voltage
| VBUS | 5V
| VSYS | 5V
| 3V3 | 3.3V
| GND | 0V
| ADC_VREF | 3.3V
### Special Pin Functions[#special-pin-functions](#special-pin-functions) ​
| | Pin | Function | Signal
| GP0 | UART0 | TX
| GP1 | UART0 | RX
| GP4 | I2C0 | SDA (Data)
| GP5 | I2C0 | SCL (Clock)
| GP16 | SPI0 | MISO (RX)
| GP18 | SPI0 | SCK (Clock)
| GP19 | SPI0 | MOSI (TX)
| GP25 | On-board LED | LED_BUILTIN
### On-Board LED[#on-board-led](#on-board-led) ​
The Raspberry Pi Pico has an on-board LED on GPIO pin 25. Use LED_BUILTIN to control it:
pinMode(LED_BUILTIN, OUTPUT);
digitalWrite(LED_BUILTIN, HIGH);
## Simulation Features[#simulation-features](#simulation-features) ​
| | Feature | Status | Notes
| Processor (ARM Cortex-M0+) | ✔️ | Single core simulated
| GPIO | ✔️ | GP0–GP28
| UART | ✔️ | Used by Serial1
| USB Serial | ✔️ | Used by Serial (USB CDC)
| I2C | 🟡 | Master mode only
| SPI | 🟡 | Master mode only
| PWM | ✔️ |
| ADC | ✔️ | 3 channels (GP26, GP27, GP28)
| Timers | ✔️ |
| Watchdog | ✔️ |
| PIO | ✔️ | Programmable I/O state machines
| DMA | 🟡 | PIO peripheral only
| RTC | ✔️ |
Legend:
- ✔️ Supported
- 🟡 Partial support
- ❌ Not yet supported
## Serial Monitor[#serial-monitor](#serial-monitor) ​
The Pi Pico supports two serial interfaces:
- Serial — communicates over USB (CDC). This is the default for the Serial Monitor.
- Serial1 — communicates over UART0 (GP0 TX, GP1 RX).
USB Serial requires a brief setup time. Always wait for the connection in setup():
void setup() {
Serial.begin(115200);
while (!Serial) {
delay(10);
}
Serial.println("Connected!");
}
## Simulation Speed[#simulation-speed](#simulation-speed) ​
The Raspberry Pi Pico runs at a 125 MHz clock frequency, matching real hardware.
## Practical Limitations[#practical-limitations](#practical-limitations) ​
- Single core only — only one of the two ARM Cortex-M0+ cores is simulated
- SPI and I2C are master mode only — slave mode is not supported
- DMA is limited to PIO — DMA for other peripherals is not yet supported
- Timing may differ slightly from real hardware — most Arduino code is unaffected
