#!/bin/sh
set -e
black --check src/pymusicassembler tests scripts
pylint src/pymusicassembler tests scripts
pytest --cov=pymusicassembler --cov-report=term-missing --cov-fail-under=85
