"""Fetch or refresh a noon store listing from the command line.

    python -m noon_store <store link> [folder]      fetch every product (folder defaults to input_data)
    python -m noon_store --refresh <listing.xlsx>   add the store's new arrivals
"""
import sys

from . import StoreError, fetch_store, refresh_store


def main(argv):
    try:
        if len(argv) == 2 and argv[0] == "--refresh":
            refresh_store(argv[1])
        elif len(argv) in (1, 2) and not argv[0].startswith("-"):
            fetch_store(argv[0], argv[1] if len(argv) == 2 else "input_data")
        else:
            print(__doc__)
            return 2
    except StoreError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
