"""Agent chain."""

from __future__ import annotations

import sys
from enum import Enum
from functools import reduce
from typing import Any, Callable, Literal

from loguru import logger
from pydantic import BaseModel

from language_models.agent.agent import Agent, Step, StepName
from language_models.tools.tool import Tool

logger.remove()
logger.add(sys.stderr, format="{message}", level="INFO")


class WorkflowStepName(str, Enum):
    TRANSFORMATION = "transformation"
    FUNCTION = "function"
    LLM = "llm"


class WorkflowStep(BaseModel):
    name: WorkflowStepName
    steps: list[Step]

    class Config:
        use_enum_values = True


class WorkflowStepOutput(BaseModel):
    """Class that represents the output of a step."""

    inputs: dict[str, Any]
    output: (
        str
        | int
        | float
        | dict[str, Any]
        | BaseModel
        | list[str]
        | list[int]
        | list[float]
        | list[dict[str, Any]]
        | list[BaseModel]
        | None
    )
    step: WorkflowStep


class WorkflowFunctionStep(BaseModel):
    """Class that implements a function step.

    Attributes:
        name: The name of the step.
        inputs: The Pydantic model that represents the input arguments.
        function: The function that will be invoked when calling this step.
    """

    name: str
    inputs: type[BaseModel]
    function: Callable[[Any], Any]

    def invoke(self, inputs: dict[str, Any], verbose: bool) -> WorkflowStepOutput:
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #2D72D2>Use Function</fg #2D72D2></b>: {self.name}")

        inputs = {key: value for key, value in inputs.items() if key in self.inputs.model_fields}
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Inputs</fg #EC9A3C></b>: {inputs}")

        output = self.function(**inputs)
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Output</fg #EC9A3C></b>: {output}")

        return WorkflowStepOutput(
            inputs=inputs,
            output=output,
            step=WorkflowStep(
                name=WorkflowStepName.FUNCTION,
                steps=[
                    Step(name=StepName.INPUTS, content=inputs),
                    Step(name=StepName.OUTPUT, content=output),
                ],
            ),
        )


class WorkflowLLMStep(BaseModel):
    """Class that implements an agent step.

    Attributes:
        name: The name of the step.
        agent: The agent that will be invoked when calling this step.
    """

    name: str
    agent: Agent

    def invoke(self, inputs: dict[str, Any], verbose: bool) -> WorkflowStepOutput:
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #2D72D2>Use LLM</fg #2D72D2></b>: {self.name}")

        inputs = {variable: inputs.get(variable) for variable in self.agent.prompt_variables}
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Inputs</fg #EC9A3C></b>: {inputs}")

        prompt = self.agent.prompt.format(**inputs)
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #738091>Prompt</fg #738091></b>: {prompt}")

        output = self.agent.invoke(inputs)
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Output</fg #EC9A3C></b>: {output.final_answer}")

        return WorkflowStepOutput(
            inputs=inputs,
            output=output.final_answer,
            step=WorkflowStep(name=WorkflowStepName.LLM, steps=output.steps),
        )


class WorkflowTransformationStep(BaseModel):
    """Class that implements a transformation step.

    Attributes:
        name: The name of the step.
        input_field: The name of the field values to transform.
        transformation: The transformation to apply (can be map, filter, reduce).
        function: The function used for the transformation.
    """

    name: str
    input_field: str
    transformation: Literal["map", "filter", "reduce"]
    function: Callable[[Any], Any]

    def invoke(self, inputs: dict[str, Any], verbose: bool) -> WorkflowStepOutput:
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #2D72D2>Use Transformation</fg #2D72D2></b>: {self.name}")

        values = inputs[self.input_field]
        inputs = {self.input_field: values}
        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Inputs</fg #EC9A3C></b>: {inputs}")

        if self.transformation == "map":
            transformed_values = map(self.function, values)
            output = list(transformed_values) if isinstance(values, list) else dict(transformed_values)
        elif self.transformation == "filter":
            transformed_values = filter(self.function, values)
            output = list(transformed_values) if isinstance(values, list) else dict(transformed_values)
        else:
            output = reduce(self.function, values)

        if verbose:
            logger.opt(colors=True).info(f"<b><fg #EC9A3C>Output</fg #EC9A3C></b>: {output}")

        return WorkflowStepOutput(
            inputs=inputs,
            output=output,
            step=WorkflowStep(
                name=WorkflowStepName.TRANSFORMATION,
                steps=[
                    Step(name=StepName.INPUTS, content=inputs),
                    Step(name=StepName.OUTPUT, content=output),
                ],
            ),
        )


class WorkflowStateManager(BaseModel):
    """Class that implements a state manager."""

    state: dict[str, Any]

    def update(self, name: str, step: WorkflowStepOutput) -> None:
        """Updates the state values."""
        self.state[name] = step.output


class WorkflowOutput(BaseModel):
    """Class that represents the workflow output."""

    inputs: dict[str, Any]
    output: (
        str
        | int
        | float
        | dict[str, Any]
        | BaseModel
        | list[str]
        | list[int]
        | list[float]
        | list[dict[str, Any]]
        | list[BaseModel]
        | None
    )
    steps: list[WorkflowStep]


class Workflow(BaseModel):
    """Class that implements a workflow.

    Attributes:
        name: The name of the workflow.
        description: The description of what the workflow does.
        steps: The steps of the workflow.
        inputs: The workflow inputs.
        output: The name of the step value to output.
    """

    name: str
    description: str
    steps: list[WorkflowLLMStep | WorkflowFunctionStep | WorkflowTransformationStep]
    inputs: type[BaseModel]
    output: str
    verbose: bool = True

    def invoke(self, inputs: dict[str, Any]) -> WorkflowOutput:
        """Runs the workflow."""
        _ = self.inputs.model_validate(inputs)
        state_manager = WorkflowStateManager(state=inputs)
        workflow_steps = []
        for step in self.steps:
            output = step.invoke(state_manager.state, self.verbose)
            state_manager.update(step.name, output)
            workflow_steps.append(output.step)

        output = state_manager.state.get(self.output)
        if self.verbose:
            logger.opt(colors=True).success(f"<b><fg #32A467>Workflow Output</fg #32A467></b>: {output}")

        return WorkflowOutput(inputs=inputs, output=output, steps=workflow_steps)

    def as_tool(self) -> Tool:
        """Converts the workflow into an LLM tool."""
        return Tool(
            function=lambda **inputs: self.invoke(inputs).output,
            name=self.name,
            description=self.description,
            args_schema=self.inputs,
        )
