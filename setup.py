import sys
import os
from cx_Freeze import setup, Executable

base_dir = os.path.dirname(os.path.abspath(__file__))

build_exe_options = {
    "packages": [
        "PIL",
        "mss",
        "openai",
        "sqlite3",
        "json",
        "tkinter",
    ],
    "includes": [
        "paths",
        "capture",
        "ocr",
        "models",
        "storage",
        "gui",
        "logger",
    ],
    "include_files": [
        ("config.json", "config.json"),
    ],
    "excludes": [
        "flask",
        "jinja2",
        "werkzeug",
        "markupsafe",
        "pip",
        "setuptools",
        "unittest",
        "pydoc",
        "doctest",
    ],
}

base = "gui" if sys.platform == "win32" else None

setup(
    name="ChatSight",
    version="1.0",
    description="ChatSight - AI 聊天智能助手",
    options={"build_exe": build_exe_options},
    executables=[Executable("main.py", base=base, target_name="ChatSight.exe")],
)
