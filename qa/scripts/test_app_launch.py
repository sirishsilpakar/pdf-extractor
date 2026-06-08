import subprocess
import time
import os

def test_application_launch():

    # Launch application
    subprocess.Popen(
        r"C:\Users\luthr\Downloads\artifacts (1)\release\PDF Text extractor tool 1.0.0.exe"
    )
    
    # Wait for startup
    time.sleep(5)

test_application_launch()