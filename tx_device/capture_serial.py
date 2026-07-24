import argparse

import serial


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("output")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.2) as source:
        with open(args.output, "a", encoding="utf-8", buffering=1) as sink:
            while True:
                data = source.readline()
                if data:
                    sink.write(data.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
