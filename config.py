from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")

DEFAULT = {"real_device": False}

TRUTHY = ("1", "true", "yes", "on", "real")
FALSY = ("0", "false", "no", "off", "emu", "sim")


def load() -> dict:
    return dict(DEFAULT)


def save(cfg: dict):
    return None


def set_real(value: bool) -> bool:
    return bool(value)


def resolve_real(flag=None) -> bool:
    env = os.environ.get("OSR_REAL", "").strip().lower()
    if env in TRUTHY:
        return True
    if env in FALSY:
        return False
    if flag is not None:
        return bool(flag)
    return False


def mode_name(real: bool) -> str:
    return "REAL 真机" if real else "EMU 模拟器"


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--show", "-s"):
        real = resolve_real()
        print(f"当前模式：{mode_name(real)}")
        print("说明：模式**不持久化**，每次启动都回到「模拟器」。")
        env = os.environ.get("OSR_REAL")
        if env:
            print(f"环境变量 OSR_REAL = {env}（优先级最高）")
        if os.path.exists(CONFIG_FILE):
            print(f"注意：{os.path.basename(CONFIG_FILE)} 存在但已不再生效，可以删掉。")
        print("\n切真机：界面上点「切到真机」，或 python daemon.py --real --port COM5")
        return 0

    if args[0] in ("--real", "--on", "--emu", "--off"):
        print("已废弃：模式不再保存到磁盘。")
        print("  要连真机：界面上点「切到真机」，或 python daemon.py --real --port COM5")
        print("  要回模拟器：关掉重开即可（默认就是模拟器）")
        return 0

    print(f"不认识的参数：{args[0]}")
    print("用法：python config.py [--show]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
