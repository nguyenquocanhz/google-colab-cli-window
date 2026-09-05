# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
from typing import Optional

import click
import typer
from typer.core import TyperGroup
from typing_extensions import Annotated

from colab_cli import auto_update
from colab_cli.auth import AuthProvider
from colab_cli.common import state, setup_logging
from colab_cli.commands import session, execution, files, automation, run, ssh, utility


class AlphabeticalGroup(TyperGroup):
    """A `TyperGroup` that lists subcommands alphabetically in `--help` output.

    Subcommands are registered in functional groups (session, execution, files,
    automation, utility), but users discovering the CLI via `colab --help` /
    `colab help` benefit from a deterministic, alphabetical listing.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(super().list_commands(ctx))


app = typer.Typer(
    help="Colab CLI",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=AlphabeticalGroup,
)


@app.callback()
def callback(
    ctx: typer.Context,
    client_oauth_config: Annotated[
        str,
        typer.Option(
            "-c", "--client-oauth-config", help="Path to client OAuth config JSON file"
        ),
    ] = os.path.expanduser("~/.colab-cli-oauth-config.json"),
    config: Annotated[
        Optional[str],
        typer.Option(
            "--config",
            # Deliberately no absolute path here: Typer wraps this help into a
            # narrow column and an interpolated home directory gets ellipsised
            # to uselessness. Name the variable instead -- it is the thing the
            # reader can act on, and it stays correct wherever the files are.
            help=(
                "Path to session state file. Defaults to sessions.json in "
                "the CLI config directory (see COLAB_CLI_HOME). Moves the "
                "sessions only -- set COLAB_CLI_HOME to move the "
                "credentials with them."
            ),
        ),
    ] = None,
    logtostderr: Annotated[
        bool, typer.Option("--logtostderr", help="Log all output to stderr")
    ] = False,
    auth: Annotated[
        AuthProvider,
        typer.Option(
            "--auth",
            help=(
                "Authentication strategy to use: 'oauth2' (public InstalledAppFlow),"
                " or 'adc' (Application Default Credentials)."
            ),
            case_sensitive=False,
        ),
    ] = AuthProvider.OAUTH2,
):
    """
    Colab CLI global configuration.
    """
    state.client_oauth_config = client_oauth_config
    state.config_path = config
    state.logtostderr = logtostderr
    state.auth_provider = auth
    setup_logging(logtostderr)

    # Daily fetch + cached banner on every invocation.
    #
    # Suppress the banner for short-lived informational subcommands so their
    # output stays clean and machine-parseable:
    #   - `update`: runs its own check + announce; would duplicate the banner.
    #   - `version`, `log`, `pay`, `help`, `url`: pure-display commands whose
    #     output users routinely pipe / scrape (e.g. `colab url -s s1 | xclip`);
    #     a stochastic upgrade banner injected once a day would corrupt those
    #     pipelines.
    #   - `whoami`: developer-only debugging tool; banner would obscure the
    #     auth/scope info the user invoked it to see.
    _AUTO_UPDATE_SUPPRESSED = {
        "update",
        "version",
        "log",
        "pay",
        "help",
        "url",
        "whoami",
        "readme",
        "README",
        "skill",
        "SKILL",
    }
    if ctx.invoked_subcommand not in _AUTO_UPDATE_SUPPRESSED:
        auto_update.run_background_check()


@app.command(name="help")
def help_command(
    ctx: typer.Context,
    command: Annotated[
        Optional[str], typer.Argument(help="Command to show help for")
    ] = None,
):
    """
    Show help for a command.
    """
    if not command:
        typer.echo(ctx.parent.get_help())
        return

    group = ctx.parent.command
    cmd = group.get_command(ctx, command)
    if cmd is None:
        typer.echo(f"No such command '{command}'.", err=True)
        raise typer.Exit(code=2)

    with click.Context(cmd, info_name=command, parent=ctx.parent) as cmd_ctx:
        typer.echo(cmd.get_help(cmd_ctx))


# Register subcommands
session.register(app)
execution.register(app)
files.register(app)
automation.register(app)
run.register(app)
ssh.register(app)
utility.register(app)


def _force_utf8_streams():
    """Make stdout/stderr UTF-8 before any command runs.

    Kernel output is arbitrary user text, and `display_output` writes it
    straight to `sys.stdout`. A Windows console defaults to the ANSI codepage
    (cp1252 on a Western install), so a single accented character raised
    `UnicodeEncodeError` and killed the command AFTER the VM had already done
    the work -- the result was lost to the printer, not to the job. Hit for
    real: `colab exec` on a script logging Vietnamese died on `ấ`.

    `errors="replace"` is deliberate. Losing one glyph to a placeholder beats
    losing a whole run, and this path only ever formats output for a human.
    """
    for stream in (sys.stdout, sys.stderr):
        # Not every stream is a TextIOWrapper: pytest's capture objects and
        # some IDE consoles substitute their own, and those have no
        # `reconfigure`. Leaving them alone is correct -- they are not the
        # console codepage that causes this.
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main():
    _force_utf8_streams()
    app()


if __name__ == "__main__":
    main()
