<!-- source: https://www.cirkitdesigner.com/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet -->

- [/docs/](/docs/)
- Custom Simulation Components
- Guides and Examples
- Component AI Power User Guide
On this page
# Component AI Power User Guide
A comprehensive guide to mastering Cirkit's AI assistant for building custom simulation components - from simple LEDs to complex protocol-driven devices.
## 📺 Video Tutorial[#-video-tutorial](#-video-tutorial) ​
Watch our walkthrough to see the Component AI in action:
[https://www.youtube.com/watch?v=i3vKdrOKFFQ&t=36s](https://www.youtube.com/watch?v=i3vKdrOKFFQ&t=36s) Building Simulation Components with AI
## ⚡ What Makes Component AI Powerful[#-what-makes-component-ai-powerful](#-what-makes-component-ai-powerful) ​
The Component AI is a specialized assistant for building simulation components in Cirkit Designer. It handles everything from visual UI to complex behavior logic:
### Autonomous Research & Implementation[#autonomous-research--implementation](#autonomous-research--implementation) ​
- Uses Cirkit's internal component-building knowledge to generate compatible implementations
- Searches the web and GitHub for datasheets, reference libraries, and real-world implementations
- Analyzes existing code (e.g., Arduino libraries) to understand how components behave
- Implements full simulation component logic with behavior, state, and interactive UI
### What You Can Build[#what-you-can-build](#what-you-can-build) ​
- Visual components: LED matrices, 7-segment displays, LCDs, OLEDs
- Input devices: Buttons, switches, potentiometers, sensors
- Motors & actuators: Servos, stepper motors, relays
- Communication devices: I2C, SPI, raw networking, radio, and infrared models
- Browser bridges: Web Bluetooth, WebUSB, Web Serial, camera, microphone, and file-backed devices
- Audio and video components: Buzzers, speakers, PCM streams, and camera inputs
- Custom logic: Timers, counters, state machines, custom algorithms
### Key Capabilities[#key-capabilities](#key-capabilities) ​
- Simulation behavior: Define how components respond to pin signals and user interactions
- Visual feedback: Create dynamic UI that updates based on component state
- Protocol implementation: Build accurate communication behavior when needed
- Code review & debugging: Validate implementations and analyze simulation logs
- Iterative refinement: Fix bugs, improve visuals, and enhance realism
## ⏱️ Important: Processing Time[#️-important-processing-time](#️-important-processing-time) ​
info
Each request can take several minutes (typically 2-5 minutes).
The AI performs multiple complex tasks in sequence:
- Searching GitHub and the web for reference materials
- Reading and analyzing datasheets or library code
- Reasoning about protocol specifications
- Writing and validating TypeScript code
- Testing the implementation against the current Cirkit simulator
Pro tip: Provide context up front (part name, protocol type, datasheet links, or GitHub repos) to improve speed and accuracy.
## 🚀 Quick Start Workflow[#-quick-start-workflow](#-quick-start-workflow) ​
Follow this proven workflow for building components efficiently:
-
Prepare Prerequisites
- Component image (PNG/SVG)
- Pin definitions and labelling
-
Open Component AI & Describe
- Specify the component name and what it should do
- Describe desired behaviors (visual feedback, pin responses, animations)
- Include datasheet or library links if available (especially for protocol-based components)
- Say whether you need persistent setup values, uploaded files, browser
hardware, or communication with another simulated component
-
Let the AI Work
- Answer any clarifying questions concisely
- Wait for initial implementation (2-5 minutes)
-
Test Immediately
- Create a simple test circuit (e.g., Arduino + your component)
- Run simulation and observe behavior
-
Iterate & Refine
- Ask AI to review for correctness
- Request logging for debugging
- Fix protocol issues and edge cases
-
Validate
- Have AI audit against reference sources
- Verify the generated behavior works correctly in Cirkit
## 💡 Example Prompts (Copy & Use)[#-example-prompts-copy--use](#-example-prompts-copy--use) ​
### Building Visual Components[#building-visual-components](#building-visual-components) ​
Create a simulation component for a MAX7219 LED matrix display. Search GitHub
for popular Arduino libraries to understand the SPI command structure. The UI
should show an 8x8 grid of LEDs that light up based on received data.
Build a 4-digit 7-segment display component.
The component should respond to GPIO pins for digit selection and segment control.
### Input & Sensor Components[#input--sensor-components](#input--sensor-components) ​
Implement a DHT22 temperature/humidity sensor. Search GitHub for the Arduino
DHT library to understand the single-wire communication protocol and timing.
Return realistic sensor values that can be configured in the UI.
Create a rotary encoder component with visual feedback showing rotation
direction and click detection. Check how popular Arduino libraries handle
pulse counting and debouncing.
### Motors & Actuators[#motors--actuators](#motors--actuators) ​
Build a servo motor simulation component. Search for Arduino Servo library
examples to understand PWM signal timing. Show visual rotation from 0-180
degrees with realistic movement speed and easing.
### Communication Components (Protocol-Based)[#communication-components-protocol-based](#communication-components-protocol-based) ​
Implement an MFRC522 RFID reader using SPI. Search GitHub for the miguelbalboa
MFRC522 Arduino library to understand register mapping, command sequences, and
authentication flow. Include visual feedback when cards are detected.
### Code Review & Debugging[#code-review--debugging](#code-review--debugging) ​
Review my component implementation for correctness and suggest improvements.
Add detailed logging to trace what's happening during simulation. I'll re-run
it and then you can check the debug logs.
### Improving Existing Components[#improving-existing-components](#improving-existing-components) ​
Check the debug logs from my last simulation run. Analyze what's going wrong
and suggest fixes.
Enhance my button component to include debouncing and better visual feedback.
## 🔍 Debugging Like a Pro[#-debugging-like-a-pro](#-debugging-like-a-pro) ​
### Step 1: Add Instrumentation[#step-1-add-instrumentation](#step-1-add-instrumentation) ​
Ask the AI to add logging before running tests:
Add detailed logging to help debug this component.
### Step 2: Run Simulation[#step-2-run-simulation](#step-2-run-simulation) ​
Run your simulation in Cirkit Designer to generate debug logs.
### Step 3: AI-Powered Analysis[#step-3-ai-powered-analysis](#step-3-ai-powered-analysis) ​
Ask the AI to analyze the logs:
Check the debug logs and analyze what's going wrong. Propose specific fixes.
### Step 4: Verify Assumptions[#step-4-verify-assumptions](#step-4-verify-assumptions) ​
Challenge the AI to explain its implementation:
Explain how this component works and walk me through the simulation flow.
### Step 5: Compatibility Check[#step-5-compatibility-check](#step-5-compatibility-check) ​
Review the component and confirm it works correctly in the current Cirkit simulator.
## 🎯 Best Practices[#-best-practices](#-best-practices) ​
### Provide Context Early[#provide-context-early](#provide-context-early) ​
The more information you provide upfront, the better results:
- Component name and type (LED matrix, servo, sensor, etc.)
- How it should look and behave visually
- Pin configuration and signal types (digital, PWM, analog, protocol)
- For protocol components: datasheet or reference library links
- Any specific timing or behavioral requirements
### Start Small, Expand Incrementally[#start-small-expand-incrementally](#start-small-expand-incrementally) ​
- Start with basic structure: pins, runtime state, simple UI
- Implement one core behavior (e.g., respond to one pin, show one visual state)
- Test in a simple circuit
- Add complexity gradually (more features, animations, edge cases)
- Retest after each addition
### Build Logic First, Polish Visuals Later[#build-logic-first-polish-visuals-later](#build-logic-first-polish-visuals-later) ​
- Get the simulation behavior working correctly first
- Ensure pin interactions and state updates work
- Verify component responds to circuit signals properly
- Then enhance the UI with animations, colors, and effects
### Lock In Checkpoints[#lock-in-checkpoints](#lock-in-checkpoints) ​
After each successful milestone:
- Test in Cirkit Designer with a real circuit
- Summarize what works to the AI
- Confirm behavior before adding new features
- Keep a mental list of what's working vs. what needs refinement
## 🧩 Advanced Techniques[#-advanced-techniques](#-advanced-techniques) ​
### Multi-Step Problem Solving[#multi-step-problem-solving](#multi-step-problem-solving) ​
When stuck on complex issues:
- Ask the AI to create a detailed plan
- Execute each step (build structure → add behavior → add visuals → test → debug)
- Report results back to the AI after each step
- Share simulation logs or describe unexpected behavior
- Iterate until component works as expected
### Leveraging Web & GitHub Search[#leveraging-web--github-search](#leveraging-web--github-search) ​
The AI can find and analyze real-world references:
- Component datasheets and specifications
- Popular Arduino/Adafruit/SparkFun libraries
- Example implementations from GitHub
- Community forum discussions and tutorials
Example prompts:
Search for how the HC-SR04 ultrasonic sensor works and find an Arduino library
to base the timing and behavior on. Include visual representation of the sound wave.
Find examples of realistic servo motor animations in JavaScript and apply
similar easing/interpolation to make movement look smooth.
### Comparative Validation[#comparative-validation](#comparative-validation) ​
Have the AI cross-reference multiple sources:
Compare my component's behavior against both the datasheet and the most
popular Arduino library. Highlight any differences in timing, state handling,
or visual representation.
