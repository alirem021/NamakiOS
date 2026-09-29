"""Visible startup report, including stack traces when startup hangs."""
import faulthandler
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent


class Tee:
    def __init__(self, console, report):
        self.console, self.report = console, report
    def write(self, text):
        self.report.write(text)
        self.report.flush()
        return self.console.write(text)
    def flush(self):
        self.report.flush()
        self.console.flush()
    def __getattr__(self, name):
        return getattr(self.console, name)


def run(main, append=False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    report_path = ROOT / 'debug-report.txt'
    stack_path = ROOT / 'debug-stacks.txt'
    stdout, stderr = sys.stdout, sys.stderr
    with report_path.open('a' if append else 'w', encoding='utf-8') as report, stack_path.open('a' if append else 'w', encoding='utf-8') as stacks:
        sys.stdout, sys.stderr = Tee(stdout, report), Tee(stderr, report)
        print(f'NamakiOS diagnostic report: {report_path}\nStarted: {time.ctime()}', flush=True)
        faulthandler.enable(file=stacks)
        faulthandler.dump_traceback_later(45, repeat=True, file=stacks)
        try:
            result = main()
            print(f'Launcher finished: {result}', flush=True)
            return result
        except BaseException:
            traceback.print_exc()
            return 1
        finally:
            faulthandler.cancel_dump_traceback_later()
            state = os.environ.get('NAMAKI_STATE_DIR')
            if state:
                for name in ('chromium.log', 'desktop.log', 'qemu.log'):
                    path = Path(state) / name
                    if path.exists():
                        report.write(f'\n--- {path} (last 32 KiB) ---\n')
                        with path.open('rb') as source:
                            source.seek(max(0, path.stat().st_size - 32768))
                            report.write(source.read().decode('utf-8', 'replace'))
            report.flush()
            faulthandler.disable()
            sys.stdout, sys.stderr = stdout, stderr
            print(f'Diagnostic files: {report_path} and {stack_path}', flush=True)
