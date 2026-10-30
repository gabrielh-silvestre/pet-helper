import json
import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "compose.yaml"
DOCKERFILE_PATH = ROOT / "Dockerfile"
README_PATH = ROOT / "README.md"

COMPOSE_DEFAULT_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-?([^}]+)\}$")
PULL_DIRECT_DEFAULT_PATTERN = re.compile(
    r"(?:^|[\s/])ollama\s+pull\s+[\"']?\$\{([A-Za-z_][A-Za-z0-9_]*):-?([^}\"']+)\}[\"']?(?:\s|$)"
)
PULL_ENV_REF_PATTERN = re.compile(
    r"(?:^|[\s/])ollama\s+pull\s+[\"']?\$\$?\{?([A-Za-z_][A-Za-z0-9_]*)\}?[\"']?(?:\s|$)"
)
NO_KEY_HEADING_PATTERN = re.compile(
    r"\b(?:sem\s+chave|sem\s+api[- ]?key|no[- ]key|without\s+(?:an?\s+)?(?:api\s+)?key|ollama)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedDockerfile:
    run_commands: tuple[str, ...]
    workdir: str | None
    env: dict[str, str]
    kit_dirs: frozenset[str]
    entrypoint: tuple[str, ...]
    cmd: tuple[str, ...]


def _load_compose() -> dict[str, Any]:
    assert COMPOSE_PATH.is_file(), "compose.yaml does not exist"
    loaded = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), "compose.yaml must define a mapping"
    return loaded


def _service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    services = compose.get("services")
    assert isinstance(services, dict), "compose.yaml has no services mapping"
    service = services.get(name)
    assert isinstance(service, dict), f"compose.yaml has no '{name}' service"
    return service


def _command_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(part) for part in value).strip()
    return ""


def _env_map(
    service: dict[str, Any], *, resolve_defaults: bool = True
) -> dict[str, str]:
    raw = service.get("environment")
    entries: dict[str, str] = {}
    if isinstance(raw, dict):
        entries = {
            str(key): "" if val is None else str(val) for key, val in raw.items()
        }
    elif isinstance(raw, list):
        for item in raw:
            key, sep, val = str(item).partition("=")
            entries[key] = val if sep else ""
    if not resolve_defaults:
        return entries
    resolved: dict[str, str] = {}
    for key, val in entries.items():
        match = COMPOSE_DEFAULT_PATTERN.match(val)
        resolved[key] = match.group(2) if match else val
    return resolved


def _init_invocation(service: dict[str, Any]) -> str:
    # ollama/ollama sets ENTRYPOINT ["/bin/ollama"], so omitting entrypoint prepends ollama
    entrypoint = service.get("entrypoint")
    command = _command_text(service.get("command"))
    if entrypoint is None and str(service.get("image", "")).startswith("ollama/ollama"):
        return f"ollama {command}".strip()
    return f"{_command_text(entrypoint)} {command}".strip()


def _init_service(compose: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    services = compose.get("services")
    assert isinstance(services, dict), "compose.yaml has no services mapping"
    candidates = [
        (str(name), cfg)
        for name, cfg in services.items()
        if name not in {"ollama", "app"} and isinstance(cfg, dict)
    ]
    assert candidates, "compose.yaml defines no init service"
    for name, cfg in candidates:
        if "pull" in _init_invocation(cfg):
            return name, cfg
    return candidates[0]


def _parse_dockerfile() -> _ParsedDockerfile:
    assert DOCKERFILE_PATH.is_file(), "Dockerfile does not exist"
    raw_lines = DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()

    logical_lines: list[str] = []
    buffer = ""
    for raw_line in raw_lines:
        if raw_line.lstrip().startswith("#"):
            continue
        stripped_right = raw_line.rstrip()
        if stripped_right.endswith("\\"):
            buffer += f"{stripped_right[:-1]} "
            continue
        full_line = f"{buffer}{stripped_right}".strip()
        buffer = ""
        if full_line:
            logical_lines.append(full_line)
    if buffer.strip():
        logical_lines.append(buffer.strip())

    run_commands: list[str] = []
    global_args: dict[str, str] = {}
    stage_kits_by_key: dict[str, set[str]] = {}
    stage_index = -1
    stage_name: str | None = None
    workdir: str | None = None
    env: dict[str, str] = {}
    args: dict[str, str] = {}
    kit_dirs: set[str] = set()
    entrypoint: list[str] = []
    cmd: list[str] = []

    def _expand(value: str) -> str:
        scope = {**args, **env}
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: scope.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )

    def _parse_exec_or_shell(raw_arg: str) -> list[str]:
        stripped = raw_arg.strip()
        tokens = (
            [str(item) for item in json.loads(stripped)]
            if stripped.startswith("[")
            else shlex.split(stripped)
        )
        if "-c" in tokens:
            idx = tokens.index("-c")
            if idx + 1 < len(tokens):
                tokens = [
                    *tokens[: idx + 1],
                    *shlex.split(tokens[idx + 1]),
                    *tokens[idx + 2 :],
                ]
        return tokens

    for line in logical_lines:
        keyword, _, rest = line.partition(" ")
        instruction = keyword.upper()
        argument = rest.strip()

        if instruction == "FROM":
            if stage_index >= 0:
                stage_kits_by_key[str(stage_index)] = set(kit_dirs)
                if stage_name:
                    stage_kits_by_key[stage_name] = set(kit_dirs)
                    stage_kits_by_key[stage_name.lower()] = set(kit_dirs)
            stage_index += 1
            as_match = re.search(
                r"\bAS\s+([A-Za-z0-9._-]+)\s*$", argument, re.IGNORECASE
            )
            stage_name = as_match.group(1) if as_match else None
            workdir = None
            env = {}
            args = dict(global_args)
            kit_dirs = set()
            entrypoint = []
            cmd = []
        elif instruction == "ARG":
            target_args = global_args if stage_index < 0 else args
            for token in shlex.split(argument):
                if "=" in token:
                    key, _, val = token.partition("=")
                    target_args[key] = _expand(val)
        elif instruction == "WORKDIR":
            resolved_target = _expand(argument).strip("\"'")
            workdir = posixpath.normpath(
                posixpath.join(workdir or "/", resolved_target)
            )
        elif instruction == "ENV":
            first_token = argument.split(maxsplit=1)[0] if argument else ""
            if "=" in first_token:
                for token in shlex.split(argument):
                    if "=" in token:
                        key, _, val = token.partition("=")
                        env[key] = _expand(val)
            elif argument:
                key, _, val = argument.partition(" ")
                env[key] = _expand(val.strip()).strip("\"'")
        elif instruction == "RUN":
            run_commands.append(argument)
        elif instruction == "COPY":
            from_stage: str | None = None
            parents = False
            remaining = argument
            while remaining.startswith("--"):
                flag, _, after_flag = remaining.partition(" ")
                if flag.startswith("--from="):
                    from_stage = flag.removeprefix("--from=")
                elif flag == "--parents":
                    parents = True
                remaining = after_flag.lstrip()
            raw_paths = (
                [str(item) for item in json.loads(remaining)]
                if remaining.startswith("[")
                else shlex.split(remaining)
            )
            paths = [_expand(item) for item in raw_paths]
            if len(paths) >= 2:
                # Docker COPY <dir> <dest> copies the directory's contents into <dest>
                sources, dest = paths[:-1], paths[-1]
                dest_dir = posixpath.normpath(posixpath.join(workdir or "/", dest))
                if from_stage is None:
                    for src in sources:
                        norm_src = posixpath.normpath(src)
                        if norm_src == ".":
                            kit_dirs.add(
                                posixpath.normpath(posixpath.join(dest_dir, "kit"))
                            )
                        elif norm_src == "kit":
                            kit_dirs.add(
                                posixpath.normpath(posixpath.join(dest_dir, "kit"))
                                if parents
                                else dest_dir
                            )
                else:
                    prior_kits = stage_kits_by_key.get(
                        from_stage, set()
                    ) | stage_kits_by_key.get(from_stage.lower(), set())
                    for src in sources:
                        src_abs = posixpath.normpath(posixpath.join("/", src))
                        for prior_kit in prior_kits:
                            if src_abs == prior_kit:
                                kit_dirs.add(dest_dir)
                            elif prior_kit.startswith(src_abs.rstrip("/") + "/"):
                                rel = posixpath.relpath(prior_kit, src_abs)
                                kit_dirs.add(
                                    posixpath.normpath(posixpath.join(dest_dir, rel))
                                )
        elif instruction == "ENTRYPOINT":
            entrypoint = _parse_exec_or_shell(argument)
            cmd = []
        elif instruction == "CMD":
            cmd = _parse_exec_or_shell(argument)

    return _ParsedDockerfile(
        run_commands=tuple(run_commands),
        workdir=workdir,
        env=env,
        kit_dirs=frozenset(kit_dirs),
        entrypoint=tuple(entrypoint),
        cmd=tuple(cmd),
    )


def _load_readme() -> str:
    assert README_PATH.is_file(), "README.md does not exist"
    return README_PATH.read_text(encoding="utf-8")


def test_it_should_pin_the_ollama_service_image_to_0_40_12():
    compose = _load_compose()

    ollama = _service(compose, "ollama")

    assert ollama.get("image") == "ollama/ollama:0.40.12"


def test_it_should_pin_every_declared_service_image_in_compose():
    compose = _load_compose()
    services = compose.get("services") or {}

    unpinned = [
        image
        for service in services.values()
        if isinstance(service, dict) and (image := service.get("image"))
        if ":" not in str(image).rsplit("/", maxsplit=1)[-1]
        or str(image).endswith(":latest")
    ]

    assert services != {}
    assert unpinned == []


def test_it_should_run_ollama_list_in_the_ollama_service_healthcheck():
    compose = _load_compose()
    ollama = _service(compose, "ollama")

    healthcheck = ollama.get("healthcheck") or {}
    check_command = _command_text(healthcheck.get("test"))

    assert not healthcheck.get("disable")
    assert re.search(r"(?:^|[\s/])ollama\s+list(?:\s|$)", check_command) is not None


def test_it_should_wait_for_a_healthy_ollama_before_starting_the_init_service():
    compose = _load_compose()
    _, init_service = _init_service(compose)

    depends_on = init_service.get("depends_on")
    ollama_dependency = (
        depends_on.get("ollama") if isinstance(depends_on, dict) else None
    )

    assert isinstance(ollama_dependency, dict)
    assert ollama_dependency.get("condition") == "service_healthy"


def test_it_should_pull_the_model_with_ollama_pull_in_the_init_service():
    compose = _load_compose()
    _, init_service = _init_service(compose)

    invocation = _init_invocation(init_service)

    assert not invocation.startswith("ollama ollama ")
    assert re.search(r"(?:^|[\s/])ollama\s+pull\s+\S+", invocation) is not None


def test_it_should_default_the_pulled_model_tag_to_llama3_1_8b_with_variable_override():
    compose = _load_compose()
    _, init_service = _init_service(compose)

    invocation = _init_invocation(init_service)
    direct_match = PULL_DIRECT_DEFAULT_PATTERN.search(invocation)
    if direct_match:
        override_var, default_tag = direct_match.groups()
    else:
        ref_match = PULL_ENV_REF_PATTERN.search(invocation)
        raw_env = _env_map(init_service, resolve_defaults=False)
        env_expr = raw_env.get(ref_match.group(1), "") if ref_match else ""
        env_default = COMPOSE_DEFAULT_PATTERN.match(env_expr)
        override_var, default_tag = (
            env_default.groups() if env_default else (None, None)
        )

    assert override_var is not None
    assert default_tag == "llama3.1:8b"


def test_it_should_wait_for_the_init_service_to_complete_before_starting_app():
    compose = _load_compose()
    init_name, _ = _init_service(compose)
    app = _service(compose, "app")

    depends_on = app.get("depends_on")
    init_dependency = (
        depends_on.get(init_name) if isinstance(depends_on, dict) else None
    )

    assert isinstance(init_dependency, dict)
    assert init_dependency.get("condition") == "service_completed_successfully"


def test_it_should_build_the_app_service_from_the_dockerfile():
    compose = _load_compose()
    app = _service(compose, "app")

    build = app.get("build")
    if isinstance(build, str):
        context, dockerfile = posixpath.normpath(build), "Dockerfile"
    elif isinstance(build, dict):
        context = posixpath.normpath(str(build.get("context", ".")))
        dockerfile = posixpath.normpath(str(build.get("dockerfile", "Dockerfile")))
    else:
        context, dockerfile = None, None

    assert (context, dockerfile) == (".", "Dockerfile")


def test_it_should_set_ollama_base_url_on_the_app_service():
    compose = _load_compose()
    app = _service(compose, "app")

    env = _env_map(app)

    assert env.get("OLLAMA_BASE_URL") == "http://ollama:11434"


def test_it_should_set_pet_helper_provider_to_ollama_on_the_app_service():
    compose = _load_compose()
    app = _service(compose, "app")

    env = _env_map(app)

    assert env.get("PET_HELPER_PROVIDER") == "ollama"


def test_it_should_enable_interactive_terminal_io_on_the_app_service():
    compose = _load_compose()

    app = _service(compose, "app")

    assert (app.get("stdin_open"), app.get("tty")) == (True, True)


def test_it_should_install_locked_dependencies_with_uv_sync_in_the_dockerfile():
    dockerfile = _parse_dockerfile()

    locked_sync_runs = [
        run_cmd
        for run_cmd in dockerfile.run_commands
        if re.search(r"\buv\s+sync\b(?=.*--locked\b)", run_cmd)
    ]

    assert locked_sync_runs != []


def test_it_should_resolve_the_copied_kit_directory_inside_the_container():
    dockerfile = _parse_dockerfile()

    configured_kit = dockerfile.env.get("PET_HELPER_KIT_DIR", "kit")
    resolved_kit = posixpath.normpath(
        posixpath.join(dockerfile.workdir or "/", configured_kit)
    )

    assert dockerfile.workdir is not None or "PET_HELPER_KIT_DIR" in dockerfile.env
    assert resolved_kit in dockerfile.kit_dirs


def test_it_should_set_the_dockerfile_entrypoint_to_pet_helper_chat():
    dockerfile = _parse_dockerfile()

    entrypoint_names = [PurePosixPath(token).name for token in dockerfile.entrypoint]
    full_command_names = [
        PurePosixPath(token).name for token in [*dockerfile.entrypoint, *dockerfile.cmd]
    ]

    assert "pet-helper" in entrypoint_names
    assert any(
        full_command_names[i : i + 2] == ["pet-helper", "chat"]
        for i in range(len(full_command_names) - 1)
    )


def test_it_should_document_docker_compose_run_in_a_no_key_readme_section():
    readme = _load_readme()

    sections: list[tuple[str, str]] = []
    for match in re.finditer(r"^(#{2,4})\s+(.+)$", readme, re.MULTILINE):
        level = len(match.group(1))
        heading = match.group(2).strip()
        tail = readme[match.end() :]
        next_heading = re.search(rf"^#{{1,{level}}}\s+", tail, re.MULTILINE)
        body = tail[: next_heading.start()] if next_heading else tail
        sections.append((heading, re.sub(r"\\\n\s*", " ", body)))
    matching_sections = [
        heading
        for heading, body in sections
        if NO_KEY_HEADING_PATTERN.search(heading)
        and re.search(r"\bdocker\s+compose\s+run\b[^\n]*\bapp\b", body)
    ]

    assert matching_sections != []


def test_it_should_document_pet_helper_kit_dir_in_the_readme():
    readme = _load_readme()

    match = re.search(r"\bPET_HELPER_KIT_DIR\b", readme)

    assert match is not None


def test_it_should_include_the_built_with_llama_attribution_in_the_readme():
    readme = _load_readme()

    has_attribution = "Built with Llama" in readme

    assert has_attribution is True
