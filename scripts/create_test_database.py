"""Create only the dedicated H0 test DB; never drop/reset any database."""

import psycopg
from sqlalchemy.engine import make_url

from digital_station.settings import Settings


def main():
    url = make_url(Settings().database_url.get_secret_value())
    with psycopg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
        autocommit=True,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", ("digital_station_h0_test",)
        ).fetchone()
        if exists:
            print("digital_station_h0_test already exists; no data changed")
        else:
            connection.execute("CREATE DATABASE digital_station_h0_test")
            print("Created isolated digital_station_h0_test")


if __name__ == "__main__":
    main()
