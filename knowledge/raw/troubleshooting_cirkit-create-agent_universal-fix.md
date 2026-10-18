<!-- source: https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/universal-fix -->

- [/docs/](/docs/)
- [/docs/troubleshooting/](/docs/troubleshooting/) Troubleshooting
- [/docs/troubleshooting/cirkit-create-agent/](/docs/troubleshooting/cirkit-create-agent/) Cirkit Create Agent Troubleshooting
- Universal Fix
On this page
# Universal Fix
If you've already checked your [/docs/troubleshooting/cirkit-create-agent/browser-permissions](/docs/troubleshooting/cirkit-create-agent/browser-permissions) browser permissions and the Cirkit Create Agent still isn't connecting, follow this step-by-step process. This resolves the majority of agent issues, including:
- Agent appears to be running but Cirkit Designer can't find it (continues to show "Download Agent")
- "Scanning for devices..." but Arduino is never detected
## The Fix[#the-fix](#the-fix) ​
### Video Walkthrough (Windows)[#video-walkthrough-windows](#video-walkthrough-windows) ​
### Step 1: Close All Running Instances of the Cirkit Create Agent[#step-1-close-all-running-instances-of-the-cirkit-create-agent](#step-1-close-all-running-instances-of-the-cirkit-create-agent) ​
You may have multiple instances of the agent running (including failed/zombie processes). Close them all.
Windows:
- Press Ctrl + Shift + Esc to open Task Manager
- Click More details if Task Manager is in compact mode
- Look for any processes named CirkitCreateAgent, ArduinoCreateAgent, or similar
- Select each one and click End task
- Repeat until no agent processes remain
Mac:
- Open Activity Monitor (search for it in Spotlight with Cmd + Space)
- In the search bar, type Cirkit or Arduino
- Select any matching processes
- Click the X button in the toolbar and choose Force Quit
- Repeat until no agent processes remain
### Step 2: Close All Instances of the Cirkit Designer Web App[#step-2-close-all-instances-of-the-cirkit-designer-web-app](#step-2-close-all-instances-of-the-cirkit-designer-web-app) ​
Close every browser tab or window that has Cirkit Designer open. This ensures no stale connections are held.
### Step 3: Verify Only One Create Agent Is Installed[#step-3-verify-only-one-create-agent-is-installed](#step-3-verify-only-one-create-agent-is-installed) ​
Having multiple copies of the agent installed can cause conflicts. Check that you only have one.
Windows:
- Open Settings > Apps > Installed apps (or Add or remove programs on older Windows)
- Search for Cirkit or Arduino Create Agent
- If you see more than one installation, uninstall all of them
- Reinstall a single copy from Cirkit Designer
Mac:
- Open Finder
- Check both of these locations for the agent:
- /Applications/ (system-wide)
- ~/Applications/ (user-only)
- Also check ~/.arduino-create/ for agent data files
- If you find multiple copies, delete all of them
- Reinstall a single copy from Cirkit Designer
### Step 4: Start the Create Agent[#step-4-start-the-create-agent](#step-4-start-the-create-agent) ​
Launch the Cirkit Create Agent. You should see its icon appear in your system tray (Windows) or menu bar (Mac).
### Step 5: Open Cirkit Designer[#step-5-open-cirkit-designer](#step-5-open-cirkit-designer) ​
Open one browser tab and navigate to [https://app.cirkitdesigner.com](https://app.cirkitdesigner.com) app.cirkitdesigner.com. The agent should now be detected.
## Still Not Working?[#still-not-working](#still-not-working) ​
If this process didn't resolve the issue, see [/docs/troubleshooting/cirkit-create-agent/agent-not-connecting](/docs/troubleshooting/cirkit-create-agent/agent-not-connecting) Agent Not Connecting for a deeper dive into connection problems.
