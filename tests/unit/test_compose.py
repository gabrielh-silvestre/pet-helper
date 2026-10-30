import json
import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "compose.yaml"
DOCKERFILE_PATH = ROOT / "Dockerfile"
README_PATH = ROOT / "README.md"

OLLAMA_MODEL_EXPRESSION = "${PET_HELPER_OLLAMA_MODEL:-llama3.1:8b}"
COMPOSE_DEFAULT_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-?([^}]+)\}$")
NO_KEY_HEADING_PATTERN = re.compile(
    r"\b(?:sem\s+chave|sem\s+api[- ]?key|no[- ]key|without\s+(?:an?\s+)?(?:api\s+)?key|ollama)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedDockerfile:
    workdir: str | None
    env: dict[str, str]
    kit_dirs: frozenset[str]


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


def _raw_env_map(service: dict[str, Any]) -> dict[str, str]:
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
    return entries


def _env_map(service: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, val in _raw_env_map(service).items():
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

    global_args: dict[str, str] = {}
    stage_kits_by_key: dict[str, set[str]] = {}
    stage_index = -1
    stage_name: str | None = None
    workdir: str | None = None
    env: dict[str, str] = {}
    args: dict[str, str] = {}
    kit_dirs: set[str] = set()

    def _expand(value: str) -> str:
        scope = {**args, **env}
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: scope.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )

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

    return _ParsedDockerfile(
        workdir=workdir,
        env=env,
        kit_dirs=frozenset(kit_dirs),
    )


def _readme_sections() -> list[tuple[str, str]]:
    readme = _load_readme()
    sections: list[tuple[str, str]] = []
    for match in re.finditer(r"^(#{2,4})\s+(.+)$", readme, re.MULTILINE):
        level = len(match.group(1))
        heading = match.group(2).strip()
        tail = readme[match.end() :]
        next_heading = re.search(rf"^#{{1,{level}}}\s+", tail, re.MULTILINE)
        body = tail[: next_heading.start()] if next_heading else tail
        sections.append((heading, re.sub(r"\\\n\s*", " ", body)))
    return sections


def _kit_reference_date() -> str:
    kit_orders = json.loads((ROOT / "kit" / "orders.json").read_text(encoding="utf-8"))
    return kit_orders["reference_date"]


def _load_readme() -> str:
    assert README_PATH.is_file(), "README.md does not exist"
    return README_PATH.read_text(encoding="utf-8")


def test_it_should_pin_the_ollama_service_image_to_0_40_12():
    compose = _load_compose()

    ollama = _service(compose, "ollama")

    assert ollama.get("image") == "ollama/ollama:0.40.12"


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


def test_it_should_pull_the_model_named_by_pet_helper_ollama_model_in_the_init_service():
    compose = _load_compose()
    _, init_service = _init_service(compose)

    invocation = _init_invocation(init_service)

    assert invocation.split()[-1] == OLLAMA_MODEL_EXPRESSION


def test_it_should_give_the_app_service_the_same_ollama_model_as_the_pull():
    compose = _load_compose()
    app = _service(compose, "app")

    env = _raw_env_map(app)

    assert env.get("PET_HELPER_OLLAMA_MODEL") == OLLAMA_MODEL_EXPRESSION


def test_it_should_not_set_the_anthropic_model_variable_on_the_app_service():
    compose = _load_compose()
    app = _service(compose, "app")

    env = _raw_env_map(app)

    assert "PET_HELPER_ASSISTANT_MODEL" not in env


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


def test_it_should_resolve_the_copied_kit_directory_inside_the_container():
    dockerfile = _parse_dockerfile()

    configured_kit = dockerfile.env.get("PET_HELPER_KIT_DIR", "kit")
    resolved_kit = posixpath.normpath(
        posixpath.join(dockerfile.workdir or "/", configured_kit)
    )

    assert dockerfile.workdir is not None or "PET_HELPER_KIT_DIR" in dockerfile.env
    assert resolved_kit in dockerfile.kit_dirs


def test_it_should_document_docker_compose_run_in_a_no_key_readme_section():
    matching_sections = [
        heading
        for heading, body in _readme_sections()
        if NO_KEY_HEADING_PATTERN.search(heading)
        and re.search(r"\bdocker\s+compose\s+run\b[^\n]*\bapp\b", body)
    ]

    assert matching_sections != []


def test_it_should_override_the_ollama_model_with_pet_helper_ollama_model_in_the_readme_low_ram_example():
    readme = _load_readme()

    has_example = (
        "PET_HELPER_OLLAMA_MODEL=llama3.2:3b docker compose run --rm app" in readme
    )

    assert has_example is True


def test_it_should_include_the_built_with_llama_attribution_in_the_readme():
    readme = _load_readme()

    has_attribution = "Built with Llama" in readme

    assert has_attribution is True


def test_it_should_default_pet_helper_today_to_the_kit_reference_date_on_the_app_service():
    compose = _load_compose()
    app = _service(compose, "app")

    env = _env_map(app)

    assert env.get("PET_HELPER_TODAY") == _kit_reference_date()


def test_it_should_let_the_host_override_pet_helper_today_on_the_app_service():
    compose = _load_compose()
    app = _service(compose, "app")

    raw_value = _raw_env_map(app).get("PET_HELPER_TODAY", "")

    match = COMPOSE_DEFAULT_PATTERN.match(raw_value)
    assert match is not None
    assert match.group(1) == "PET_HELPER_TODAY"


def test_it_should_document_the_native_chat_command_with_the_kit_reference_date_in_the_readme():
    readme = _load_readme()

    command = f"PET_HELPER_TODAY={_kit_reference_date()} uv run pet-helper chat"

    assert command in readme


@pytest.mark.parametrize(
    "variable", ["PET_HELPER_PROVIDER", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL"]
)
def test_it_should_list_the_provider_variables_in_the_native_setup_section(variable):
    native_sections = [
        body
        for heading, body in _readme_sections()
        if re.search(r"\bnativo\b", heading, re.IGNORECASE)
    ]

    assert any(variable in body for body in native_sections)


def test_it_should_tell_the_reader_to_run_the_command_again_after_a_model_unavailable_failure():
    readme = _load_readme()

    mentions_failure = "model_unavailable" in readme

    assert mentions_failure is True
