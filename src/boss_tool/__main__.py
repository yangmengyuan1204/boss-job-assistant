"""模块入口，支持 `python -m boss_tool` 调用。"""

from boss_tool.cli import app


def main() -> None:
    """CLI 入口函数。"""
    app()


if __name__ == "__main__":
    main()
