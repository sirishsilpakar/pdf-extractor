import os
import sys

try:
    import PyInstaller.__main__
except ImportError:
    print(
        "PyInstaller is not installed. Please install it with: pip install pyinstaller"
    )
    sys.exit(1)


def build():
    # Make sure we run from the project root
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)

    print("Building pdf-extractor-backend with PyInstaller...")

    PyInstaller.__main__.run(
        [
            "server.py",
            "--name",
            "pdf-extractor-backend",
            "--onedir",  # Much faster startup time than onefile, better for Electron
            "--noconfirm",  # Overwrite output directory without asking
            "--clean",
            # Collect all files/submodules for complex dynamic modules
            "--collect-all",
            "uvicorn",
            "--collect-all",
            "fastapi",
            "--collect-all",
            "pydantic",
            # Additional hidden imports
            "--hidden-import",
            "sqlite3",
            "--hidden-import",
            "pytesseract",
            "--hidden-import",
            "services.ocr.tesseract",
            "--hidden-import",
            "services.ocr.base",
        ]
    )

    print("\nBuild complete! Output is in the 'dist/pdf-extractor-backend' directory.")
    print("When running from Electron, ensure you set:")
    print("  BASE_DIR")
    print("  TESSERACT_CMD")
    print("  TESSDATA_PREFIX")


if __name__ == "__main__":
    build()
