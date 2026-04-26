"""Runtime identity constants for the ductor-slack fork."""

from __future__ import annotations

APP_SLUG = "ductor-slack"
CLI_COMMAND = APP_SLUG
PACKAGE_NAME = APP_SLUG
DEFAULT_HOME_DIRNAME = ".ductor-slack"
DEFAULT_HOME = f"~/{DEFAULT_HOME_DIRNAME}"
DEFAULT_DOCKER_IMAGE = "ductor-slack-sandbox"
DEFAULT_DOCKER_CONTAINER = DEFAULT_DOCKER_IMAGE
DEFAULT_API_PORT = 18741
DEFAULT_WEBHOOK_PORT = 18742
DEFAULT_INTERAGENT_PORT = 18799
SERVICE_NAME = APP_SLUG
MACOS_LAUNCHD_LABEL = "dev.ductor-slack"
UPSTREAM_REPOSITORY = "PleasePrompto/ductor"
