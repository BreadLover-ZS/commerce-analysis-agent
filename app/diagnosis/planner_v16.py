"""v16 uses the frozen v15 obligations and explicitly identifies the new protocol."""

import json

from app.diagnosis.planner_v15 import prompt as v15_prompt


def prompt(task, bundle, scope, steps, evidence, catalog):
    messages = v15_prompt(task, bundle, scope, steps, evidence, catalog)
    context = json.loads(messages[1][1])
    context["prompt_version"] = "diagnosis-v16"
    return [("system", messages[0][1]), ("human", json.dumps(context, ensure_ascii=False))]
