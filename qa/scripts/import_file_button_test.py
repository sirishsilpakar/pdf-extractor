import subprocess
import pyautogui
import time
import os

APP_PATH = r"C:\Users\luthr\Downloads\artifacts (1)\release\PDF Text extractor tool 1.0.0.exe"

# Launch app
subprocess.Popen(APP_PATH)

time.sleep(15)  # better: wait for window in real tests

# locate the upload file button and click 
import_but = pyautogui.locateOnScreen("import_file.png", confidence=0.8)

if import_but is None:
    raise Exception("Import button not found")

x, y = pyautogui.center(import_but)

pyautogui.moveTo(x, y, duration=1)
pyautogui.click()

time.sleep(5)

# Select file
pyautogui.write(r"C:\Users\luthr\Documents\Samples\Scanned PDF\1. Handwritten_Image.pdf")
pyautogui.press("enter")

time.sleep(3)

print("PDF uploaded successfully")

# locate the upload file button and click 
import_but = pyautogui.locateOnScreen("start_button.png", confidence=0.7)

if import_but is None:
    raise Exception("Import button not found")

x, y = pyautogui.center(import_but)

pyautogui.moveTo(x, y, duration=1)
pyautogui.click()