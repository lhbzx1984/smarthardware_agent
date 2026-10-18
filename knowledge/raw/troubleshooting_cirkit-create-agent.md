<!-- source: https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/ -->

- [/docs/](/docs/)
- [/docs/troubleshooting/](/docs/troubleshooting/) Troubleshooting
- Cirkit Create Agent Troubleshooting
On this page
# Cirkit Create Agent Troubleshooting
The Cirkit Create Agent is a desktop application that allows Cirkit Designer to communicate with physical microcontrollers (Arduino, ESP32, etc.) connected to your computer via USB. If you're having trouble getting it to work, start here.
## Common Issues[#common-issues](#common-issues) ​
| | Issue | Start Here
| Agent installed but Cirkit Designer says it can't find it | [/docs/troubleshooting/cirkit-create-agent/browser-permissions](/docs/troubleshooting/cirkit-create-agent/browser-permissions) Browser Permissions
| Agent was working before but stopped connecting | [/docs/troubleshooting/cirkit-create-agent/universal-fix](/docs/troubleshooting/cirkit-create-agent/universal-fix) Universal Fix
| Agent connects but your board doesn't show up | [/docs/troubleshooting/cirkit-create-agent/board-not-detected](/docs/troubleshooting/cirkit-create-agent/board-not-detected) Board Not Detected
| Setting up for a classroom | [/docs/troubleshooting/cirkit-create-agent/classroom-setup](/docs/troubleshooting/cirkit-create-agent/classroom-setup) Classroom Setup Guide
## How the Agent Works[#how-the-agent-works](#how-the-agent-works) ​
The Cirkit Create Agent runs in the background on your computer and listens on a local port (8991–9000). When you open Cirkit Designer in your browser, the web app scans these ports to find the agent. Once connected, the agent detects USB-connected boards and enables you to upload code and use the serial monitor.
For this to work, three things need to be true:
- Your browser allows Cirkit Designer to connect — see [/docs/troubleshooting/cirkit-create-agent/browser-permissions](/docs/troubleshooting/cirkit-create-agent/browser-permissions) Browser Permissions
- Exactly one instance of the agent is running — see [/docs/troubleshooting/cirkit-create-agent/universal-fix](/docs/troubleshooting/cirkit-create-agent/universal-fix) Universal Fix
- Your board is properly connected via USB — see [/docs/troubleshooting/cirkit-create-agent/board-not-detected](/docs/troubleshooting/cirkit-create-agent/board-not-detected) Board Not Detected
