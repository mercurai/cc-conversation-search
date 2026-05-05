from conversation_search.core.session_miner import extract_attachment_error, normalize_exit_code


def test_normalize_exit_code():
    assert normalize_exit_code(0) == 0
    assert normalize_exit_code("200") == 200
    assert normalize_exit_code("") is None
    assert normalize_exit_code("abc") is None


def test_hook_success_is_not_error():
    obj = {
        "attachment": {
            "type": "hook_success",
            "hookName": "PreToolUse:Bash",
            "exitCode": 200,
            "command": "http://127.0.0.1:29063/notify",
        }
    }
    assert extract_attachment_error(obj) is None


def test_hook_non_blocking_error_is_error():
    obj = {
        "timestamp": "2026-05-01T20:03:53.120Z",
        "attachment": {
            "type": "hook_non_blocking_error",
            "hookName": "SessionStart:startup",
            "stderr": "cannot execute binary file",
            "exitCode": 126,
            "command": "sh ${CLAUDE_PLUGIN_ROOT}/scripts/check-peer.sh",
        },
    }
    result = extract_attachment_error(obj)
    assert result is not None
    assert result["hook"] == "SessionStart:startup"
    assert result["exit_code"] == 126
