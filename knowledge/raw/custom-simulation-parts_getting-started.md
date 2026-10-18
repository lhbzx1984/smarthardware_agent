<!-- source: https://www.cirkitdesigner.com/docs/custom-simulation-parts/getting-started -->

- [/docs/](/docs/)
- Custom Simulation Components
- Getting Started
On this page
# Getting Started
Custom simulation components let you define how a part looks, responds to
electrical signals, exchanges data, and interacts with users.
## 🚀 Quick Start: Learn by Example[#-quick-start-learn-by-example](#-quick-start-learn-by-example) ​
The fastest way to get started is to watch and follow along with our tutorials:
- [https://www.youtube.com/watch?v=i3vKdrOKFFQ&t=36s](https://www.youtube.com/watch?v=i3vKdrOKFFQ&t=36s) 📺 Video: Building Simulation Components with AI - Watch the Component AI in action
- [/docs/tutorials/create-simulation-servo](/docs/tutorials/create-simulation-servo) Tutorial: Build a Servo Component - Step-by-step guide with PWM control and visual rotation
- [/docs/tutorials/create-simulation-lcd](/docs/tutorials/create-simulation-lcd) Tutorial: Build a 16x2 LCD Display - Complete walkthrough for I2C LCD component
- [/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet](/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet) Component AI Power User Guide - Master Cirkit's AI assistant for building components
## Component creation workflow[#component-creation-workflow](#component-creation-workflow) ​
Creating a custom component has three main steps:
-
Upload a Component Image
- Supported formats: SVG, PNG, or Fritzing Part.
- This becomes the component's static visual.
-
Define Component Pins
- Clearly identify pin locations and labels.
- Pins interface between your component and other parts in the simulation.
-
(Optional) Define Simulation Behavior
- Ask Component AI to add visual behavior, electrical responses, or support
for the protocol used by the physical part.
- Test the generated behavior in a simple circuit and describe any changes
you want Component AI to make.
## Understanding Visual Layers: Static vs Interactive[#understanding-visual-layers-static-vs-interactive](#understanding-visual-layers-static-vs-interactive) ​
Every simulated custom component can use two visual layers:
-
Static Layer: Always visible by default, this is the static image you uploaded (SVG, PNG, or Fritzing part). It represents your component visually when the simulation is turned off.
-
Interactive Layer: Activated when the simulation is turned on. This layer consists of dynamic, interactive elements that respond to user inputs and state changes during simulation.
## Choosing the Right Image Type[#choosing-the-right-image-type](#choosing-the-right-image-type) ​
### Static PNG Component[#static-png-component](#static-png-component) ​
- When simulation is off: Displays a static PNG image.
- When simulation is on: Shows the static PNG image with limited interactive elements overlaid.
- Best suited for: Simple sensors, indicators, or components with minimal interactions.
### Interactive SVG Component[#interactive-svg-component](#interactive-svg-component) ​
- When simulation is off: Displays a static SVG image.
- When simulation is on: The SVG becomes fully interactive.
- Best suited for: Complex interactions such as knobs, joysticks, buttons, or other dynamic elements.
- Choose PNG if your component is mostly static or requires minimal interactivity.
- Choose SVG if you need rich visuals, dynamic elements, or complex interactions.
## What's Next?[#whats-next](#whats-next) ​
- Learn how to describe, test, and refine a component with the
[/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet](/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet) Component AI Power User Guide.
- Browse [/docs/custom-simulation-parts/component-examples](/docs/custom-simulation-parts/component-examples) Component Examples for
inspiration and working circuits.
## Share a Component with the Community[#share-a-component-with-the-community](#share-a-component-with-the-community) ​
Your custom components can remain available only in your own Cirkit account. If
you want to make one available to the wider community, you can submit it for
review for the public library.
Cirkit reviews public-library submissions for quality and accuracy. A submission
may be accepted, returned with requested changes, or declined. High-quality
public contributions may also be featured on the Cirkit homepage.
See [/docs/contributors](/docs/contributors) Contributing Components and the
[/docs/contributors/submission-guidelines](/docs/contributors/submission-guidelines) Submission Guidelines before
submitting your component.
