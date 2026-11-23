import json
import os
import signal
import time
import core


def main():
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    core.initialize()
    core.recover()
    marker = core.RUNTIME / 'worker.json'
    marker.write_text(json.dumps({'pid':os.getpid()}))
    try:
        while running:
            task_id = core.claim()
            if task_id:
                core.execute(task_id)
            else:
                time.sleep(0.5)
    finally:
        marker.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
