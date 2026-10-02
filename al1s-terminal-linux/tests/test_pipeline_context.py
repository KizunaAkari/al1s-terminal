from concurrent.futures import ThreadPoolExecutor

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


def test_reused_compiler_keeps_completed_graphs_independent(tmp_path):
    compiler = MaaPipelineCompiler(tmp_path)
    first = compiler.compile({"steps": [{"action": "wait", "seconds": 1}]})
    before = repr(first.pipeline)
    second = compiler.compile({"steps": [{"action": "tap", "x": 1, "y": 2}]})
    assert repr(first.pipeline) == before
    assert first.pipeline is not second.pipeline
    assert first.step_nodes is not second.step_nodes


def test_one_compiler_can_build_different_scripts_concurrently(tmp_path):
    compiler = MaaPipelineCompiler(tmp_path)
    scripts = [{"steps": [{"action": "wait", "seconds": i}]} for i in range(1, 9)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        compiled = list(pool.map(compiler.compile, scripts))
    assert len({task.script_hash for task in compiled}) == len(scripts)
    for task in compiled:
        assert all(name.startswith(task.entry.removesuffix("Root")) for name in task.pipeline)
        assert task.entry in task.pipeline
        assert len(task.step_nodes) == 1
