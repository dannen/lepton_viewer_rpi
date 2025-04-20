#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# === Lepton Viewer for Pi 3B+/4 + Adafruit Mini PiTFT ===
# Uses libuvc/ctypes for UYVY capture.
# Uses adafruit_rgb_display library for ST7789 display output.
# Uses Mini PiTFT built-in buttons.
# Added power saving: Stops UVC stream, sets CPU to powersave when backlight is off.
# Added CPU temperature monitoring and auto-shutdown.
# Added file logging to /var/log/

import time
import numpy as np
import traceback
from queue import Queue, Empty as QueueEmpty
import threading
from ctypes import *
import os
import subprocess # For CPU governor and shutdown
import logging # For logging to file
import ast # For safe evaluation of the LUT data

# --- Logging Setup ---
# Standardized syslog-like format: "<timestamp> <hostname> <process_name>[<pid>]: <log_level>: <message>"
LOG_FILENAME = '/var/log/thermal_viewer.log'  # Log file in standard system log directory
LOG_LEVEL = logging.INFO  # Set desired log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

try:
    # Fetch the hostname dynamically
    hostname = os.uname().nodename

    # Configure logging with the hostname included in the format
    logging.basicConfig(
        level=LOG_LEVEL,
        format=f'%(asctime)s {hostname} %(process)s[%(process)d]: %(levelname)s: %(message)s',
        filename=LOG_FILENAME,
        filemode='a'  # Append mode
    )
except PermissionError:
    # Fallback to logging in the current directory if /var/log isn't writable
    LOG_FILENAME = './thermal_viewer.log'
    logging.basicConfig(
        level=LOG_LEVEL,
        format=f'%(asctime)s {hostname} %(process)s[%(process)d]: %(levelname)s: %(message)s',
        filename=LOG_FILENAME,
        filemode='a'
    )
    logging.warning(f"Could not write to /var/log, falling back to {LOG_FILENAME}")
except Exception as log_e:
    print(f"FATAL: Could not configure logging. Error: {log_e}")
    exit(1)  # Exit if logging cannot be set up

logging.info("--- Script Started ---")

# --- Display Libraries ---
try:
    import board
    import digitalio
    import busio
    from adafruit_rgb_display import st7789 as adafruit_rgb_display_st7789
    from PIL import Image
    logging.info("Display libraries imported successfully.")
except ImportError as e:
    logging.exception(f"ERROR: Failed Adafruit library import: {e}")
    exit(1)
except Exception as e:
    logging.exception(f"ERROR: Unexpected error during display library imports: {e}")
    exit(1)

# --- Camera Libraries ---
try:
    from uvctypes import *
except ImportError: logging.error("ERROR: Could not import uvctypes.py."); exit(1)
try:
    import cv2
    logging.info("Camera libraries imported successfully.")
except ImportError: logging.error("ERROR: OpenCV not found."); exit(1)
except AttributeError as e:
     if '_ARRAY_API' in str(e): logging.error("\nERROR: OpenCV/NumPy incompatibility! Downgrade NumPy: pip install --upgrade \"numpy<2.0\"");
     else: logging.exception(f"ERROR: Unexpected AttributeError importing OpenCV: {e}")
     exit(1)

# --- Configuration ---
# Camera Config
CAM_WIDTH = 160; CAM_HEIGHT = 120; CAM_FPS = 9
PT_VID = 0x1e4e; PT_PID = 0x0100
try: UVC_FRAME_FORMAT_UYVY
except NameError: UVC_FRAME_FORMAT_UYVY = 4
try: PT_USB_VID
except NameError: PT_USB_VID = 0x1e4e
try: PT_USB_PID
except NameError: PT_USB_PID = 0x0100

# Display Config
LCD_WIDTH = 240; LCD_HEIGHT = 240; LCD_ROTATION = 270
X_OFFSET = 0; Y_OFFSET = 80
SPI_BAUDRATE = 24000000 # 24MHz

# Pin definitions (RPi 3B/3B+/4)
SPI_SCK = board.SCLK; SPI_MOSI = board.MOSI; SPI_CS = board.CE0
DC_PIN = board.D25; RESET_PIN_OBJ = None; BACKLIGHT_PIN = board.D22
BUTTON_A_PIN = board.D23; BUTTON_B_PIN = board.D24

# CPU Governor Settings
CPU_GOVERNOR_SAVE = "powersave"
CPU_GOVERNOR_RUN = "ondemand"

# Temperature Shutdown Config
CPU_TEMP_SENSOR_PATH = "/sys/class/thermal/thermal_zone0/temp"
CPU_TEMP_SHUTDOWN_CHECK_INTERVAL_S = 30 # Check more often for shutdown condition
CPU_TEMP_LOG_INTERVAL_S = 300 # Log temperature every 5 minutes
CPU_TEMP_SHUTDOWN_THRESHOLD_C = 75.0

# Frame queue
BUF_SIZE = 2
frame_queue = Queue(BUF_SIZE)
# --- End Configuration ---


# --- Frame Callback Function (for libuvc) ---
def py_frame_callback(frame, userptr):
    global frame_queue
    try:
        if not frame or not frame.contents.data or frame.contents.width != CAM_WIDTH or frame.contents.height != CAM_HEIGHT: return
        actual_bytes = frame.contents.data_bytes; expected_bytes = CAM_WIDTH * CAM_HEIGHT * 2
        if actual_bytes != expected_bytes: return
        data_type = c_uint8 * actual_bytes
        data_buffer = cast(frame.contents.data, POINTER(data_type)).contents
        frame_data_np = np.frombuffer(data_buffer, dtype=np.uint8).reshape((CAM_HEIGHT, CAM_WIDTH, 2))
        if not frame_queue.full(): frame_queue.put(frame_data_np.copy())
        else:
            try: frame_queue.get_nowait(); frame_queue.put(frame_data_np.copy())
            except QueueEmpty: pass
    except Exception as e:
        # Use logging inside callback for errors
        logging.exception(f"ERROR in py_frame_callback: {e}")

PTR_PY_FRAME_CALLBACK = CFUNCTYPE(None, POINTER(uvc_frame), c_void_p)(py_frame_callback)
# --- End Frame Callback ---

# --- CPU Governor Helper ---
def set_cpu_governor(governor):
    logging.info(f"Attempting to set CPU governor to: {governor}")
    try:
        result = subprocess.run(['sudo', 'cpufreq-set', '-g', governor], check=False, capture_output=True, text=True)
        if result.returncode == 0: logging.info(f"Successfully set CPU governor to {governor}."); return True
        else: logging.warning(f"Failed to set CPU governor to {governor}. Error: {result.stderr.strip()}"); return False
    except FileNotFoundError: logging.error("ERROR: 'cpufreq-set' not found. Install 'cpufrequtils'."); return False
    except Exception as e: logging.exception(f"ERROR: Exception setting CPU governor: {e}"); return False

def verify_cpu_governor(governor):
    try:
        with open('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor', 'r') as f:
            current_governor = f.read().strip()
        return current_governor == governor
    except Exception as e:
        logging.warning(f"Could not verify CPU governor: {e}")
        return False
    
# --- Temperature Reading Helper ---
def get_cpu_temperature():
    try:
        with open(CPU_TEMP_SENSOR_PATH, 'r') as f:
            temp_milli_c = int(f.read().strip())
        return temp_milli_c / 1000.0
    except FileNotFoundError: return None
    except Exception as e: logging.error(f"Could not read CPU temperature: {e}"); return None

# --- Shutdown Helper ---
def shutdown_pi(reason="Overheat"):
    logging.critical(f"!!! {reason} detected! Initiating shutdown NOW! !!!")
    try:
        subprocess.run(['sudo', 'shutdown', '-h', 'now'], check=True)
    except Exception as e:
        logging.exception(f"ERROR: Failed to initiate shutdown: {e}")
        logging.error("       Manual shutdown may be required.")

# --- Main Application Class ---
class LeptonViewer:
    def __init__(self):
        self.disp = None
        self.backlight = None
        self.buttonA = None
        self.buttonB = None
        self.uvc_ctx = POINTER(uvc_context)()
        self.uvc_dev = POINTER(uvc_device)()
        self.uvc_devh = POINTER(uvc_device_handle)()
        self.uvc_ctrl = uvc_stream_ctrl()
        self.is_running = True
        self.display_active = True
        self.stream_is_active = False
        self.frame_count = 0
        self.start_time = time.time()
        self.last_display_time = 0
        self.last_temp_shutdown_check_time = 0
        self.last_temp_log_time = 0

        # Predefined colormaps
        self.colormaps = [
            ("HOT", cv2.COLORMAP_HOT),
            ("BONE", cv2.COLORMAP_BONE),
            ("COOL", cv2.COLORMAP_COOL),
            ("OCEAN", cv2.COLORMAP_OCEAN),
            ("VIRIDIS", cv2.COLORMAP_VIRIDIS),
        ]

        # Add custom LUTs
        self.add_custom_luts()

        self.colormap_index = 0
        self.current_colormap = self.colormaps[self.colormap_index][1]
        self.current_colormap_name = self.colormaps[self.colormap_index][0]

    def create_custom_lut(self, color, color_gradient_step):
        """
        Creates a custom LUT using predefined color data, ensuring
        it is 256 entries and C-contiguous.
        """
        if color_gradient_step <= 0 or color_gradient_step >= 256:
            # Ensure step makes sense for concatenation logic
            raise ValueError("color_gradient_step must be between 1 and 255.")

        if color == 'red':
            # BGR format for color definitions
            gradient_colors = ((64, 0, 0), (255, 0, 0), color_gradient_step) # Blue to Red -> OpenCV Red
        elif color == 'green':
            gradient_colors = ((0, 64, 0), (0, 255, 0), color_gradient_step) # Dark Green to Bright Green
        elif color == 'blue':
            gradient_colors = ((0, 0, 64), (0, 0, 255), color_gradient_step) # Dark Blue to Bright Blue
        else:
            raise ValueError("Unsupported color for LUT creation. Supported: 'red', 'green', 'blue'.")

        BLACK_TO_WHITE_STEP = 256 - color_gradient_step
        # Ensure black_to_white segment is not empty
        if BLACK_TO_WHITE_STEP <= 0:
            logging.warning(f"Color gradient step ({color_gradient_step}) is too large, cannot create black-to-white segment. Adjusting.")
            # Handle edge case - maybe just return a full gradient? Or error?
            # For now, let's just make the gradient fill all 256
            gradient_colors = (gradient_colors[0], gradient_colors[1], 256)
            custom_colors = np.linspace(*gradient_colors).astype(np.uint8)

        else:
            black_to_white = np.linspace((0, 0, 0), (255, 255, 255), BLACK_TO_WHITE_STEP).astype(np.uint8)
            color_gradient = np.linspace(*gradient_colors).astype(np.uint8)
            custom_colors = np.concatenate((black_to_white, color_gradient))


        # Ensure exactly 256 entries. This secondary linspace might lose detail.
        # Consider if the initial concatenation logic should aim for exactly 256.
        if len(custom_colors) != 256:
            logging.warning(f"Custom LUT ({color}) concatenation resulted in {len(custom_colors)} entries. Interpolating to 256 (may lose detail).")
            custom_colors = np.linspace(custom_colors[0], custom_colors[-1], 256, dtype=np.uint8)

        # Reshape to the required (256, 1, 3) format
        custom_lut = custom_colors.reshape((256, 1, 3))

        # *** ADD EXPLICIT CONTIGUITY CHECK/FIX ***
        custom_lut = np.ascontiguousarray(custom_lut)
        logging.debug(f"Created custom LUT '{color}' - Shape: {custom_lut.shape}, Dtype: {custom_lut.dtype}, Contiguous: {custom_lut.flags['C_CONTIGUOUS']}")

        return custom_lut
    
    def add_custom_luts(self):
        """
        Adds custom LUTs to the colormaps list.
        """
        try:
            red_lut = self.create_custom_lut('red', 64)
            self.colormaps.append(("RED_GRADIENT", red_lut))
            logging.info("Added custom LUT: RED_GRADIENT")

            green_lut = self.create_custom_lut('green', 64)
            self.colormaps.append(("GREEN_GRADIENT", green_lut))
            logging.info("Added custom LUT: GREEN_GRADIENT")

            blue_lut = self.create_custom_lut('blue', 64)
            self.colormaps.append(("BLUE_GRADIENT", blue_lut))
            logging.info("Added custom LUT: BLUE_GRADIENT")
        except Exception as e:
            logging.error(f"Error adding custom LUTs: {e}")

    def load_custom_luts(self):
        """
        Scans for .lut files, loads them, ensures 256 entries (resizing
        if needed), applying minimal processing for existing 256-entry LUTs.
        """
        logging.info("Scanning for custom .lut files...")
        loaded_count = 0
        for filename in os.listdir('.'):
            if filename.endswith('.lut'):
                filepath = os.path.join('.', filename)
                logging.debug(f"Found potential LUT file: {filename}")
                try:
                    # ... (file reading, parsing, validation as before) ...
                    with open(filepath, 'r') as f:
                        lut_data = f.read().strip()
                        # ... (validation checks using ast.literal_eval, etc.) ...
                        if not (lut_data.startswith('[') and lut_data.endswith(']')): continue # Simplified validation for brevity
                        lut_values = ast.literal_eval(lut_data)
                        if not isinstance(lut_values, list): continue
                        if not all(isinstance(c, tuple) and len(c) == 3 for c in lut_values): continue

                        lut_name = os.path.splitext(filename)[0].upper()
                        logging.debug(f"Parsing LUT file: {filename} for LUT: {lut_name}")

                        lut_array = np.array(lut_values, dtype=np.uint8) # Shape (N, 3)
                        original_length = len(lut_array)

                        if original_length == 0:
                            logging.warning(f"LUT file {filename} parsed but is empty. Skipping.")
                            continue

                        logging.debug(f"LUT {lut_name} - Original length: {original_length}")

                        # --- Conditional Processing ---
                        if original_length == 256:
                            # Exactly 256 entries: Mimic working script - just reshape.
                            logging.debug(f"LUT {lut_name} has 256 entries. Reshaping directly (no resize, no explicit ascontiguousarray).")
                            try:
                                lut_final = lut_array.reshape((256, 1, 3))
                                # We ASSUME reshape provides sufficient contiguity here, like in the working script.
                                # We skip the explicit np.ascontiguousarray call for this case.
                            except ValueError as e:
                                logging.error(f"Error reshaping LUT {lut_name} even though length is 256? {e}. Skipping.")
                                continue
                        else:
                            # Needs resizing: Use cv2.resize and ensure contiguity after.
                            logging.info(f"LUT {lut_name} needs resizing from {original_length} to 256 entries.")
                            try:
                                lut_image = lut_array.reshape((original_length, 1, 3))
                                lut_resized = cv2.resize(lut_image, (1, 256), interpolation=cv2.INTER_LINEAR)
                                lut_final = lut_resized.reshape((256, 1, 3))
                                # Ensure contiguity AFTER resizing
                                lut_final = np.ascontiguousarray(lut_final)
                            except Exception as e:
                                logging.exception(f"Error resizing LUT {lut_name}: {e}. Skipping.")
                                continue
                        # --- End Conditional Processing ---

                        # Final validation before adding (shape check is still good)
                        # NOTE: We are now intentionally NOT checking lut_final.flags['C_CONTIGUOUS']
                        # for the original_length == 256 case, trusting the reshape was sufficient.
                        if lut_final.shape == (256, 1, 3):
                             # Check contiguity only if resizing occurred (where we explicitly called ascontiguousarray)
                            is_contig_ok = True # Assume ok for direct reshape case
                            if original_length != 256:
                                is_contig_ok = lut_final.flags['C_CONTIGUOUS']

                            if is_contig_ok:
                                self.colormaps.append((lut_name, lut_final))
                                logging.info(f"Successfully loaded and processed custom LUT: {lut_name} (Final size: 256 entries)")
                                loaded_count += 1
                            else:
                                # This should now only trigger if resizing happened and ascontiguousarray failed
                                logging.warning(f"Resized LUT {lut_name} from {filename} was non-contiguous after processing. Skipping.")
                        else:
                            logging.warning(f"Processed LUT {lut_name} from {filename} resulted in invalid shape {lut_final.shape}. Skipping.")

                # ... (exception handling as before) ...
                except FileNotFoundError: logging.error(f"Could not open file {filename}. Skipping.")
                except (SyntaxError, ValueError) as e: logging.error(f"Error parsing LUT file {filename}: {e}. Check format. Skipping.")
                except Exception as e: logging.exception(f"Unexpected error processing LUT file {filename}: {e}. Skipping.")


        logging.info(f"Finished scanning for LUTs. Loaded {loaded_count} custom LUTs.")

    def initialize_display_and_buttons(self):
        logging.info("Initializing Adafruit Mini PiTFT...")
        try:
            self.backlight = digitalio.DigitalInOut(BACKLIGHT_PIN); self.backlight.switch_to_output(); self.backlight.value = self.display_active
            self.buttonA = digitalio.DigitalInOut(BUTTON_A_PIN); self.buttonA.switch_to_input(pull=digitalio.Pull.UP)
            self.buttonB = digitalio.DigitalInOut(BUTTON_B_PIN); self.buttonB.switch_to_input(pull=digitalio.Pull.UP)
            logging.info("Backlight and Buttons Initialized.")
        except Exception as e: logging.exception(f"ERROR initializing digitalio pins: {e}"); exit(1)
        try:
            cs_pin = digitalio.DigitalInOut(SPI_CS); dc_pin = digitalio.DigitalInOut(DC_PIN)
        except Exception as e: logging.exception(f"ERROR initializing display control pins: {e}"); exit(1)
        try:
            try: import displayio; displayio.release_displays()
            except Exception: pass
            spi = busio.SPI(SPI_SCK, MOSI=SPI_MOSI)
        except Exception as e: logging.exception(f"ERROR initializing busio SPI: {e}"); exit(1)
        try:
            self.disp = adafruit_rgb_display_st7789.ST7789( spi, cs=cs_pin, dc=dc_pin, rst=RESET_PIN_OBJ, baudrate=SPI_BAUDRATE, width=LCD_WIDTH, height=LCD_HEIGHT, rotation=LCD_ROTATION, x_offset=X_OFFSET, y_offset=Y_OFFSET)
            logging.info("LCD Initialized via adafruit_rgb_display.st7789.")
            black_img = Image.new("RGB", (self.disp.width, self.disp.height), "black"); self.disp.image(black_img)
            logging.info("Display cleared to black.")
        except Exception as e: logging.exception(f"ERROR initializing display driver: {e}"); exit(1)

    def start_uvc_stream(self):
        if self.stream_is_active: return True
        if not self.uvc_devh: logging.error("Cannot start stream, UVC handle not ready."); return False
        logging.info("Attempting to start UVC stream...")
        res = libuvc.uvc_start_streaming(self.uvc_devh, byref(self.uvc_ctrl), PTR_PY_FRAME_CALLBACK, None, 0)
        if res < 0:
            error_str=f"(Code: {res})"
            try:
                libuvc.uvc_strerror.restype=c_char_p; libuvc.uvc_strerror.argtypes=[c_int]; error_str=libuvc.uvc_strerror(res).decode('utf-8', errors='ignore')
            except Exception: pass
            logging.error(f"uvc_start_streaming failed: {res} {error_str}")
            self.stream_is_active = False; return False
        else:
            logging.info("UVC stream started."); self.stream_is_active = True; self.last_display_time = time.time(); return True

    def clear_frame_queue(self):
        cleared_frames = 0
        while not frame_queue.empty():
            try:
                frame_queue.get_nowait()
                cleared_frames += 1
            except QueueEmpty:
                break
        logging.info(f"Cleared {cleared_frames} frames from the queue.")
        return cleared_frames

    def stop_uvc_stream(self):
        if not self.stream_is_active:
            return
        if not self.uvc_devh:
            logging.warning("UVC device handle is None. Cannot stop stream.")
            return
        try:
            libuvc.uvc_stop_streaming(self.uvc_devh)
            logging.info("UVC stream stopped for power save.")
        except AttributeError as e:
            logging.warning(f"AttributeError stopping UVC stream: {e}")
        except RuntimeError as e:
            logging.warning(f"RuntimeError stopping UVC stream: {e}")
        except Exception as e_stop:
            logging.warning(f"Unexpected error stopping UVC stream: {e_stop}")
        finally:
            self.stream_is_active = False
            # Non-blocking queue clearing with logging
            cleared_frames = 0
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                    cleared_frames += 1
                except QueueEmpty:
                    break
            logging.info(f"Cleared {cleared_frames} frames from the queue.")

    def initialize_camera(self):
        logging.info("Initializing libuvc Camera...")
        res = libuvc.uvc_init(byref(self.uvc_ctx), 0)
        if res < 0: logging.error(f"uvc_init error {res}"); raise RuntimeError(f"uvc_init error {res}")
        res = libuvc.uvc_find_device(self.uvc_ctx, byref(self.uvc_dev), PT_USB_VID, PT_USB_PID, 0)
        if res < 0: logging.error(f"uvc_find_device error {res}"); libuvc.uvc_exit(self.uvc_ctx); raise RuntimeError(f"uvc_find_device error {res}")
        logging.info("Found device.")
        res = libuvc.uvc_open(self.uvc_dev, byref(self.uvc_devh))
        if res < 0: 
            logging.error(f"uvc_open error {res}")
            libuvc.uvc_unref_device(self.uvc_dev); libuvc.uvc_exit(self.uvc_ctx); raise RuntimeError(f"uvc_open error {res}")
        logging.info("Device opened!")
        logging.info(f"Requesting stream control: UYVY W={CAM_WIDTH}, H={CAM_HEIGHT}, FPS={CAM_FPS}")
        res = libuvc.uvc_get_stream_ctrl_format_size(self.uvc_devh, byref(self.uvc_ctrl), UVC_FRAME_FORMAT_UYVY, CAM_WIDTH, CAM_HEIGHT, CAM_FPS)
        if res < 0:
            error_str=f"(Code: {res})"
            try: libuvc.uvc_strerror.restype=c_char_p; libuvc.uvc_strerror.argtypes=[c_int]; error_str=libuvc.uvc_strerror(res).decode('utf-8', errors='ignore')
            except Exception: pass
            msg=f"uvc_get_stream_ctrl_format_size failed: {res} {error_str}"
            logging.error(msg); self.cleanup(); raise RuntimeError(msg)
        logging.info("uvc_get_stream_ctrl_format_size OK for UYVY.")
        logging.info("Camera Initialized (Stream not started yet).")


    def process_and_display(self):
        """Main loop to get frames from queue, process, and display."""
        if not self.disp:
            logging.error("Display not initialized.")
            return

        set_cpu_governor(CPU_GOVERNOR_RUN if self.display_active else CPU_GOVERNOR_SAVE)
        if self.display_active:
            self.start_uvc_stream()

        logging.info("Starting thermal viewer loop...")
        self.start_time = time.time()
        self.frame_count = 0
        self.last_temp_shutdown_check_time = time.time()
        self.last_temp_log_time = time.time()

        # Initialize timestamps for button debouncing
        self.last_button_a_time = 0
        self.last_button_b_time = 0

        while self.is_running:
            current_time = time.time()
            try:
                # --- Check Temperature ---
                if current_time - self.last_temp_shutdown_check_time > CPU_TEMP_SHUTDOWN_CHECK_INTERVAL_S:
                    cpu_temp = get_cpu_temperature()
                    if cpu_temp is not None:
                        if current_time - self.last_temp_log_time > CPU_TEMP_LOG_INTERVAL_S:
                            logging.info(f"CPU Temp: {cpu_temp:.2f} C")
                            self.last_temp_log_time = current_time
                        if cpu_temp > CPU_TEMP_SHUTDOWN_THRESHOLD_C:
                            self.is_running = False
                            shutdown_pi(f"CPU Temp {cpu_temp:.1f}C > Threshold {CPU_TEMP_SHUTDOWN_THRESHOLD_C:.1f}C")
                            time.sleep(10)
                            break
                    self.last_temp_shutdown_check_time = current_time
                # --- End Temperature Check ---

                # Check Buttons
                button_a_pressed = self.buttonA and not self.buttonA.value
                button_b_pressed = self.buttonB and not self.buttonB.value

                # Debounce Button A
                if button_a_pressed and (current_time - self.last_button_a_time > 0.3):  # 0.3 seconds debounce
                    self.last_button_a_time = current_time
                    self.colormap_index = (self.colormap_index + 1) % len(self.colormaps)
                    self.current_colormap = self.colormaps[self.colormap_index][1]
                    self.current_colormap_name = self.colormaps[self.colormap_index][0]
                    logging.info(f"Button A Pressed: Colormap -> {self.current_colormap_name}")

                # Debounce Button B
                if button_b_pressed and (current_time - self.last_button_b_time > 0.3):  # 0.3 seconds debounce
                    self.last_button_b_time = current_time
                    self.display_active = not self.display_active
                    self.backlight.value = self.display_active
                    logging.info(f"Button B Pressed: Toggled display {'ON' if self.display_active else 'OFF'}")
                    if self.display_active:
                        set_cpu_governor(CPU_GOVERNOR_RUN)
                        self.start_uvc_stream()
                    else:
                        self.stop_uvc_stream()
                        set_cpu_governor(CPU_GOVERNOR_SAVE)
                        black_img = Image.new("RGB", (self.disp.width, self.disp.height), "black")
                        self.disp.image(black_img)

                # --- Frame Handling ---
                frame_uyvy = None
                if self.display_active and self.stream_is_active:
                    try:
                        frame_uyvy = frame_queue.get(block=True, timeout=0.5)
                    except QueueEmpty:
                        continue

                if self.display_active and frame_uyvy is not None:
                    # --- Process UYVY Frame ---
                    # Convert UYVY (YUV 4:2:2) to BGR
                    frame_bgr = cv2.cvtColor(frame_uyvy, cv2.COLOR_YUV2BGR_UYVY)
                    # Convert BGR to Grayscale
                    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

                    # Automatic Gain Control (AGC) on grayscale
                    min_val, max_val, _, _ = cv2.minMaxLoc(frame_gray)
                    if max_val > min_val:
                        # Apply scaling for contrast enhancement
                        alpha = 255.0 / (max_val - min_val)
                        beta = -min_val * alpha
                        frame_gray_agc = cv2.convertScaleAbs(frame_gray, alpha=alpha, beta=beta)
                    else:
                        # Avoid division by zero if frame is flat
                        frame_gray_agc = frame_gray # Should be uint8, single channel

                    # Apply colormap or custom LUT
                    if isinstance(self.current_colormap, np.ndarray):  # Custom LUT (NumPy array)
                        # +++ DETAILED DEBUG LOGGING +++
                        logging.debug(f"--- Applying Custom LUT: {self.current_colormap_name} ---")
                        logging.debug(f"Input frame_gray_agc - Type: {type(frame_gray_agc)}, Shape: {frame_gray_agc.shape}, Dtype: {frame_gray_agc.dtype}, Flags:\n{frame_gray_agc.flags}")
                        logging.debug(f"Custom LUT - Type: {type(self.current_colormap)}")
                        logging.debug(f"Custom LUT - Shape: {self.current_colormap.shape}")
                        logging.debug(f"Custom LUT - Size (Total Elements): {self.current_colormap.size}") # np.size gives total elements
                        logging.debug(f"Custom LUT - Dtype: {self.current_colormap.dtype}")
                        logging.debug(f"Custom LUT - Flags:\n{self.current_colormap.flags}")
                        # +++ END DEBUG LOGGING +++

                        # Explicitly check conditions required by cv2.LUT before calling it
                        is_correct_shape = self.current_colormap.shape == (256, 1, 3)
                        is_uint8 = self.current_colormap.dtype == np.uint8
                        # Check C_CONTIGUOUS flag specifically
                        is_contig = self.current_colormap.flags['C_CONTIGUOUS'] # CORRECT
                        # Total elements must be 256 * 3 = 768 for (256, 1, 3) LUT
                        is_total_768 = self.current_colormap.size == 768

                        # Check input frame type as well (part of OpenCV assertion)
                        is_input_uint8 = frame_gray_agc.dtype == np.uint8

                        if is_correct_shape and is_uint8 and is_contig and is_total_768 and is_input_uint8:
                            try:
                                # All checks passed, attempt to apply LUT using cv2.applyColorMap
                                # This function CAN accept a custom (256, 1, 3) uint8 LUT array.
                                frame_color = cv2.applyColorMap(frame_gray_agc, self.current_colormap) # <--- USE applyColorMap INSTEAD OF LUT
                            except cv2.error as map_e: # Changed variable name e -> map_e
                                # Log error even if checks passed
                                # Error message might be different if applyColorMap fails
                                logging.exception(f"!!! cv2.applyColorMap error! LUT Name: {self.current_colormap_name}. Error: {map_e}")
                                # Fallback: display grayscale directly or skip frame
                                frame_color = cv2.cvtColor(frame_gray_agc, cv2.COLOR_GRAY2BGR) # Display grayscale on error
                        else:
                            # Log details if any check failed
                            logging.error(f"!!! Custom LUT '{self.current_colormap_name}' failed validation right before use:")
                            logging.error(f"    Input uint8 OK?  {is_input_uint8} (Dtype: {frame_gray_agc.dtype})")
                            logging.error(f"    LUT Shape OK?    {is_correct_shape} (Shape: {self.current_colormap.shape})")
                            logging.error(f"    LUT Dtype OK?    {is_uint8} (Dtype: {self.current_colormap.dtype})")
                            logging.error(f"    LUT Contig OK?   {is_contig} (Flags: {self.current_colormap.flags})")
                            logging.error(f"    LUT Size OK?     {is_total_768} (Size: {self.current_colormap.size})")
                            # Fallback: display grayscale directly
                            frame_color = cv2.cvtColor(frame_gray_agc, cv2.COLOR_GRAY2BGR) # Display grayscale on error

                    else:  # Standard OpenCV colormap (integer ID)
                        logging.debug(f"Applying OpenCV Colormap ID: {self.current_colormap} (Name: {self.current_colormap_name})")
                        # Apply built-in OpenCV colormap
                        frame_color = cv2.applyColorMap(frame_gray_agc, self.current_colormap)

                    # --- Resize and Display ---
                    # Resize the colored frame to fit the LCD display
                    frame_resized = cv2.resize(frame_color, (self.disp.width, self.disp.height), interpolation=cv2.INTER_LINEAR)
                    # Convert BGR (OpenCV default) to RGB (for PIL/Display)
                    img_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                    # Create PIL image from NumPy array
                    img_pil = Image.fromarray(img_rgb)
                    # Display image on the LCD
                    self.disp.image(img_pil)
                    # --- End Frame Processing and Display ---

                    # Update frame count and timing for FPS calculation
                    self.frame_count += 1
                    self.last_display_time = current_time
                    # Calculate FPS periodically
                    elapsed_time = current_time - self.start_time
                    if elapsed_time >= 5.0: # Calculate every 5 seconds
                        fps = self.frame_count / elapsed_time
                        self.frame_count = 0
                        self.start_time = current_time
                        logging.debug(f"Display FPS: {fps:.2f}") # Log FPS if needed

                elif not self.display_active:
                     # If display is off, sleep briefly to avoid busy-waiting
                    time.sleep(0.25)

            except KeyboardInterrupt:
                self.is_running = False
                logging.info("Exiting loop due to KeyboardInterrupt.")
            except Exception as e:
                logging.exception(f"Error in main processing loop: {e}")
                time.sleep(0.1)

    def run(self):
        logging.info("Application run started.")
        set_cpu_governor(CPU_GOVERNOR_RUN) # Ensure run governor on start
        try:
            self.initialize_display_and_buttons()
            self.load_custom_luts()
            self.initialize_camera()
            self.process_and_display()
        except KeyboardInterrupt: logging.info("Exiting application due to KeyboardInterrupt.")
        except Exception as e: logging.exception(f"An unexpected error occurred during setup or run: {e}")
        finally:
            self.is_running = False
            set_cpu_governor(CPU_GOVERNOR_RUN) # Ensure run governor on exit
            self.cleanup()

    def cleanup(self):
        logging.info("Cleaning up resources...")
        self.stop_uvc_stream()
        # ... (libuvc cleanup code as before) ...
        if self.uvc_devh:
            try: libuvc.uvc_close(self.uvc_devh); logging.info("UVC device handle closed.")
            except Exception as e: logging.warning(f"Error closing UVC handle: {e}")
        self.uvc_devh = None
        if self.uvc_dev:
            try: libuvc.uvc_unref_device(self.uvc_dev); logging.info("UVC device unreferenced.")
            except Exception as e: logging.warning(f"Error unref device: {e}")
        self.uvc_dev = None
        if self.uvc_ctx:
             try: libuvc.uvc_exit(self.uvc_ctx); logging.info("UVC context exited.")
             except Exception as e: logging.warning(f"Error exiting UVC context: {e}")
        self.uvc_ctx = None
        # ... (GPIO cleanup code as before) ...
        if self.buttonA:
             try: self.buttonA.deinit(); logging.info("Button A deinit.")
             except Exception as e: logging.warning(f"Error deinit Button A: {e}")
        if self.buttonB:
             try: self.buttonB.deinit(); logging.info("Button B deinit.")
             except Exception as e: logging.warning(f"Error deinit Button B: {e}")
        if self.backlight:
             try:
                  self.backlight.value = False # Ensure backlight is off
                  self.backlight.deinit(); logging.info("Backlight pin deinit (OFF).")
             except Exception as e: logging.warning(f"Error deinit backlight: {e}")
        self.buttonA = None; self.buttonB = None; self.backlight = None
        # ... (Display cleanup code as before) ...
        if self.disp:
            try:
                logging.info("Clearing display (fill black)...")
                black_img = Image.new("RGB", (self.disp.width, self.disp.height), "black")
                self.disp.image(black_img)
                logging.info("Display cleared.")
            except Exception as e: logging.error(f"Error clearing display: {e}")
        self.disp = None
        logging.info("Cleanup finished.")
        logging.info("--- Script Finished ---")


# --- Run Application ---
if __name__ == '__main__':
    app = LeptonViewer()
    app.run()
