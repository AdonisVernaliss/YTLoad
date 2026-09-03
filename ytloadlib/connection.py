from __future__ import annotations


YOUTUBE_CLIENTS = ('auto', 'default', 'web_safari')


def validate_connection(user_agent: str | None, youtube_client: str) -> None:
    if not isinstance(youtube_client, str) or youtube_client not in YOUTUBE_CLIENTS:
        raise ValueError('Choose Automatic, Default or Default + Safari for the YouTube player.')
    if user_agent is not None:
        if not isinstance(user_agent, str) or not user_agent.strip() or len(user_agent) > 1024:
            raise ValueError('User-Agent must contain between 1 and 1024 printable characters.')
        if user_agent.startswith('-') or any(ord(character) < 32 or ord(character) > 126 for character in user_agent):
            raise ValueError('User-Agent must be a single printable ASCII header value.')


def connection_options(browser: str | None = None, user_agent: str | None = None,
                       youtube_client: str = 'auto', safari_fallback: bool = False) -> list[str]:
    validate_connection(user_agent, youtube_client)
    options = []
    if browser:
        options.extend(['--cookies-from-browser', browser])
    if user_agent:
        options.extend(['--user-agent', user_agent])
    if youtube_client == 'web_safari' or (youtube_client == 'auto' and safari_fallback):
        options.extend(['--extractor-args', 'youtube:player_client=default,web_safari'])
    elif youtube_client == 'default':
        options.extend(['--extractor-args', 'youtube:player_client=default'])
    return options
