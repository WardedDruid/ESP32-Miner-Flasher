import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    from gui.app import ESPFlasherApp
    app = ESPFlasherApp()
    app.mainloop()


if __name__ == "__main__":
    # When frozen by PyInstaller, sys.executable is the .exe itself.
    # Subprocess calls that need esptool pass --run-esptool as the first arg
    # so we dispatch to esptool here before any GUI code loads.
    if getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1] == "--run-esptool":
        import esptool
        esptool.main(argv=sys.argv[2:])
        sys.exit(0)
    main()
