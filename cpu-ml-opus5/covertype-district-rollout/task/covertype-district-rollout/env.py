"""HUD environment wrapper for covertype-district-rollout.

The task itself is Harbor-format (task.toml / environment/ / solution/ / tests/). This file
is the thin HUD adapter around it — it does not reimplement anything and it does not relax
anything.

The privilege wall is preserved exactly:
  * `hud serve` runs as root inside the task image, so the env process holds the held-out
    district and the action log.
  * the agent's shell is dropped to uid 1000 (`ubuntu`) via `shell_uid`, which is the same
    uid the Harbor task assumes, so the sudoers rule (`ubuntu` may run only /usr/local/bin/envd)
    and the root:envdata 0750 on /opt/env both still apply.
  * grading runs here, in the root env process, by invoking the same tests/grade.py the
    Harbor task ships. The agent's final text is ignored: the score comes from the
    environment-owned action log and frozen terminal, never from anything the agent says.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from hud.environment import Environment

env = Environment(name="covertype-district-rollout")

# Resolved lazily and from either location: `hud eval tasks.py` imports this module ON THE
# HOST to enumerate tasks, where /app does not exist. Reading at import time made the module
# unimportable there and killed the run before any rollout.
_INSTRUCTION_PATHS = (Path(__file__).resolve().parent / "instruction.md",
                      Path("/app/instruction.md"))


def _instruction() -> str:
    for q in _INSTRUCTION_PATHS:
        if q.exists():
            return q.read_text()
    raise FileNotFoundError(f"instruction.md not found in {_INSTRUCTION_PATHS}")
GRADER = "/opt/grade/grade.py"          # root-only (0700); the agent cannot read it

# The agent's world. shell_uid=1000 == `ubuntu`: the privilege wall this task depends on.
# TMOUT: a bash session left waiting on stdin exits rather than wedging the episode.
# Belt and braces alongside the setuid strip in the Dockerfile.
ws = env.workspace("/workspace/target", shell_uid=1000, network=False,
                   env={"TMOUT": "600"})


def _grade() -> float:
    """Run the shipped Harbor grader and return its reward. Root-side."""
    r = subprocess.run(["python3", GRADER], capture_output=True, text=True, timeout=1200)
    for line in r.stdout.splitlines():
        if line.startswith("Final score:"):
            try:
                return float(line.split(":", 1)[1].strip())
            except ValueError:
                break
    return 0.0


@env.template(id="district-rollout")
async def district_rollout():
    """Hand over the incident, then grade the trajectory the environment recorded."""
    _ = yield _instruction()      # the agent's closing text is deliberately not used
    yield _grade()


async def test():
    """Local smoke: uv run python env.py"""
    from hud import LocalRuntime
    from hud.agents.claude import ClaudeAgent

    job = await district_rollout().run(ClaudeAgent(), runtime=LocalRuntime(__file__))
    print("reward:", job.reward)


if __name__ == "__main__":
    import asyncio
    asyncio.run(test())
