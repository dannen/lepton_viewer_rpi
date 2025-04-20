# Lepton Thermal Viewer for Raspberry Pi + Mini PiTFT

## Description

This Python script displays a video feed from a FLIR Lepton thermal camera module (connected via a UVC-compatible interface like PureThermal) on an Adafruit Mini PiTFT 1.3" display attached to a Raspberry Pi (3B, 3B+, 4B recommended).

It includes features like multiple colormaps (built-in, custom generated gradients, and external `.lut` files), power saving by stopping the UVC stream and changing the CPU governor when the display is off, CPU temperature monitoring with automatic shutdown, and detailed file logging.

## Features

* Displays Lepton thermal camera feed (acquires UYVY format over UVC).
* Output tailored for Adafruit Mini PiTFT 1.3" (240x240 ST7789 SPI display).
* Uses Mini PiTFT's built-in buttons for control:
    * Button A: Cycle through available colormaps.
    * Button B: Toggle display backlight ON/OFF (also toggles power saving).
* Power saving mode: Stops the UVC stream and sets CPU governor to `powersave` when the display is toggled OFF; restarts stream and sets governor to `ondemand` when turned ON.
* CPU temperature monitoring: Logs CPU temperature periodically and triggers an automatic shutdown if it exceeds a configurable threshold.
* Colormap Support:
    * Includes standard OpenCV colormaps (HOT, BONE, COOL, OCEAN, VIRIDIS).
    * Generates custom gradient colormaps (RED\_GRADIENT, GREEN\_GRADIENT, BLUE\_GRADIENT).
    * Loads custom colormaps from external `.lut` files placed in the script's directory.
* File logging: Logs script events, warnings, and errors to `/var/log/thermal_viewer.log` (or falls back to `./thermal_viewer.log` if permissions fail).

## Hardware Requirements

* **Raspberry Pi:** Model 3B, 3B+, or 4B recommended.
* **Display:** Adafruit Mini PiTFT 1.3" 240x240 Color Display (ST7789 driver).
    * [Product Link](https://www.adafruit.com/product/4484)
* **Camera:**
    * FLIR Lepton module (e.g., Lepton 2.5, 3.0, 3.5).
    * UVC-compatible interface board (e.g., PureThermal 1/2/Mini, GroupGets USB Breakout v2). The script assumes the default PureThermal VID/PID (`0x1e4e`/`0x0100`), which can be configured.
* **Wiring:** Correct SPI wiring for the Mini PiTFT to the Raspberry Pi GPIO header. Default pins are configured in the script (see Configuration section). Ensure power supply is adequate for the Pi, display, and camera board.

## Compatibility

* **Tested:** Raspberry Pi 3B+, Raspberry Pi 4B.
* **Known Issues:** This script has been tested and does **not** work correctly on Raspberry Pi Zero 2 W or Orange Pi Zero 2 W, likely due to hardware limitations, UVC handling, or other incompatibilities encountered during testing.

## Software Dependencies

### Python Modules (Install via pip)

* `numpy` (<2.0 recommended for broader OpenCV compatibility)
* `opencv-python` (cv2)
* `adafruit-circuitpython-rgb-display`
* `adafruit-circuitpython-busdevice`
* `Pillow` (PIL)
* `uvctypes.py`: This file (not a pip package) contains Python bindings for libuvc. It needs to be placed in the same directory as the main script. You can obtain it from sources like the [PureThermal1 UVC Capture repository](https://github.com/groupgets/purethermal1-uvc-capture/blob/master/python/uvctypes.py).

### Debian Packages (Install via apt)

* `python3`
* `python3-pip`
* `git`
* `cmake`
* `build-essential`
* `pkg-config`
* `libusb-1.0-0-dev` (Required for building `libuvc`)
* `libuvc-dev` (Provides the `libuvc.so` library - installation via build is often required)
* `cpufrequtils` (Provides `cpufreq-set` for CPU governor control)

## Installation

1.  **Update System:**
    ```bash
    sudo apt update && sudo apt full-upgrade -y
    ```
2.  **Install Debian Packages:**
    ```bash
    sudo apt install -y python3 python3-pip git cmake build-essential pkg-config libusb-1.0-0-dev cpufrequtils
    ```
3.  **Install/Build `libuvc`:**
    *(Option A: Try installing package - may not be available or up-to-date)*
    ```bash
    # sudo apt install libuvc-dev # Try this first, if it fails or causes issues, use Option B
    ```
    *(Option B: Build from source - Recommended)*
    ```bash
    git clone [https://github.com/libuvc/libuvc.git](https://github.com/libuvc/libuvc.git)
    cd libuvc
    mkdir build
    cd build
    cmake .. -DBUILD_EXAMPLE=OFF # Optionally disable examples
    make
    sudo make install
    sudo ldconfig -v # Update library cache
    cd ../.. # Go back to your original directory
    ```
4.  **Enable SPI:**
    ```bash
    sudo raspi-config
    ```
    Navigate to `Interface Options` -> `SPI` -> `Yes` to enable. Finish and reboot if prompted.

5.  **Install Python Modules:**
    ```bash
    sudo pip3 install --upgrade pip
    # Install specific numpy version (<2.0) first if needed for OpenCV compatibility
    sudo pip3 install "numpy<2.0"
    # Install other required packages
    sudo pip3 install Pillow adafruit-circuitpython-rgb-display adafruit-circuitpython-busdevice opencv-python
    ```
    *(Note: Using `sudo pip3` installs packages globally. Consider using a virtual environment for better dependency management if preferred.)*

6.  **Get Files:**
    * Download or clone the main script (`lepton_viewer_rpi3.py`).
    * Download `uvctypes.py` (see Python Modules section above) and place it in the same directory.
    * Place any custom `.lut` files (like `ironblack.lut`) in the same directory.
    * Make the main script executable: `chmod +x lepton_viewer_rpi3.py`

7.  **Permissions:**
    * **UVC Device:** Your user (`pi` by default) needs permission to access the camera device (`/dev/videoX`). Add your user to the `video` group:
        ```bash
        sudo usermod -a -G video $USER
        ```
        You may need to log out and log back in for this to take effect. Alternatively, create a udev rule (see Troubleshooting).
    * **GPIO:** Accessing GPIO pins typically requires root privileges. Running the script with `sudo` is the simplest way.
    * **CPU Governor:** The script uses `sudo cpufreq-set`. Running the main script with `sudo` handles this.
    * **Logging:** To write to `/var/log/thermal_viewer.log`, the script needs write permission in `/var/log`. Running with `sudo` grants this. If run without `sudo` and permissions fail, it will log to `./thermal_viewer.log` instead.

## Configuration

Several parameters can be adjusted directly in the Python script near the top:

* **Camera:** `CAM_WIDTH`, `CAM_HEIGHT`, `CAM_FPS`, `PT_VID`, `PT_PID`.
* **Display:** `LCD_WIDTH`, `LCD_HEIGHT`, `LCD_ROTATION`, `X_OFFSET`, `Y_OFFSET`, `SPI_BAUDRATE`.
* **Pins:** `SPI_SCK`, `SPI_MOSI`, `SPI_CS`, `DC_PIN`, `BACKLIGHT_PIN`, `BUTTON_A_PIN`, `BUTTON_B_PIN`. Ensure these match your wiring.
* **CPU:** `CPU_GOVERNOR_SAVE`, `CPU_GOVERNOR_RUN`.
* **Temperature:** `CPU_TEMP_SHUTDOWN_THRESHOLD_C`.
* **Logging:** `LOG_FILENAME`, `LOG_LEVEL` (e.g., `logging.INFO`, `logging.DEBUG`).

## Running the Script

Navigate to the directory containing the script and `uvctypes.py`:

```bash
cd /path/to/your/script_directory

Run using sudo to ensure necessary permissions:
Bash

sudo python3 ./lepton_viewer_rpi3.py

Press Ctrl+C to exit gracefully.

(Optional: Consider setting up a systemd service to run the script automatically on boot.)
Usage

    Button A (Default GPIO 23): Press briefly to cycle through the available colormaps (Defaults -> Gradients -> File LUTs -> back to start).
    Button B (Default GPIO 24): Press briefly to toggle the display backlight ON or OFF.
        When OFF: Stream stops, CPU governor set to powersave.
        When ON: Stream starts, CPU governor set to ondemand.

Logging

    Logs are written to /var/log/thermal_viewer.log by default (if permissions allow) or ./thermal_viewer.log otherwise.
    Log format includes timestamp, hostname, process name/PID, log level, and message.
    Set LOG_LEVEL in the script to logging.DEBUG for more detailed output during troubleshooting.

Troubleshooting

    Permission Denied (UVC / dev/videoX): Ensure your user is in the video group (see Installation Step 7) or create a udev rule. Example rule (/etc/udev/rules.d/99-purethermal.rules):
    Code snippet

    SUBSYSTEM=="usb", ATTRS{idVendor}=="1e4e", ATTRS{idProduct}=="0100", MODE="0666", GROUP="video"

    Reload rules: sudo udevadm control --reload-rules && sudo udevadm trigger
    Permission Denied (GPIO / cpufreq-set / /var/log): Run the script using sudo python3 ....
    ImportError: No module named cv2 (or similar): Ensure Python packages installed correctly (sudo pip3 install ...). Check your Python environment if using virtual environments.
    ImportError: No module named uvctypes: Make sure uvctypes.py is in the same directory as the main script.
    cpufreq-set: command not found: Install cpufrequtils (sudo apt install cpufrequtils).
    OpenCV / NumPy Errors: If related to _ARRAY_API_, try ensuring NumPy version is less than 2.0 (sudo pip3 install "numpy<2.0").
    Camera Not Found / libuvc errors: Verify lsusb shows the camera (VID/PID match script). Double-check libuvc installation (sudo ldconfig -p | grep libuvc). Check camera connection.
    Display Not Working: Check wiring, ensure SPI is enabled (sudo raspi-config), verify pin definitions in the script match wiring.
    Low FPS: Some processing overhead is expected. Ensure the CPU governor switches to ondemand or performance when the display is active. Close other CPU-intensive applications.

License

This project is released under the MIT License.