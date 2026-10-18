<!-- source: https://www.cirkitdesigner.com/docs/guides/wifi-networking -->

- [/docs/](/docs/)
- Guides
- WiFi Networking
On this page
# WiFi Networking
Cirkit Designer simulates a WiFi network with full internet access. You can use WiFi-capable boards like the ESP32-S3 to prototype IoT projects directly in the simulator. Common use cases include:
- Connecting to MQTT servers to send sensor data
- Querying web services over HTTP and HTTPS
## Connecting to WiFi[#connecting-to-wifi](#connecting-to-wifi) ​
The simulator provides a virtual WiFi access point called CirkitWifi. It is an open access point — no password is required.
### Connecting from Arduino[#connecting-from-arduino](#connecting-from-arduino) ​
To connect from an Arduino sketch running on an ESP32-S3, use the following code:
#include <WiFi.h>
void setup() {
Serial.begin(9600);
Serial.print("Connecting to WiFi");
WiFi.begin("CirkitWifi", "");
while (WiFi.status() != WL_CONNECTED) {
delay(100);
Serial.print(".");
}
Serial.println(" Connected!");
}
void loop() {
delay(100); // TODO: Build something amazing!
}
## Internet Access[#internet-access](#internet-access) ​
Your simulated device has internet access automatically — no additional setup or gateway installation is required. When your ESP32-S3 code makes a network request (HTTP, DNS, MQTT, etc.), Cirkit Designer routes the traffic through its backend to reach external servers.
## Example Projects[#example-projects](#example-projects) ​
- [https://app.cirkitdesigner.com/project/e6abd77f-c55f-40f5-bdf9-b503e3135672](https://app.cirkitdesigner.com/project/e6abd77f-c55f-40f5-bdf9-b503e3135672) Crypto Price Tracker — Fetches live cryptocurrency prices over WiFi and displays them on screen.
- [https://app.cirkitdesigner.com/project/c81da98d-8e2d-4cb8-9be3-c7df6eb8e8f7](https://app.cirkitdesigner.com/project/c81da98d-8e2d-4cb8-9be3-c7df6eb8e8f7) IP Echo Test — Makes an HTTPS request to httpbin.org and displays the response on an ILI9341 TFT display.
