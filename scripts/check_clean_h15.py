"""H15 clean migration: an additional empty database, existing databases untouched."""

import check_clean_h12

check_clean_h12.TARGET = "digital_station_h15_clean_test"

if __name__ == "__main__":
    check_clean_h12.main()
