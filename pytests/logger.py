class MyLogger:
    def __init__(self):
        pass

    def info(self, msg):
        print(f"INFO: {msg}")

    def error(self, msg):
        print(f"ERROR: {msg}")

    def debug(self, msg):
        print(f"DEBUG: {msg}")

    def warning(self, msg):
        print(f"WARNING: {msg}")

    def trace(self, msg):
        print(f"TRACE: {msg}")


def get_logger(*args, **kwargs):
    return MyLogger()
