"""Registry-3 retrieval with the frozen v5 intent and completion contract."""

import json

from app.diagnosis import planner_v5


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v5.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context["prompt_version"] = "diagnosis-v6"
    return [
        ("system", planner_v5.SYSTEM_PROMPT_V5),
        ("human", json.dumps(context, ensure_ascii=False)),
    ]
