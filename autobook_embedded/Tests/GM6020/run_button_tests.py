from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name("run_uart_sequence_tests.py")), run_name="__main__")
