"""OpsExecutor: bare type + constructor (ac575525). No interface methods yet."""
from skos.autopilot.ops_executor import OpsExecutor, new_ops_executor


def test_new_ops_executor_returns_ops_executor_instance():
    ex = new_ops_executor()
    assert isinstance(ex, OpsExecutor)


def test_ops_executor_is_directly_constructible():
    assert isinstance(OpsExecutor(), OpsExecutor)
