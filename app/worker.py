from app.services.background_worker import run_worker


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
