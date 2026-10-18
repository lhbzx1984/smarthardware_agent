<!-- source: https://www.cirkitdesigner.com/docs/platforms/esp32-s3 -->

- [/docs/](/docs/)
- [/docs/platforms/](/docs/platforms/) Supported MCUs
- ESP32-S3
On this page
# ESP32-S3 Simulation
The ESP32-S3 is a WiFi-enabled dual-core microcontroller from Espressif, widely used for IoT projects. Cirkit Designer supports Arduino sketch projects on the ESP32-S3.
note
ESP-IDF project support in Cirkit Designer is coming soon.
## Getting Started[#getting-started](#getting-started) ​
Place an ESP32-S3 development board on the canvas and open the code editor by clicking on the Code tab. Write your Arduino sketch using familiar APIs like Serial, digitalWrite, analogRead, and WiFi libraries.
#include <Arduino.h>
void setup() {
Serial.begin(9600);
pinMode(2, OUTPUT);
}
void loop() {
digitalWrite(2, HIGH);
delay(500);
digitalWrite(2, LOW);
delay(500);
}
## Simulation Features[#simulation-features](#simulation-features) ​
| | Feature | Status | Notes
| Processor (Dual-core Xtensa) | ✔️ |
| GPIO | ✔️ | Digital I/O, interrupts, pull-up/pull-down resistors, peripheral routing (IOMUX)
| UART | ✔️ | 3 channels — used by Serial, Serial1, Serial2
| SPI | ✔️ | Full-duplex with DMA. Works with standard SPI library and SPI devices
| I2C | ✔️ | 2 controllers — works with Wire library and I2C sensors/displays
| Timers | ✔️ | Hardware timers, millis(), delay(), alarm scheduling
| LEDC PWM | ✔️ | Used by analogWrite(), Servo, tone/buzzer
| ADC | ✔️ | analogRead() with oneshot and continuous/DMA sampling
| WiFi | ✔️ | HTTP, HTTPS, MQTT, WebSocket, UDP. Open networks only — see [/docs/guides/wifi-networking](/docs/guides/wifi-networking) WiFi Networking guide
| RMT | 🟡 | Transmit only — NeoPixel / WS2812 LED strips work. Receive not yet supported
| AES Accelerator | ✔️ | Hardware-accelerated AES encryption/decryption with DMA support
| SHA Accelerator | ✔️ | Hardware-accelerated SHA hashing with DMA support
| RSA Accelerator | ✔️ | Hardware-accelerated RSA operations
| RNG | ✔️ | Hardware random number generator
| RTC | ✔️ | Sleep modes not simulated
| Flash / PSRAM | ✔️ | Standard firmware flash and PSRAM access
| Watchdog Timers | ✔️ | MWDT and RWDT with standard ESP-IDF/Arduino usage
| USB Serial/JTAG | 🟡 | Serial TX output works. RX input not fully exposed
| Bluetooth | ❌ | Not yet supported
| PCNT | ❌ | Not yet supported
| TWAI / CAN | ❌ | Not yet supported
Legend:
- ✔️ Supported
- 🟡 Partial support
- ❌ Not yet supported
## Simulation Speed[#simulation-speed](#simulation-speed) ​
To keep the simulation running in real time, Cirkit Designer limits the simulated CPU frequency. This does not affect peripheral timing — millis(), delay(), UART baud rates, and timer intervals all remain accurate. It only limits how fast instructions execute, which matters for compute-heavy loops.
| | Setting | Description
| 8 MHz (default) | Fastest simulation. Works for most projects
| 16 MHz | More instruction throughput for compute-heavy code
| Max | Native hardware speed (240 MHz). Slowest simulation
Most Arduino projects work well at the default 8 MHz setting. Only increase it if your code has tight computation loops where instruction throughput matters.
## Practical Limitations[#practical-limitations](#practical-limitations) ​
- WiFi requires open networks — WPA2/WPA3 encryption is not supported. Use the virtual CirkitWifi access point (no password)
- Timing may differ from real hardware — crypto operations complete instantly, ADC conversions have no delay, and clock behavior is simplified. Most Arduino code is unaffected
- No Bluetooth — if your project requires BLE or classic Bluetooth, it cannot be simulated at this time
## Example Circuits[#example-circuits](#example-circuits) ​
### WiFi & Networking[#wifi--networking](#wifi--networking) ​
- [https://app.cirkitdesigner.com/project/e6abd77f-c55f-40f5-bdf9-b503e3135672](https://app.cirkitdesigner.com/project/e6abd77f-c55f-40f5-bdf9-b503e3135672) Crypto Price Tracker
- [https://app.cirkitdesigner.com/project/491459f8-9109-4daf-b12b-17742c0057ff](https://app.cirkitdesigner.com/project/491459f8-9109-4daf-b12b-17742c0057ff) MQTT Temperature Monitor — Two ESP32s with Sensor and Display
- [https://app.cirkitdesigner.com/project/c81da98d-8e2d-4cb8-9be3-c7df6eb8e8f7](https://app.cirkitdesigner.com/project/c81da98d-8e2d-4cb8-9be3-c7df6eb8e8f7) HTTPS IP Echo Test
### Sensors[#sensors](#sensors) ​
- [https://app.cirkitdesigner.com/project/f57e6734-ca48-46a6-aca9-7dfaca641530](https://app.cirkitdesigner.com/project/f57e6734-ca48-46a6-aca9-7dfaca641530) Measuring Distance with Analog IR Sensor
- [https://app.cirkitdesigner.com/project/bec1d64c-9299-425e-93d0-e127c6236b52](https://app.cirkitdesigner.com/project/bec1d64c-9299-425e-93d0-e127c6236b52) FSR (Force Sensor) with Curve Fitting
- [https://app.cirkitdesigner.com/project/d8f5243e-c875-4d73-a17c-d77f8be5de0e](https://app.cirkitdesigner.com/project/d8f5243e-c875-4d73-a17c-d77f8be5de0e) Measuring Temperature with DS18B20
- [https://app.cirkitdesigner.com/project/dc68ab76-ba6e-4182-9629-c546861d9fac](https://app.cirkitdesigner.com/project/dc68ab76-ba6e-4182-9629-c546861d9fac) Measuring Temperature with 2 DS18B20 Devices on Same Bus
- [https://app.cirkitdesigner.com/project/a4a2a875-89e6-4fac-97e4-6af6418be9a4](https://app.cirkitdesigner.com/project/a4a2a875-89e6-4fac-97e4-6af6418be9a4) Measure Temperature with DHT11
- [https://app.cirkitdesigner.com/project/5de96c25-1221-47d4-89b7-b0014f29eb0d](https://app.cirkitdesigner.com/project/5de96c25-1221-47d4-89b7-b0014f29eb0d) Measuring Distance with HC-SR04 Sensor
- [https://app.cirkitdesigner.com/project/836904bf-27ba-4669-a9c0-79dc86a64eb7](https://app.cirkitdesigner.com/project/836904bf-27ba-4669-a9c0-79dc86a64eb7) Track Time with DS1307 RTC
### Displays[#displays](#displays) ​
- [https://app.cirkitdesigner.com/project/0ccaeef3-b081-4e83-91c6-0aaac6d13bdc](https://app.cirkitdesigner.com/project/0ccaeef3-b081-4e83-91c6-0aaac6d13bdc) Hello World with 16x2 LCD Display
- [https://app.cirkitdesigner.com/project/5032dceb-3bc6-4fce-8ce7-d40207aaac7b](https://app.cirkitdesigner.com/project/5032dceb-3bc6-4fce-8ce7-d40207aaac7b) Joystick-Controlled Dot on MAX7219 LED Matrix (Bit-Banged SPI)
### LEDs & PWM[#leds--pwm](#leds--pwm) ​
- [https://app.cirkitdesigner.com/project/76c3abfc-b024-4d87-ad5d-6f84badf1996](https://app.cirkitdesigner.com/project/76c3abfc-b024-4d87-ad5d-6f84badf1996) Iterating Red Pixel on Chained NeoPixel Rings
- [https://app.cirkitdesigner.com/project/44707273-332c-4393-ac1b-bed77e548b00](https://app.cirkitdesigner.com/project/44707273-332c-4393-ac1b-bed77e548b00) Sequential LED with 74HC595 Shift Register
- [https://app.cirkitdesigner.com/project/deabd32e-0a89-4353-8b09-c15f63882d4c](https://app.cirkitdesigner.com/project/deabd32e-0a89-4353-8b09-c15f63882d4c) PWM with an RGB LED
### Motors[#motors](#motors) ​
- [https://app.cirkitdesigner.com/project/15a7ade0-217f-4651-a15e-3e21eb11c29f](https://app.cirkitdesigner.com/project/15a7ade0-217f-4651-a15e-3e21eb11c29f) Two-Wheeled Bot with L298N Motor Driver
### Projects[#projects](#projects) ​
- [https://app.cirkitdesigner.com/project/b6be54fa-7669-46eb-be47-9345e9f43241](https://app.cirkitdesigner.com/project/b6be54fa-7669-46eb-be47-9345e9f43241) Hotel Safe
