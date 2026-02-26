import os
import shutil
import time
from logging.handlers import TimedRotatingFileHandler


class WindowsSafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    Windows-safe rotating handler that uses copy+truncate instead of os.rename().

    Python's standard TimedRotatingFileHandler calls os.rename() during rollover,
    which fails on Windows with PermissionError [WinError 32] when any thread still
    has the log file open. This handler copies the log to the rotated filename and
    then truncates the original in place, avoiding the rename entirely.
    """

    def doRollover(self):
        self.acquire()
        try:
            if self.stream:
                self.stream.flush()
                self.stream.close()
                self.stream = None

            # Determine the rotated filename (same logic as base class)
            t = self.rolloverAt - self.interval
            dfn = self.rotation_filename(
                self.baseFilename + "." + time.strftime(self.suffix, time.localtime(t))
            )

            # Copy current log to rotated name, then truncate original in place
            if os.path.exists(self.baseFilename):
                if not os.path.exists(dfn):
                    shutil.copy2(self.baseFilename, dfn)
                # Truncate to zero bytes — no rename, so Windows won't block this
                with open(self.baseFilename, "w"):
                    pass

            # Remove old backup files beyond backupCount
            if self.backupCount > 0:
                for old_file in self.getFilesToDelete():
                    os.remove(old_file)

            # Reopen the stream for continued writing
            self.mode = "a"
            self.stream = self._open()

            # Advance the next rollover time
            current_time = int(time.time())
            new_rollover_at = self.computeRollover(current_time)
            while new_rollover_at <= current_time:
                new_rollover_at += self.interval
            self.rolloverAt = new_rollover_at
        finally:
            self.release()
