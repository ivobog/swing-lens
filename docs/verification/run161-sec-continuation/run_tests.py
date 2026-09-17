"""Run certification against disposable databases on the configured local server."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main():
    import pytest
    from sqlalchemy.engine import make_url

    from app.settings import get_settings

    admin = make_url(get_settings().database_url).set(database="postgres", drivername="postgresql")
    os.environ["SWINGLENS_TEST_POSTGRES_ADMIN_URL"] = admin.render_as_string(hide_password=False)
    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
