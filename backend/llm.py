"""Model client. Imported lazily so mock mode works with no SDK and no API key."""


def get_client():
    from anthropic import Anthropic  # reads ANTHROPIC_API_KEY from the environment
    return Anthropic()
