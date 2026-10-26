SPL Flasher for RG Rotate
==========================

This flashes a new splloader (SPL) onto your RG Rotate handheld, so it
can boot a compatible Linux OS from the SD card slot.

THIS IS *ONLY* TO BE USED WITH AN ANBERNIC RG ROTATE HANDHELD!


1. Fully power off your RG Rotate handheld.

2a. On Windows, double-click flash-spl.bat (or run it from a command
    prompt) in this folder.

2b. On Linux, run ./flash-spl.sh from this folder. If it fails with a
    USB permissions error, re-run it with sudo.

3. While holding the Home/Back combo button on your RG Rotate, plug it
   into your PC with a USB-A to USB-C cable.

4. Wait a short bit for the script to confirm completion. It will
   print "SPL flash complete!" when done, or an error message if
   something went wrong.


If your device no longer boots after flashing this SPL with the included
scripts, follow these steps to restore the stock SPL image:


1. Plug your device into your computer with a USB-A to USB-C cable.

2. Hold the Power, Volume Down, and Home/Back buttons. Do NOT let go
   of the Home/Back button in particular.

3a. On Windows, double-click restore-stock-spl.bat (or run it from a
    command prompt) in this folder.

3b. On Linux, run ./restore-stock-spl.sh from this folder. If it fails
    with a USB permissions error, re-run it with sudo.

4. Wait for the script to finish. At this point it is safe to let go
   of the Home/Back button. If your device is still off after you let
   go, hold ONLY the Power and Volume Down buttons until it turns on.

