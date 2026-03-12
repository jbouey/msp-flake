---
title: Fleet Deployment — One Command, Every Site
duration: 60s
demo_file: 04-fleet-deployment
---

## Scene 1: Opening

[SCREEN: Navigate to fleet management / sites overview]

You've got twenty client sites. A critical security patch needs to go out today. With traditional MSP tooling, that's twenty remote sessions, twenty change windows, twenty chances for something to go wrong. With OsirisCare, it's one fleet order.

## Scene 2: Fleet Order

[SCREEN: Create a new fleet order, fill in parameters]

I create a fleet order — in this case, a NixOS rebuild that pulls the latest hardened configuration from our Git repository. Every appliance picks it up on its next check-in, executes the rebuild, and reports back. If anything fails, it automatically rolls back. No truck rolls. No babysitting.

## Scene 3: Progress Tracking

[SCREEN: Show fleet order status with completion tracking per appliance]

Here's the fleet order in progress. You can see exactly which appliances have completed, which are pending, and if any failed. The entire fleet updates in minutes, not days.

## Scene 4: Close

[SCREEN: Show all sites updated to latest version]

Infrastructure as code, deployed as compliance. Every site running the exact same hardened configuration, verified by continuous drift detection. That's fleet management done right.
