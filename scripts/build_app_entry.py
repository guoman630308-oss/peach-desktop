"""打包 EXE 的入口：第一个参数是 `peach` 子命令就走 CLI，否则起托盘。

判据必须从 `peach.cli.subcommands()` 现算，不硬编码一份子命令集合。写死
`{"serve", "migrate"}` 的话，`ledger-sync`、`status` 在 EXE 里完全不可达，而且
不报错——参数被当成托盘参数吞掉，用户看到的是又起了一个托盘，不是「没有这个命令」。
"""
import ctypes
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

def cli_main(argv):
    from peach.cli import main
    return main(argv)


def subcommands():
    from peach.cli import subcommands as commands
    return commands()


def tray_main():
    from peach.error_reporting import tray_main as run
    return run()

_ATTACH_PARENT_PROCESS = -1
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12


def _open_std_handle(handle_id: int):
    try:
        handle = ctypes.windll.kernel32.GetStdHandle(handle_id)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return None
        import msvcrt

        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        return os.fdopen(fd, "w", buffering=1, encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        return None


def _prepare_console() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    if sys.stdout is not None and sys.stderr is not None:
        from peach.error_reporting import console_ready
        console_ready()
        return
    try:
        ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS)
    except (AttributeError, OSError):
        pass
    devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _open_std_handle(_STD_OUTPUT_HANDLE) or devnull
    sys.stderr = _open_std_handle(_STD_ERROR_HANDLE) or devnull
    from peach.error_reporting import console_ready
    console_ready()


def cli_commands() -> frozenset[str]:
    return subcommands()


def wants_cli(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] in cli_commands()


def installer_reporting(usage: str, errors: str) -> int:
    from peach import remote_reporting
    if usage not in {"0", "1"} or errors not in {"0", "1"}:
        return 2
    remote_reporting.save({"usage": usage == "1", "errors": errors == "1"})
    return 0


def installer_command(argv: list[str]) -> int:
    """执行安装辅助协议，启动与故障只在本机取证。"""
    from peach import remote_reporting, error_reporting
    remote_reporting.disable_for_process()
    if len(argv) == 4 and argv[1] == "--installer-reporting":
        return installer_reporting(argv[2], argv[3])
    if len(argv) == 3 and argv[1] == "--installer-library-sharing":
        if argv[2] not in {"0", "1"}:
            return 2
        from peach import library_sharing, settings_file
        library_sharing.save_choice(settings_file.active(), argv[2] == "1")
        return 0
    error_reporting.install()
    if len(argv) == 2 and argv[1] in {"--installer-stop", "--installer-uninstall"}:
        from peach.desktop_installer import main as run_installer
        return run_installer(argv[1])
    if len(argv) == 5 and argv[1] == "--installer-prepare":
        from peach.desktop_installer import prepare
        return prepare(Path(argv[2]), int(argv[3]), Path(argv[4]))
    if len(argv) == 5 and argv[1] == "--installer-tools":
        from peach.desktop_installer import prepare_install_tools
        return prepare_install_tools(Path(argv[2]), Path(argv[3]), Path(argv[4]))
    if len(argv) == 4 and argv[1] == "--installer-finish":
        from peach.desktop_installer import finish_install
        return finish_install(Path(argv[2]), int(argv[3]))
    if len(argv) == 3 and argv[1] == "--installer-migrate":
        from peach.desktop_installer import migrate_install
        return migrate_install(Path(argv[2]))
    raise ValueError("安装辅助参数无效")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from peach.distribution import embedded_window
    if "--data-root" in argv:
        index = argv.index("--data-root")
        if index + 1 >= len(argv):
            raise ValueError("--data-root 缺少目录")
        os.environ["PEACH_DATA_ROOT"] = argv[index + 1]
        del argv[index:index + 2]
        sys.argv = argv
    if embedded_window():
        from peach.native_shell import prepare_environment
        prepare_environment()
    if len(argv) > 1 and argv[1].startswith("--installer-"):
        return installer_command(argv)
    from peach import error_reporting
    error_reporting.install()
    if len(argv) in (2, 3) and argv[1] == "--diagnostic-report":
        from peach.support_bundle import save_bundle
        path = save_bundle(Path(argv[2]) if len(argv) == 3 else None)
        if len(argv) == 2 and os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, f"诊断报告已保存：\n{path}", "Peach 诊断报告", 0x40)
        elif sys.stdout is not None:
            print(path)
        return 0
    if len(argv) == 3 and argv[1] == "--tray-watchdog":
        from peach.tray_lifecycle import main as watchdog_main
        return watchdog_main(argv[2:])
    if len(argv) == 4 and argv[1] == "--apply-standalone-update":
        from peach.standalone_update import apply
        return apply(Path(argv[2]), int(argv[3]))
    if wants_cli(argv):
        _prepare_console()
        try:
            return cli_main(argv[1:])
        except Exception as error:
            error_reporting.event("packaged-cli", error)
            import traceback
            traceback.print_exc()
            return 1
    if embedded_window():
        from peach.native_shell import main as window_main
        return window_main(argv[1:])
    return tray_main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from peach import error_reporting
    try:
        raise SystemExit(main())
    except Exception as error:
        message = error_reporting.failure_details("desktop-entry", error)
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "Peach 启动失败", 0x10)
        elif sys.stderr is not None:
            print(message, file=sys.stderr)
        raise SystemExit(1)
