# Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import importlib.resources
from typing import ClassVar, Sequence, cast

from pants.backend.python.target_types import ConsoleScript, EntryPoint, MainSpecification
from pants.backend.python.util_rules.interpreter_constraints import InterpreterConstraints
from pants.backend.python.util_rules.pex import PexRequirements
from pants.engine.fs import FileContent
from pants.option.errors import OptionsError
from pants.option.subsystem import Subsystem


class PythonToolRequirementsBase(Subsystem):
    """Base class for subsystems that configure a set of requirements for a python tool."""

    # Subclasses must set.
    default_version: ClassVar[str]
    # Subclasses do not need to override.
    default_extra_requirements: ClassVar[Sequence[str]] = []

    default_interpreter_constraints: ClassVar[Sequence[str]] = []
    register_interpreter_constraints: ClassVar[bool] = False

    # If this tool does not mix with user requirements (e.g. Flake8 and Isort, but not Pylint and
    # Pytest), you should set this to True.
    #
    # You also need to subclass `PythonToolLockfileSentinel` and create a rule that goes from
    # it -> PythonToolLockfileRequest by calling `PythonLockFileRequest.from_python_tool()`.
    # Register the UnionRule.
    register_lockfile: ClassVar[bool] = False
    default_lockfile_resource: ClassVar[tuple[str, str] | None] = None
    default_lockfile_url: ClassVar[str | None] = None

    @classmethod
    def register_options(cls, register):
        super().register_options(register)
        register(
            "--version",
            type=str,
            advanced=True,
            default=cls.default_version,
            help="Requirement string for the tool.",
        )
        register(
            "--extra-requirements",
            type=list,
            member_type=str,
            advanced=True,
            default=cls.default_extra_requirements,
            help="Any additional requirement strings to use with the tool. This is useful if the "
            "tool allows you to install plugins or if you need to constrain a dependency to "
            "a certain version.",
        )

        if cls.default_interpreter_constraints and not cls.register_interpreter_constraints:
            raise ValueError(
                f"`default_interpreter_constraints` are configured for `{cls.options_scope}`, but "
                "`register_interpreter_constraints` is not set to `True`, so the "
                "`--interpreter-constraints` option will not be registered. Did you mean to set "
                "this?"
            )
        if cls.register_interpreter_constraints:
            register(
                "--interpreter-constraints",
                type=list,
                advanced=True,
                default=cls.default_interpreter_constraints,
                help="Python interpreter constraints for this tool.",
            )

        if cls.register_lockfile and (
            not cls.default_lockfile_resource or not cls.default_lockfile_url
        ):
            raise ValueError(
                "The class property `default_lockfile_resource` and `default_lockfile_url` "
                f"must be set if `register_lockfile` is set. See `{cls.options_scope}`."
            )
        if cls.register_lockfile and not cls.register_interpreter_constraints:
            # TODO(#12314): Figure out how to determine the interpreter constraints for tools that
            #  get ICs from code, like FLake8 and Bandit. We could theoretically scan the repo to
            #  see all constraints (`py-constraints` goal). What should the default lockfile we
            #  bundle use?
            raise ValueError(
                "For now, you cannot set `register_lockfile` without also setting "
                f"`register_interpreter_constraints`. See `{cls.options_scope}`."
            )
        if cls.register_lockfile:
            register(
                "--experimental-lockfile",
                type=str,
                default="<none>",
                advanced=True,
                help=(
                    "Path to a lockfile used for installing the tool.\n\n"
                    "Set to the string '<default>' to use a lockfile provided by "
                    "Pants, so long as you have not changed the `--version`, "
                    "`--extra-requirements`, and `--interpreter-constraints` options. See "
                    f"{cls.default_lockfile_url} for the default lockfile contents.\n\n"
                    "Set to the string '<none>' to opt out of using a lockfile. We do not "
                    "recommend this, as lockfiles are essential for reproducible builds.\n\n"
                    "To use a custom lockfile, set this option to a file path relative to the "
                    "build root, then activate the backend_package "
                    "`pants.backend.experimental.python` and run `./pants tool-lock`.\n\n"
                    "This option is experimental and will likely change. It does not follow the "
                    "normal deprecation cycle."
                ),
            )

    @property
    def version(self) -> str:
        return cast(str, self.options.version)

    @property
    def extra_requirements(self) -> tuple[str, ...]:
        return tuple(self.options.extra_requirements)

    @property
    def all_requirements(self) -> tuple[str, ...]:
        """All the raw requirement strings to install the tool.

        This may not include transitive dependencies: these are top-level requirements.
        """
        return (self.version, *self.extra_requirements)

    @property
    def pex_requirements(self) -> PexRequirements:
        """The requirements to be used when installing the tool.

        If the tool supports lockfiles, the returned type will install from the lockfile rather than
        `all_requirements`.
        """
        if not self.register_lockfile or self.lockfile == "<none>":
            return PexRequirements(self.all_requirements)
        if self.lockfile == "<default>":
            assert self.default_lockfile_resource is not None
            return PexRequirements(
                file_content=FileContent(
                    f"{self.options_scope}_default_lockfile.txt",
                    importlib.resources.read_binary(*self.default_lockfile_resource),
                ),
                is_lockfile=True,
            )
        return PexRequirements(
            file_path=self.lockfile,
            file_path_description_of_origin=(
                f"the option `[{self.options_scope}].experimental_lockfile`"
            ),
            is_lockfile=True,
        )

    @property
    def lockfile(self) -> str:
        """The path to a lockfile or special strings '<none>' and '<default>'.

        This assumes you have set the class property `register_lockfile = True`.
        """
        return cast(str, self.options.experimental_lockfile)

    @property
    def interpreter_constraints(self) -> InterpreterConstraints:
        """The interpreter constraints to use when installing and running the tool.

        This assumes you have set the class property `register_interpreter_constraints = True`.
        """
        return InterpreterConstraints(self.options.interpreter_constraints)


class PythonToolBase(PythonToolRequirementsBase):
    """Base class for subsystems that configure a python tool to be invoked out-of-process."""

    # Subclasses must set.
    default_main: ClassVar[MainSpecification]

    @classmethod
    def register_options(cls, register):
        super().register_options(register)
        register(
            "--console-script",
            type=str,
            advanced=True,
            default=cls.default_main.spec if isinstance(cls.default_main, ConsoleScript) else None,
            help=(
                "The console script for the tool. Using this option is generally preferable to "
                "(and mutually exclusive with) specifying an --entry-point since console script "
                "names have a higher expectation of staying stable across releases of the tool. "
                "Usually, you will not want to change this from the default."
            ),
        )
        register(
            "--entry-point",
            type=str,
            advanced=True,
            default=cls.default_main.spec if isinstance(cls.default_main, EntryPoint) else None,
            help=(
                "The entry point for the tool. Generally you only want to use this option if the "
                "tool does not offer a --console-script (which this option is mutually exclusive "
                "with). Usually, you will not want to change this from the default."
            ),
        )

    @property
    def main(self) -> MainSpecification:
        is_default_console_script = self.options.is_default("console_script")
        is_default_entry_point = self.options.is_default("entry_point")
        if not is_default_console_script and not is_default_entry_point:
            raise OptionsError(
                f"Both [{self.scope}].console-script={self.options.console_script} and "
                f"[{self.scope}].entry-point={self.options.entry_point} are configured but these "
                f"options are mutually exclusive. Please pick one."
            )
        if not is_default_console_script:
            return ConsoleScript(cast(str, self.options.console_script))
        if not is_default_entry_point:
            return EntryPoint.parse(cast(str, self.options.entry_point))
        return self.default_main
