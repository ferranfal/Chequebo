"""A local 12-second workload: CPU, RAM, disk and a child process.

Run from Chequebo: python3 examples/workload.py
"""

import multiprocessing as mp
import tempfile
import time


def worker():
    memory = bytearray(64 * 1024 * 1024)
    for offset in range(0, len(memory), 4096):
        memory[offset] = 1
    end = time.monotonic() + 5
    while time.monotonic() < end:
        sum(i * i for i in range(10000))


if __name__ == "__main__":
    print("Chequebo: starting example workload", flush=True)
    child = mp.Process(target=worker)
    child.start()
    memory = bytearray(80 * 1024 * 1024)
    for offset in range(0, len(memory), 4096):
        memory[offset] = 1
    with tempfile.TemporaryFile() as output:
        output.write(memory)
        output.flush()
        import os
        os.fsync(output.fileno())
        time.sleep(3)
    child.join()
    del memory
    print("Memory released; waiting to show the drop", flush=True)
    time.sleep(6)
    print("Example finished", flush=True)

