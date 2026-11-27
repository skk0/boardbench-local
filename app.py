"""Launch BoardBench from source or the packaged desktop executable."""

import os
import sys

if __name__ == "__main__" and sys.argv[1:2] == ["--esp-worker"]:
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w"))
    import esptool

    sys.argv.pop(1)
    esptool._main()
    sys.exit(0)

from boardbench.server import main, make_app


if __name__ == "__main__":
    main()
