<!-- source: https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/agent-not-connecting -->

- [/docs/](/docs/)
- [/docs/troubleshooting/](/docs/troubleshooting/) Troubleshooting
- [/docs/troubleshooting/cirkit-create-agent/](/docs/troubleshooting/cirkit-create-agent/) Cirkit Create Agent Troubleshooting
- Agent Not Connecting
On this page
# Agent Not Connecting
If you've already tried the [/docs/troubleshooting/cirkit-create-agent/browser-permissions](/docs/troubleshooting/cirkit-create-agent/browser-permissions) browser permissions fix and the [/docs/troubleshooting/cirkit-create-agent/universal-fix](/docs/troubleshooting/cirkit-create-agent/universal-fix) universal fix and the agent still won't connect, this page covers less common causes.
## How the Connection Works[#how-the-connection-works](#how-the-connection-works) ​
The Cirkit Create Agent runs a local web server on your computer, listening on ports 8991–9000. When you open Cirkit Designer in your browser, it sends requests to http://localhost:8991/info through http://localhost:9000/info to find the agent. If any port responds, the connection is established via WebSocket.
## Possible Causes[#possible-causes](#possible-causes) ​
### Firewall or Antivirus Blocking Localhost Connections[#firewall-or-antivirus-blocking-localhost-connections](#firewall-or-antivirus-blocking-localhost-connections) ​
Some security software (especially on school or corporate computers) blocks applications from listening on local ports or blocks the browser from connecting to localhost.
What to check:
- Temporarily disable your firewall/antivirus and see if the agent connects
- If it does, add an exception for the Cirkit Create Agent (ports 8991–9000 on localhost)
- On school-managed computers, you may need to ask your IT administrator to whitelist the agent
### Port Conflict[#port-conflict](#port-conflict) ​
Another application may be using ports 8991–9000, preventing the agent from starting its server.
What to check:
Windows (Command Prompt as Administrator):
netstat -ano | findstr "8991 8992 8993 8994 8995 8996 8997 8998 8999 9000"
Mac/Linux (Terminal):
lsof -i :8991-9000
If another application is using these ports, close it or reconfigure it to use a different port range.
### Browser Extensions Interfering[#browser-extensions-interfering](#browser-extensions-interfering) ​
Some browser extensions (ad blockers, privacy extensions, or network-related extensions) can block requests to localhost.
What to try:
- Open Cirkit Designer in an incognito/private window (most extensions are disabled by default in incognito)
- If the agent connects in incognito, one of your extensions is the cause
- Disable extensions one at a time to identify the culprit
### HTTPS Certificate Issues[#https-certificate-issues](#https-certificate-issues) ​
The agent uses self-signed certificates for secure localhost connections. If these certificates are corrupted or expired, the connection may fail.
What to try:
- Close the agent
- Delete the certificates directory:
- Mac: ~/.arduino-create/
- Windows: %APPDATA%\ArduinoCreateAgent\
- Look for a cert.pem and key.pem file and delete them
- Restart the agent — it will regenerate the certificates automatically
## Getting Further Help[#getting-further-help](#getting-further-help) ​
If none of the above resolves your issue, reach out on the [https://discord.gg/zXxfQqhXEP](https://discord.gg/zXxfQqhXEP) Cirkit Designer Discord with the following information:
- Your operating system (Windows/Mac/Linux) and version
- Your browser and version
- Whether the agent icon appears in your system tray/menu bar
- Any error messages you see in Cirkit Designer
