from tstriage._progress import SubprocessProgress


def test_feed_logs_child_log_lines(caplog):
    """The child's own log lines show up as they arrive, not only when it fails."""
    progress = SubprocessProgress()
    with caplog.at_level('INFO'):
        progress.feed('[12:00:00] WARNING  Clip (1.0, 2.0) failed to decode, retrying 2.0s earlier')
    assert 'retrying 2.0s earlier' in caplog.text


def test_feed_reads_progress_lines_instead_of_logging_them(caplog):
    progress = SubprocessProgress()
    with caplog.at_level('INFO'):
        progress.feed('PROGRESS:{"task": "t", "total": 10, "n": 1, "desc": "Unit"}')
    assert 'PROGRESS:' not in caplog.text
    assert 't' in progress._tasks
