
def identify_intent(
    intents: Mapping[str, Sequence[str]],
    intent_map: Mapping[str, Callable[[str], Result]],
    message: str,
) -> IntentDispatchResult[Result]:
    if not intents:
        raise ValueError("intents must contain at least one intent")
    if not message.strip():
        raise ValueError("message must not be empty")

    unexpected_handlers = set(intent_map) - set(intents)
    if unexpected_handlers:
        raise ValueError(
            f"handlers have no matching intent: {', '.join(sorted(unexpected_handlers))}"
        )

    intent_descriptions = "\n".join(
        f"- {intent}: {', '.join(phrases)}" for intent, phrases in intents.items()
    )
    prompt = (
        "Classify the user's message into exactly one of the supplied intents "
        "and extract any named entities explicitly mentioned in it. Use the "
        "example phrases as guidance, not as an exhaustive list. Return entity "
        "strings exactly as written, in order, without duplicates. Do not "
        "include generic concepts or inferred information; use an empty list "
        "when there are no named entities.\n\n"
        f"Intents:\n{intent_descriptions}\n\n"
        f"Message:\n{message}"
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": list(intents),
            },
            "entities": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["intent", "entities"],
        "additionalProperties": False,
    }
    result: dict[str, Any] = json.loads(
        interact_with_openai(
            prompt,
            schema=schema,
            system_prompt=(
                "You classify user messages by intent and extract named "
                "entities. Follow the provided schema exactly."
            ),
        )
    )
    intent = result.get("intent")
    if not isinstance(intent, str) or intent not in intents:
        raise ValueError(f"classifier returned an unknown intent: {intent!r}")
    entities = result.get("entities")
    if not isinstance(entities, list) or not all(
        isinstance(entity, str) for entity in entities
    ):
        raise ValueError(f"classifier returned invalid entities: {entities!r}")
    if intent in intent_map:
        return IntentDispatchResult(
            intent=intent,
            entities=entities,
            handler_result=intent_map[intent](message),
        )
    
    return IntentDispatchResult(
        intent=intent,
        entities=entities,
        handler_result=None,
    )
