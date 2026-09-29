#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
sh ./run.sh "$@"
run_result=$?
if [ "$run_result" -ne 0 ]; then
    echo "Automatic setup or launch failed. Check the message above. Press Enter to close."
    read -r ignored
fi
exit "$run_result"
