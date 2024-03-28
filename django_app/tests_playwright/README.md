# Automated Accessibility Testing using Playwright and Axe

The tests are currently located at `django_app/tests_playwright`. All commands below should be run from this directory.

## Setup

`poetry add pytest-playwright`

`playwright install`

`poetry add axe-playwright-python`

## Running tests

`pytest -s`

Commandline options are documented here: https://playwright.dev/python/docs/running-tests
