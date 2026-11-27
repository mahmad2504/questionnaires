All team members using an Arm Windows 365 (W365) Cloud PC must verify their Windows version and complete the mandatory upgrade if required.

## 1. Verify your Windows versions

Log in to your Arm W365 Cloud PC.

Inside the Cloud PC session, press Windows + R, type `winver`, and press Enter. Check the version in the About Windows window.

- **25H2:** Already updated. Report your status as Completed.
- **Not 25H2:** Follow the upgrade steps below, run `winver` again, and then report your status.

Run `winver` on the Arm Cloud PC, not on your local computer.

## 2. Complete the mandatory update

Open Settings > Windows Update > Check for updates. Install Windows 11 25H2, restart when prompted, and run `winver` again to confirm the upgrade. If the update is not listed, wait a few hours and check again.

Mandatory deployment is Thursday, December 3, 2026. The update installs in the background, so you can keep working. You have a two-day grace period to restart. If you do not restart, the Cloud PC restarts on its own. The final installation can take up to five minutes, and the Cloud PC is unavailable during that time.

## 3. Report your status

Choose a status only after you have checked `winver` on the Arm Cloud PC.

- **Completed:** `winver` shows Windows 11 25H2. In the update, note the version you saw.
- **In progress:** The update is installing, is not listed yet, or you still need to restart. Say where you are in the update.
- **Blocked:** The upgrade cannot proceed. Describe the issue in the update and attach a screenshot.

Submit again if your status changes. A Completed reply stays locked until an admin records a different status. Earlier updates are kept. Finish the upgrade early where you can, so the December 3 deployment causes less disruption.

Source: Arm IT – Windows 11 25H2 update guidelines (Logic Silicon).
