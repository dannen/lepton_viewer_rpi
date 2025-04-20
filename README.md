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
    * Includes standard OpenCV colormaps (HOT, BONE, COOL, OCEAN, VIRIDIS, etc.).
    * Generates custom gradient colormaps (RED\_GRADIENT, GREEN\_GRADIENT, BLUE\_GRADIENT).
    * Loads custom colormaps from external `.lut` files placed in the script's directory. (Format: Text file containing a Python-style list of 256 RGB tuples, e.g., `[(0,0,0), (1,1,1), ..., (255,255,255)]`).
* File logging: Logs script events, warnings, and errors to `/var/log/thermal_viewer.log` (or falls back to `./thermal_viewer.log` if permissions fail) with a standard format.

## Hardware Requirements

* **Raspberry Pi:** Model 3B, 3B+, or 4B recommended. (Pi 3 Model B offers lower power than 3B+).
* **Display:** Adafruit Mini PiTFT 1.3" 240x240 Color Display (ST7789 driver).
    * [Product Link](https://www.adafruit.com/product/4484)
* **Camera:**
    * FLIR Lepton module (e.g., Lepton 2.5, 3.0, 3.5).
    * UVC-compatible interface board (e.g., PureThermal 1/2/Mini, GroupGets USB Breakout v2). Script uses default PureThermal VID/PID (`0x1e4e`/`0x0100`).
* **SD Card:** 8GB or larger, flashed with Raspberry Pi OS.
* **Power Supply:**
    * A reliable 5V, >=2.5A power supply for the Raspberry Pi.
    * **Optional:** [PiJuice HAT](https://uk.pi-supply.com/products/pijuice-standard) with a suitable battery (e.g., BP7X) for portability. The script works with the PiJuice providing power.
* **USB Cable:** USB-A to USB-C or Micro-USB cable suitable for connecting the PureThermal board to the Pi.

## Compatibility

* **Tested:** Raspberry Pi 3 Model B, Raspberry Pi 3 Model B+. (Should work on Pi 4B).
* **Known Issues:** This script has shown issues or failed on Raspberry Pi Zero 2 W and Orange Pi Zero 2 W, likely due to hardware limitations, UVC handling, or GPIO library incompatibilities on those specific platforms/OS versions.

## Software Dependencies

### Python Modules (Install via pip)

* `numpy` (<2.0 recommended for OpenCV compatibility)
* `opencv-python` (cv2)
* `Pillow` (PIL)
* `Adafruit-Blinka`
* `adafruit-circuitpython-rgb-display`
* `adafruit-circuitpython-busdevice` (usually installed as dependency)
* `uvctypes.py`: (Not a pip package) Python bindings for libuvc. Place in the same directory as the main script. Get from [PureThermal1 UVC Capture repository](https://github.com/groupgets/purethermal1-uvc-capture/blob/master/python/uvctypes.py).

### Debian Packages (Install via apt)

* `python3`
* `python3-pip`
* `python3-venv`
* `git`
* `cmake`
* `build-essential`
* `pkg-config`
* `libusb-1.0-0-dev` (Required for building `libuvc`)
* `python3-dev` (or `python3.X-dev` matching your version, for building some pip packages)
* `cpufrequtils` (Provides `cpufreq-set` for CPU governor control)
* `libjpeg-dev`, `zlib1g-dev` (Common dependencies for Pillow)
* `libopencv-dev` (Optional, if building OpenCV from source, not needed if using `opencv-python` pip package)
* `pijuice-base` (Optional, only if you want to interact with PiJuice via software)

## Installation

These steps assume you are starting with a relatively fresh Raspberry Pi OS (Bullseye or Bookworm recommended) and are logged in as the default `pi` user (or equivalent).

1.  **Update System:**
    ```bash
    sudo apt update
    sudo apt full-upgrade -y
    # Consider rebooting if kernel or firmware was updated
    # sudo reboot 
    ```

2.  **Install System Dependencies:**
    ```bash
    sudo apt install -y python3-pip python3-venv git cmake build-essential pkg-config libusb-1.0-0-dev python3-dev cpufrequtils libjpeg-dev zlib1g-dev
    ```
    *(Note: Adjust `python3-dev` to `python3.11-dev` or similar if needed for your specific OS version).*

3.  **Install/Build `libuvc` (GroupGets Fork Recommended):**
    ```bash
    cd ~ 
    git clone [https://github.com/groupgets/libuvc](https://github.com/groupgets/libuvc)
    cd libuvc
    mkdir build
    cd build
    cmake ..
    make -j$(nproc) 
    sudo make install
    sudo ldconfig 
    cd ~ # Return home
    ```

4.  **Enable SPI Interface:**
    ```bash
    sudo raspi-config
    ```
    Navigate to `Interface Options` -> `SPI` -> `<Yes>` to enable. Finish and reboot if prompted.

5.  **Create Python Virtual Environment:**
    ```bash
    cd ~
    python3 -m venv lepton_env 
    source ~/lepton_env/bin/activate
    ```
    *(Your prompt should now start with `(lepton_env)`)*.

6.  **Install Python Packages (inside venv):**
    ```bash
    # Upgrade pip in venv
    pip install --upgrade pip setuptools wheel

    # Install required libraries
    pip install "numpy<2.0" 
    pip install opencv-python 
    pip install Pillow
    pip install Adafruit-Blinka 
    pip install adafruit-circuitpython-rgb-display 
    ```
    * **Blinka Prompts:** When installing `Adafruit-Blinka`, it might prompt you to install system packages like `python3-rpi.gpio`, `python3-spidev`. Confirm these if prompted. It might also ask to reboot.

7.  **Setup USB Permissions (Camera):**
    Create a udev rule to allow access to the PureThermal device.
    ```bash
    echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1e4e", ATTR{idProduct}=="0100", MODE="0666", GROUP="video"' | sudo tee /etc/udev/rules.d/99-purethermal.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger
    # Add your user to the video group (if not already)
    sudo usermod -aG video $USER 
    ```
    **Log out and log back in** for the group change to take effect.

8.  **Get Code Files:**
    * Clone the repository `git clone https://github.com/dannen/lepton_viewer_rpi`
    * Download `uvctypes.py` into the same directory:
        ```bash
        wget [https://raw.githubusercontent.com/groupgets/purethermal1-uvc-capture/master/python/uvctypes.py](https://raw.githubusercontent.com/groupgets/purethermal1-uvc-capture/master/python/uvctypes.py)
        ```
    * Place any custom `.lut` files in this directory. (see my other project for luts and futher ideas https://github.com/dannen/boson_lut.git)

## Configuration

Several parameters can be adjusted directly in the Python script (`lepton_viewer.py`) near the top:

* **Camera:** `CAM_WIDTH`, `CAM_HEIGHT`, `CAM_FPS`, `PT_VID`, `PT_PID`.
* **Display:** `LCD_WIDTH`, `LCD_HEIGHT`, `LCD_ROTATION`, `X_OFFSET`, `Y_OFFSET`, `SPI_BAUDRATE`.
* **Pins:** `BUTTON_A_PIN`, `BUTTON_B_PIN` (uses Blinka `board` names).
* **CPU:** `CPU_GOVERNOR_SAVE`, `CPU_GOVERNOR_RUN`.
* **Temperature:** `CPU_TEMP_SHUTDOWN_THRESHOLD_C`, check/log intervals.
* **Logging:** `LOG_FILENAME`, `LOG_LEVEL`.

## Optional Power Saving Configuration (`config.txt`)

For additional power saving, especially when running headless or without Bluetooth, you can edit `/boot/firmware/config.txt` (`sudo nano /boot/firmware/config.txt`) and append the following lines:

```
# Disable wifi (leave this disabled until you're certain)
#dtoverlay=disable-wifi

# Disable Bluetooth (if not needed)
dtoverlay=disable-bt

# Disable HDMI output (if not needed)
hdmi_blanking=2

# Disable onboard LEDs (optional)
dtparam=pwr_led_trigger=none
dtparam=pwr_led_activelow=off
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
```

A reboot is required for these settings to take effect.

## Running the Application
### Method 1: Manually (for testing)
Activate the virtual environment: source ~/lepton_env/bin/activate
Navigate to the code directory: cd ~/thermal_viewer
Run using sudo (required for cpufreq-set and shutdown):
```bash
sudo /home/pi/lepton_env/bin/python3 ./lepton_viewer.py
```
Press Ctrl+C to exit.
### Method 2: As a Systemd Service (Recommended for auto-start)
Create Service File:
```bash
sudo cp ~/lepton_viewer_rpi/lepton_viewer.service /etc/systemd/system/lepton_viewer.service
```

Reload, Enable, Start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable lepton_viewer.service
sudo systemctl start lepton_viewer.service
```

Check Status/Logs:
```bash
sudo systemctl status lepton_viewer.service
journalctl -u lepton_viewer.service -f 
```

Reboot to test auto-start.

## Usage

    Button A (Default GPIO 23): Press briefly to cycle through the available colormaps (Defaults -> Gradients -> File LUTs -> back to start).
    Button B (Default GPIO 24): Press briefly to toggle the display backlight ON or OFF.
        When OFF: Stream stops, CPU governor set to powersave.
        When ON: Stream starts, CPU governor set to ondemand.

## Logging

    Logs are written to /var/log/thermal_viewer.log by default (if permissions allow) or ./thermal_viewer.log otherwise.
    Log format includes timestamp, hostname, process name/PID, log level, and message.
    Set LOG_LEVEL in the script to logging.DEBUG for more detailed output during troubleshooting.

## Troubleshooting

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
