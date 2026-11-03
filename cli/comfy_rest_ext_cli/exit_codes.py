"""Stable CLI exit codes for agent callers."""

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_NETWORK = 5
EXIT_CONFLICT = 6


def exit_code_for(status: int) -> int:
    if status == 0:
        return EXIT_NETWORK
    if status in (2, 400, 422):
        return EXIT_USAGE
    if status in (401, 403):
        return EXIT_AUTH
    if status == 404:
        return EXIT_NOT_FOUND
    if status == 409:
        return EXIT_CONFLICT
    if status == 429 or status >= 500:
        return EXIT_NETWORK
    return EXIT_FAILURE
