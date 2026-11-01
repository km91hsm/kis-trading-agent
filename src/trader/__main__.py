"""`python -m trader` 진입점."""


def main() -> None:
    from trader.kernel.cli.main import app

    app()


if __name__ == "__main__":
    main()
