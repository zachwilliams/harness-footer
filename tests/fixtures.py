def claude_payload(tokens=173_000, session='one'):
    """A Claude statusLine payload as documented for Claude Code."""
    return {
        'session_id': session,
        'workspace': {'current_dir': '/tmp/project'},
        'model': {'display_name': 'Opus'},
        'context_window': {
            'context_window_size': 1_000_000,
            'current_usage': {
                'input_tokens': 3000,
                'cache_read_input_tokens': tokens - 3000,
                'cache_creation_input_tokens': 0,
                'output_tokens': 5000,
            },
        },
        'rate_limits': {
            'five_hour': {'used_percentage': 42},
            'seven_day': {'used_percentage': 57},
        },
    }


def isolated_environment(directory):
    """Environment variables that keep caches and settings in directory."""
    return {
        'XDG_CACHE_HOME': f'{directory}/cache',
        'XDG_CONFIG_HOME': f'{directory}/config',
    }
