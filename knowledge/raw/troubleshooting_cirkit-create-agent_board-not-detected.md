<!-- source: https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/board-not-detected -->

- [/docs/](/docs/)
- [/docs/troubleshooting/](/docs/troubleshooting/) Troubleshooting
- [/docs/troubleshooting/cirkit-create-agent/](/docs/troubleshooting/cirkit-create-agent/) Cirkit Create Agent Troubleshooting
- Board Not Detected
On this page
# Board Not Detected
If the Cirkit Create Agent is connected to Cirkit Designer but your Arduino or other microcontroller doesn't appear in the port/board list, follow these steps.
## Quick Checks[#quick-checks](#quick-checks) ​
### 1. Check Your USB Cable[#1-check-your-usb-cable](#1-check-your-usb-cable) ​
Not all USB cables support data transfer — some are charge-only. This is the most common cause of boards not being detected.
How to tell:
- Try a different USB cable
- If you have a cable that you know works for data (e.g., one that came with the board), use that one
- Data cables are often slightly thicker than charge-only cables
### 2. Try a Different USB Port[#2-try-a-different-usb-port](#2-try-a-different-usb-port) ​
Some USB ports (especially hubs or front-panel ports) may not provide enough power or have driver issues.
- Plug the board directly into a USB port on your computer (not through a hub)
- Try a different port if available
### 3. Check That the Board Powers On[#3-check-that-the-board-powers-on](#3-check-that-the-board-powers-on) ​
When plugged in, most Arduino boards will show a power LED (usually green or red). If no LED lights up:
- The cable may be faulty
- The USB port may not be providing power
- The board may be damaged
## Driver Issues[#driver-issues](#driver-issues) ​
Some boards require USB drivers to be installed on your computer.
### Arduino UNO, Mega, Nano (Original)[#arduino-uno-mega-nano-original](#arduino-uno-mega-nano-original) ​
These boards use the ATmega16U2 USB chip and typically work without additional drivers on modern operating systems.
### Arduino Nano (Clone) and Other CH340-Based Boards[#arduino-nano-clone-and-other-ch340-based-boards](#arduino-nano-clone-and-other-ch340-based-boards) ​
Many clone boards use the CH340 USB-to-serial chip, which requires a driver:
- Windows: Download the CH340 driver from the manufacturer's website
- Mac: On macOS 12+, the CH340 driver is usually included. For older versions, you may need to install it manually
### ESP32 Boards[#esp32-boards](#esp32-boards) ​
ESP32 boards typically use a CP2102 or CH340 USB-to-serial chip. Install the appropriate driver if your board isn't detected.
## Agent-Side Troubleshooting[#agent-side-troubleshooting](#agent-side-troubleshooting) ​
If the board is powered on, the cable is good, and drivers are installed, the issue may be with the agent's port discovery.
### Restart the Agent[#restart-the-agent](#restart-the-agent) ​
The agent's serial port discovery process can sometimes get stuck. Follow the [/docs/troubleshooting/cirkit-create-agent/universal-fix](/docs/troubleshooting/cirkit-create-agent/universal-fix) Universal Fix to fully restart the agent.
### Check the Agent Debug Console[#check-the-agent-debug-console](#check-the-agent-debug-console) ​
- Open your browser and navigate to http://localhost:8991 (or try ports 8992–9000)
- This opens the agent's built-in debug console
- Type list and press Enter to see what ports the agent detects
- If your board's port appears in the list but not in Cirkit Designer, the issue is on the web app side — try reloading the page
## Still Not Working?[#still-not-working](#still-not-working) ​
Reach out on the [https://discord.gg/zXxfQqhXEP](https://discord.gg/zXxfQqhXEP) Cirkit Designer Discord with:
- Your board model
- Your operating system
- Whether the board's power LED is on
- The output of the list command from the agent debug console
