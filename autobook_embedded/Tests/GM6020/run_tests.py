from pathlib import Path
import subprocess, tempfile, shutil
here = Path(__file__).resolve().parent
project = here.parents[1]
gcc = shutil.which("gcc")
if not gcc:
    raise SystemExit("Install GCC or add it to PATH to run host tests.")
with tempfile.TemporaryDirectory() as tmp:
    output = Path(tmp) / "gm6020_test.exe"
    subprocess.run([gcc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I" + str(here), "-I" + str(project / "Core/Inc"),
                    str(project / "Core/Src/gm6020.c"), str(here / "test.c"),
                    "-o", str(output)], check=True)
    subprocess.run([str(output)], check=True)
