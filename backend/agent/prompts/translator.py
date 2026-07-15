"""
Translator prompt for Urdu to English translation.

This prompt is used to translate Urdu text to English for the agent.
"""

URDU_TRANSLATOR_SYSTEM_MESSAGE = """Output only Urdu-language text in Urdu script."""


def build_urdu_translator_user_prompt(answer_text: str) -> str:
    """
    Build the user prompt for Urdu translation.

    This includes the translation instructions and the text to translate.
    """
    return f"""Convert this assistant answer into natural Pakistani Urdu written only in Urdu script.
Do not use Hindi Devanagari, Roman Urdu, Arabic-language wording, French, or English prose.
Preserve markdown structure where possible. Output only the Urdu answer text.

{answer_text}"""
