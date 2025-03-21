#!/bin/sh

cat /src/main.c
echo "---"
sed -i 's#blue#red#' /src/main.c
echo "---"
cat /src/main.c
