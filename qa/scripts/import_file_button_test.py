import subprocess
import pyautogui
import time
from pathlib import Path

# ----------------------------
# Base directory (repo-safe)
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent

# ----------------------------
# Paths (ALL RELATIVE)
# ----------------------------

APP_PATH = BASE_DIR / "PDF Text extractor tool 1.0.0.exe"
IMPORT_BTN_IMG = BASE_DIR / "Import_file.png"
START_BTN_IMG = BASE_DIR / "start_button.png"

PDF_PATH = BASE_DIR / "1. Handwritten_Image.pdf"


# ----------------------------
# Launch App
# ----------------------------
subprocess.Popen(str(APP_PATH))
time.sleep(30)


# ----------------------------
# Helper function
# ----------------------------
def find_and_click(image_path, confidence=0.7, name="element"):
    location = pyautogui.locateOnScreen(str(image_path), confidence=confidence)

    if location is None:
        pyautogui.screenshot(f"debug_{name}.png")
        raise Exception(f"{name} not found: {image_path}")

    x, y = pyautogui.center(location)
    pyautogui.moveTo(x, y, duration=1)
    pyautogui.click()


# ----------------------------
# Step 1: Click Import Button
# ----------------------------
find_and_click(IMPORT_BTN_IMG, confidence=0.6, name="import_button")

time.sleep(5)

# ----------------------------
# Step 2: Upload PDF
# ----------------------------
pyautogui.write(str(PDF_PATH))
pyautogui.press("enter")

time.sleep(3)
print("PDF uploaded successfully")


# ----------------------------
# Step 3: Click Start Button
# ----------------------------
find_and_click(START_BTN_IMG, confidence=0.7, name="start_button")

print("Test completed successfully")