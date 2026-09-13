"""Record model responses at the PydanticAI model boundary; replay them so the same runs need no key (tier 3)."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, ModelSettings, infer_model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel

from data_agents.agents import analysis, router, visualization

AGENTS = {"analysis": analysis.agent, "visualization": visualization.agent, "router": router.agent}
Responses = TypeAdapter(list[ModelResponse])


class Recorder(WrapperModel):
    """The framework's hook for sitting between agent and model: passes every request through and keeps the response."""

    def __init__(self, wrapped, tape: list[ModelResponse]):
        super().__init__(wrapped)
        self.tape = tape

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None, parameters: ModelRequestParameters) -> ModelResponse:
        response = await super().request(messages, model_settings, parameters)
        self.tape.append(response)
        return response


def player(tape: list[ModelResponse]) -> FunctionModel:
    queue = list(tape)

    def next_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not queue:
            raise RuntimeError("tape exhausted: the run made more model requests than were recorded")
        return queue.pop(0)

    return FunctionModel(next_response, model_name="replay")


@contextmanager
def recording(path: Path, agents: dict | None = None):
    """Run live; write every model response of each agent to one JSON tape when the block ends."""
    agents = agents or AGENTS
    tapes: dict[str, list[ModelResponse]] = {name: [] for name in agents}
    with ExitStack() as stack:
        for name, agent in agents.items():
            stack.enter_context(agent.override(model=Recorder(infer_model(agent.model), tapes[name])))
        yield
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({name: json.loads(Responses.dump_json(tape)) for name, tape in tapes.items()}, indent=1))


@contextmanager
def replaying(path: Path, agents: dict | None = None):
    tapes = json.loads(path.read_text())
    with ExitStack() as stack:
        for name, agent in (agents or AGENTS).items():  # a tape from before an agent existed replays it as never called
            stack.enter_context(agent.override(model=player(Responses.validate_python(tapes.get(name, [])))))
        yield
