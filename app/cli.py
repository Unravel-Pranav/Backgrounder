import subprocess
import sys


def main():
    """Entry point for `backgrounder` CLI command."""
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        check=True,
    )


if __name__ == "__main__":
    main()
